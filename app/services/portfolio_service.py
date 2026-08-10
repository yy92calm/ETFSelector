"""
实盘模拟服务
基于配置组合+再平衡逻辑，每日检查并执行策略
"""

import logging
from datetime import date
from typing import Optional, List, Dict

from sqlalchemy.orm import Session

from app.models.etf import ETFQuotation
from app.models.strategy import Strategy
from app.models.portfolio import PortfolioSnapshot, TradeRecord, Holding, HoldingSnapshot
from app.services.strategy_service import get_strategy_service
from app.strategies.base import PortfolioContext, compute_adjustment

logger = logging.getLogger(__name__)


class PortfolioService:

    def _activate_pending_allocation(self, strategy: Strategy, trade_date: date):
        """待生效配置（t+1）：提交日之后的交易日才激活"""
        if strategy.pending_allocation and strategy.pending_set_date and trade_date > strategy.pending_set_date:
            logger.info(
                f"策略 {strategy.id} 待生效配置于 {trade_date} 激活: "
                f"{strategy.allocation_config} -> {strategy.pending_allocation}"
            )
            strategy.allocation_config = strategy.pending_allocation
            strategy.pending_allocation = None
            strategy.pending_set_date = None

    def run_strategy_for_date(self, strategy: Strategy, trade_date: date, db: Session):
        """为指定策略在指定日期执行再平衡检查和交易

        按实际日期如实记录：当日快照反映当日实际持仓与当日行情。
        调仓采用 t+1 生效：提交日当日仍按旧持仓记录，下一交易日才执行新配置。
        """
        # 检查是否已处理过该日期
        existing = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.strategy_id == strategy.id,
                    PortfolioSnapshot.trade_date == trade_date)
            .first()
        )
        if existing:
            return

        # t+1：激活提交日在 trade_date 之前的待生效配置
        self._activate_pending_allocation(strategy, trade_date)

        etf_codes = strategy.get_etf_codes()
        if not etf_codes:
            return

        # 获取上一个快照来确定当前持仓和现金
        prev_snapshot = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.strategy_id == strategy.id,
                    PortfolioSnapshot.trade_date < trade_date)
            .order_by(PortfolioSnapshot.trade_date.desc())
            .first()
        )

        if prev_snapshot:
            cash = prev_snapshot.cash
        else:
            cash = float(strategy.initial_capital)
            # 首次建仓，记录持仓起始日期
            if not strategy.holding_start_date:
                strategy.holding_start_date = trade_date
                db.add(strategy)

        # 获取当前持仓
        holdings_records = db.query(Holding).filter(
            Holding.strategy_id == strategy.id
        ).all()
        holdings: Dict[str, int] = {h.etf_code: h.quantity for h in holdings_records}

        # 获取当日各ETF价格：配置内ETF + 全部实际持仓（遗留持仓也要估值和卖出）
        current_prices = {}
        for code in set(etf_codes) | set(holdings.keys()):
            quote = (
                db.query(ETFQuotation)
                .filter(ETFQuotation.etf_code == code,
                        ETFQuotation.trade_date <= trade_date)
                .order_by(ETFQuotation.trade_date.desc())
                .first()
            )
            if quote and quote.close_price > 0:
                current_prices[code] = quote.close_price

        # 无行情的持仓用持仓价兜底，保证市值完整、可卖出
        for h in holdings_records:
            if h.quantity > 0 and h.etf_code not in current_prices and h.current_price and h.current_price > 0:
                current_prices[h.etf_code] = h.current_price

        if not current_prices:
            return

        # 查询截至 trade_date 的历史交易日（用于判断月末/季末时间触发）
        history_dates = [
            r[0] for r in db.query(ETFQuotation.trade_date)
            .filter(
                ETFQuotation.etf_code == etf_codes[0],
                ETFQuotation.trade_date <= trade_date,
            )
            .distinct()
            .order_by(ETFQuotation.trade_date.desc())
            .limit(120)
            .all()
        ]
        history_dates.reverse()  # 升序，trade_date 为最后一个

        # 计算当前市值和总资产
        market_value = 0
        for code, qty in holdings.items():
            if qty > 0 and code in current_prices:
                market_value += qty * current_prices[code]

        total_asset = cash + market_value

        # 构建上下文
        ctx = PortfolioContext(
            current_date=trade_date,
            total_asset=total_asset,
            holdings=holdings.copy(),
            current_prices=current_prices,
            allocation_config=strategy.allocation_config,
            rebalance_threshold=strategy.rebalance_threshold,
            history_dates=history_dates,
        )

        # 获取策略实例并检查是否需要再平衡
        svc = get_strategy_service()
        strategy_instance = svc.get_strategy_instance(strategy)

        if strategy_instance.check_rebalance(ctx):
            signals = strategy_instance.generate_rebalance_signals(ctx)

            for signal in signals:
                for adj in signal.adjustments:
                    etf_code = adj['etf_code']
                    action = adj['action']
                    amount = adj['amount']
                    price = current_prices.get(etf_code, 0)

                    result = compute_adjustment(
                        action, amount, price,
                        holdings.get(etf_code, 0), cash
                    )
                    if not result:
                        continue

                    holdings[etf_code] = result["new_qty"]
                    cash += result["cash_delta"]

                    db.add(TradeRecord(
                        strategy_id=strategy.id,
                        trade_date=trade_date,
                        etf_code=etf_code,
                        direction=result["direction"],
                        price=price,
                        quantity=result["quantity"],
                        amount=round(result["actual_amount"], 2),
                        reason=signal.reason,
                    ))

        # 重新计算市值
        market_value = 0
        for code, qty in holdings.items():
            if qty > 0 and code in current_prices:
                market_value += qty * current_prices[code]

        total_asset = cash + market_value
        initial = float(strategy.initial_capital)
        profit = total_asset - initial
        profit_pct = profit / initial * 100 if initial > 0 else 0

        # 更新持仓记录（配置内ETF + 遗留持仓，卖空的删除）
        for code in set(etf_codes) | set(holdings.keys()):
            qty = holdings.get(code, 0)
            price = current_prices.get(code, 0)

            h = db.query(Holding).filter(
                Holding.strategy_id == strategy.id,
                Holding.etf_code == code,
            ).first()

            if qty > 0 and price > 0:
                if h:
                    h.quantity = qty
                    h.current_price = price
                    h.market_value = qty * price
                else:
                    db.add(Holding(
                        strategy_id=strategy.id,
                        etf_code=code,
                        quantity=qty,
                        avg_cost=price,
                        current_price=price,
                        market_value=qty * price,
                    ))
            elif h and qty <= 0:
                db.delete(h)

        db.add(PortfolioSnapshot(
            strategy_id=strategy.id,
            trade_date=trade_date,
            total_asset=round(total_asset, 2),
            cash=round(cash, 2),
            market_value=round(market_value, 2),
            profit=round(profit, 2),
            profit_pct=round(profit_pct, 4),
        ))

        # 写入当日持仓快照（按实际日期保留历史持仓记录）
        db.query(HoldingSnapshot).filter(
            HoldingSnapshot.strategy_id == strategy.id,
            HoldingSnapshot.trade_date == trade_date,
        ).delete()
        for code, qty in holdings.items():
            if qty <= 0:
                continue
            price = current_prices.get(code, 0)
            db.add(HoldingSnapshot(
                strategy_id=strategy.id,
                trade_date=trade_date,
                etf_code=code,
                quantity=qty,
                price=price,
                market_value=round(qty * price, 2),
            ))

        db.commit()
        logger.info(f"策略 {strategy.id} 在 {trade_date} 执行完成, 总资产={total_asset:.2f}")

    def run_all_active_strategies(self, db: Session):
        """执行所有活跃策略的当日信号"""
        today = date.today()
        strategies = db.query(Strategy).filter(Strategy.status == "active").all()
        for s in strategies:
            try:
                self.run_strategy_for_date(s, today, db)
            except Exception as e:
                logger.error(f"执行策略 {s.id} 失败: {e}")

    def catch_up_strategy(self, strategy: Strategy, db: Session):
        """补跑策略：从策略创建日到今天，逐日执行"""
        start = strategy.created_at.date()
        today = date.today()
        etf_codes = strategy.get_etf_codes()

        if not etf_codes:
            return

        # 找到所有交易日
        trade_dates = (
            db.query(ETFQuotation.trade_date)
            .filter(
                ETFQuotation.etf_code == etf_codes[0],
                ETFQuotation.trade_date >= start,
                ETFQuotation.trade_date <= today,
            )
            .distinct()
            .order_by(ETFQuotation.trade_date.asc())
            .all()
        )

        for (td,) in trade_dates:
            try:
                self.run_strategy_for_date(strategy, td, db)
            except Exception as e:
                logger.error(f"补跑策略 {strategy.id} 日期 {td} 失败: {e}")

    def get_portfolio_history(self, strategy_id: int, db: Session, start_date: date = None) -> List[PortfolioSnapshot]:
        query = db.query(PortfolioSnapshot).filter(PortfolioSnapshot.strategy_id == strategy_id)
        if start_date:
            query = query.filter(PortfolioSnapshot.trade_date >= start_date)
        return query.order_by(PortfolioSnapshot.trade_date.asc()).all()

    def get_holdings(self, strategy_id: int, db: Session) -> List[Holding]:
        return (
            db.query(Holding)
            .filter(Holding.strategy_id == strategy_id)
            .all()
        )

    def get_trades(self, strategy_id: int, db: Session) -> List[TradeRecord]:
        return (
            db.query(TradeRecord)
            .filter(TradeRecord.strategy_id == strategy_id)
            .order_by(TradeRecord.trade_date.desc())
            .all()
        )


_service: Optional[PortfolioService] = None


def get_portfolio_service() -> PortfolioService:
    global _service
    if _service is None:
        _service = PortfolioService()
    return _service
