"""
实盘模拟服务
从策略创建日起，使用每日行情执行策略，
以「全部成功买入 / 全部成功卖出」方式处理信号，每日记录组合资产
"""

import logging
from datetime import date, timedelta
from typing import Optional, List, Dict

import pandas as pd
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.etf import ETFQuotation
from app.models.strategy import Strategy
from app.models.portfolio import PortfolioSnapshot, TradeRecord, Holding
from app.services.strategy_service import get_strategy_service
from app.strategies.base import StrategyContext

logger = logging.getLogger(__name__)


class PortfolioService:

    def run_strategy_for_date(self, strategy: Strategy, trade_date: date, db: Session):
        """
        为指定策略在指定日期执行一次信号检测和交易
        """
        svc = get_strategy_service()
        strategy_instance = svc.get_strategy_instance(strategy)
        etf_codes = strategy.etf_codes or []

        if not etf_codes:
            return

        # 检查是否已处理过该日期
        existing = (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.strategy_id == strategy.id,
                    PortfolioSnapshot.trade_date == trade_date)
            .first()
        )
        if existing:
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

        # 获取当前持仓
        holdings_records = (
            db.query(Holding)
            .filter(Holding.strategy_id == strategy.id)
            .all()
        )
        holdings: Dict[str, int] = {h.etf_code: h.quantity for h in holdings_records}
        avg_costs: Dict[str, float] = {h.etf_code: h.avg_cost for h in holdings_records}

        # 对每个ETF生成并执行信号
        for code in etf_codes:
            # 取历史数据（含当日）
            rows = (
                db.query(ETFQuotation)
                .filter(ETFQuotation.etf_code == code,
                        ETFQuotation.trade_date <= trade_date)
                .order_by(ETFQuotation.trade_date.asc())
                .all()
            )
            if not rows:
                continue

            hist = pd.DataFrame([{
                "date": r.trade_date,
                "open": r.open_price,
                "close": r.close_price,
                "high": r.high_price,
                "low": r.low_price,
                "volume": r.volume,
                "amount": r.amount,
                "change_pct": r.change_pct,
            } for r in rows])

            # 检查当日是否有行情
            today_data = hist[hist["date"] == trade_date]
            if today_data.empty:
                continue

            ctx = StrategyContext(
                etf_code=code,
                history=hist,
                current_date=trade_date,
                holdings=holdings.copy(),
                cash=cash,
                params=strategy_instance.params,
            )

            signals = strategy_instance.generate_signals(ctx)

            for sig in signals:
                current_price = hist.iloc[-1]["close"]
                if current_price <= 0:
                    continue

                if sig.direction == "buy" and cash > 0:
                    max_qty = int(cash / current_price / 100) * 100
                    if max_qty > 0:
                        trade_amount = max_qty * current_price
                        old_qty = holdings.get(code, 0)
                        old_cost = avg_costs.get(code, 0)
                        if old_qty + max_qty > 0:
                            avg_costs[code] = (old_cost * old_qty + trade_amount) / (old_qty + max_qty)
                        holdings[code] = old_qty + max_qty
                        cash -= trade_amount

                        db.add(TradeRecord(
                            strategy_id=strategy.id,
                            trade_date=trade_date,
                            etf_code=code,
                            direction="buy",
                            price=current_price,
                            quantity=max_qty,
                            amount=trade_amount,
                            reason=sig.reason,
                        ))

                elif sig.direction == "sell" and holdings.get(code, 0) > 0:
                    qty = holdings[code]
                    trade_amount = qty * current_price
                    cash += trade_amount
                    holdings[code] = 0

                    db.add(TradeRecord(
                        strategy_id=strategy.id,
                        trade_date=trade_date,
                        etf_code=code,
                        direction="sell",
                        price=current_price,
                        quantity=qty,
                        amount=trade_amount,
                        reason=sig.reason,
                    ))

        # 计算当日市值
        market_value = 0
        for code, qty in holdings.items():
            if qty <= 0:
                continue
            latest = (
                db.query(ETFQuotation)
                .filter(ETFQuotation.etf_code == code,
                        ETFQuotation.trade_date <= trade_date)
                .order_by(ETFQuotation.trade_date.desc())
                .first()
            )
            if latest:
                price = latest.close_price
                market_value += qty * price

                # 更新持仓记录
                h = db.query(Holding).filter(
                    Holding.strategy_id == strategy.id,
                    Holding.etf_code == code,
                ).first()
                if h:
                    h.quantity = qty
                    h.current_price = price
                    h.market_value = qty * price
                    h.avg_cost = avg_costs.get(code, 0)
                else:
                    db.add(Holding(
                        strategy_id=strategy.id,
                        etf_code=code,
                        quantity=qty,
                        avg_cost=avg_costs.get(code, 0),
                        current_price=price,
                        market_value=qty * price,
                    ))

        # 清除已清仓的持仓
        for code, qty in holdings.items():
            if qty <= 0:
                h = db.query(Holding).filter(
                    Holding.strategy_id == strategy.id,
                    Holding.etf_code == code,
                ).first()
                if h:
                    db.delete(h)

        total_asset = cash + market_value
        initial = float(strategy.initial_capital)
        profit = total_asset - initial
        profit_pct = profit / initial * 100

        db.add(PortfolioSnapshot(
            strategy_id=strategy.id,
            trade_date=trade_date,
            total_asset=round(total_asset, 2),
            cash=round(cash, 2),
            market_value=round(market_value, 2),
            profit=round(profit, 2),
            profit_pct=round(profit_pct, 4),
        ))

        db.commit()
        logger.info(f"策略 {strategy.id} 在 {trade_date} 执行完成, 总资产={total_asset:.2f}")

    def run_all_active_strategies(self, db: Session):
        """执行所有活跃策略的当日信号"""
        from datetime import date as d
        today = d.today()

        strategies = db.query(Strategy).filter(Strategy.status == "active").all()
        for s in strategies:
            try:
                self.run_strategy_for_date(s, today, db)
            except Exception as e:
                logger.error(f"执行策略 {s.id} 失败: {e}")

    def catch_up_strategy(self, strategy: Strategy, db: Session):
        """
        补跑策略：从策略创建日到今天，逐日执行
        用于策略首次创建后的补全
        """
        from datetime import date as d

        start = strategy.created_at.date()
        today = d.today()
        etf_codes = strategy.etf_codes or []

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

    def get_portfolio_history(self, strategy_id: int, db: Session) -> List[PortfolioSnapshot]:
        return (
            db.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.strategy_id == strategy_id)
            .order_by(PortfolioSnapshot.trade_date.asc())
            .all()
        )

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
