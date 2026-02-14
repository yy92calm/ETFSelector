"""
回测引擎
用历史行情数据对策略进行回测，输出收益曲线和统计指标
"""

import logging
from datetime import date, timedelta
from typing import List, Optional, Dict

import pandas as pd
import numpy as np
from sqlalchemy.orm import Session

from app.models.etf import ETFQuotation
from app.models.strategy import Strategy
from app.strategies.base import BaseStrategy, StrategyContext, Signal
from app.services.strategy_service import get_strategy_service

logger = logging.getLogger(__name__)


class BacktestEngine:
    """回测引擎：模拟历史交易，输出净值曲线和统计"""

    def run(self, strategy: Strategy, start_date: date, end_date: date,
            initial_capital: float, db: Session) -> dict:
        """
        执行回测
        返回:
        {
            strategy_id, start_date, end_date, initial_capital, final_asset,
            total_return_pct, max_drawdown_pct, sharpe_ratio, trade_count, win_rate,
            daily_data: [{date, total_asset, cash, market_value, profit_pct}],
            trades: [{date, etf_code, direction, price, quantity, amount, reason}],
        }
        """
        svc = get_strategy_service()
        strategy_instance = svc.get_strategy_instance(strategy)
        etf_codes = strategy.etf_codes or []

        if not etf_codes:
            raise ValueError("策略未绑定ETF代码")

        # 加载历史数据
        all_data = {}
        for code in etf_codes:
            rows = (
                db.query(ETFQuotation)
                .filter(
                    ETFQuotation.etf_code == code,
                    ETFQuotation.trade_date >= start_date - timedelta(days=60),  # 多取60天用于指标计算
                    ETFQuotation.trade_date <= end_date,
                )
                .order_by(ETFQuotation.trade_date.asc())
                .all()
            )
            if rows:
                all_data[code] = pd.DataFrame([{
                    "date": r.trade_date,
                    "open": r.open_price,
                    "close": r.close_price,
                    "high": r.high_price,
                    "low": r.low_price,
                    "volume": r.volume,
                    "amount": r.amount,
                    "change_pct": r.change_pct,
                } for r in rows])

        if not all_data:
            raise ValueError("没有找到历史行情数据，请先获取数据")

        # 获取回测期内的交易日列表（取并集）
        trade_dates = set()
        for df in all_data.values():
            dates = df[df["date"] >= start_date]["date"].tolist()
            trade_dates.update(dates)
        trade_dates = sorted(trade_dates)

        if not trade_dates:
            raise ValueError("回测区间内无交易日")

        # 模拟交易
        cash = initial_capital
        holdings: Dict[str, int] = {}      # etf_code -> quantity
        avg_costs: Dict[str, float] = {}   # 平均成本
        daily_data = []
        trades = []

        for td in trade_dates:
            # 对每个ETF生成信号
            for code in etf_codes:
                if code not in all_data:
                    continue
                df = all_data[code]
                hist = df[df["date"] <= td].copy()
                if hist.empty:
                    continue

                ctx = StrategyContext(
                    etf_code=code,
                    history=hist,
                    current_date=td,
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
                        # 全部资金买入（按100股整数倍）
                        max_qty = int(cash / current_price / 100) * 100
                        if max_qty > 0:
                            amount = max_qty * current_price
                            old_qty = holdings.get(code, 0)
                            old_cost = avg_costs.get(code, 0)

                            # 更新平均成本
                            if old_qty + max_qty > 0:
                                avg_costs[code] = (old_cost * old_qty + amount) / (old_qty + max_qty)
                            holdings[code] = old_qty + max_qty
                            cash -= amount

                            trades.append({
                                "date": td.isoformat(),
                                "etf_code": code,
                                "direction": "buy",
                                "price": current_price,
                                "quantity": max_qty,
                                "amount": amount,
                                "reason": sig.reason,
                            })

                    elif sig.direction == "sell" and holdings.get(code, 0) > 0:
                        # 全部卖出
                        qty = holdings[code]
                        amount = qty * current_price
                        cash += amount
                        holdings[code] = 0

                        trades.append({
                            "date": td.isoformat(),
                            "etf_code": code,
                            "direction": "sell",
                            "price": current_price,
                            "quantity": qty,
                            "amount": amount,
                            "reason": sig.reason,
                        })

            # 计算当日总资产
            market_value = 0
            for code, qty in holdings.items():
                if qty > 0 and code in all_data:
                    df = all_data[code]
                    price_row = df[df["date"] <= td]
                    if not price_row.empty:
                        market_value += qty * price_row.iloc[-1]["close"]

            total_asset = cash + market_value
            profit_pct = (total_asset - initial_capital) / initial_capital * 100

            daily_data.append({
                "date": td.isoformat(),
                "total_asset": round(total_asset, 2),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "profit_pct": round(profit_pct, 4),
            })

        # 统计指标
        final_asset = daily_data[-1]["total_asset"] if daily_data else initial_capital
        total_return_pct = (final_asset - initial_capital) / initial_capital * 100

        # 最大回撤
        max_drawdown = self._calc_max_drawdown([d["total_asset"] for d in daily_data])

        # Sharpe比率（年化，假设无风险利率2%）
        sharpe = self._calc_sharpe([d["total_asset"] for d in daily_data])

        # 胜率
        win_rate = self._calc_win_rate(trades)

        return {
            "strategy_id": strategy.id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "initial_capital": initial_capital,
            "final_asset": round(final_asset, 2),
            "total_return_pct": round(total_return_pct, 4),
            "max_drawdown_pct": round(max_drawdown, 4),
            "sharpe_ratio": round(sharpe, 4) if sharpe else None,
            "trade_count": len(trades),
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "daily_data": daily_data,
            "trades": trades,
        }

    def _calc_max_drawdown(self, assets: List[float]) -> float:
        if not assets:
            return 0
        peak = assets[0]
        max_dd = 0
        for v in assets:
            if v > peak:
                peak = v
            dd = (peak - v) / peak * 100
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _calc_sharpe(self, assets: List[float], risk_free_rate: float = 0.02) -> Optional[float]:
        if len(assets) < 2:
            return None
        returns = []
        for i in range(1, len(assets)):
            returns.append((assets[i] - assets[i - 1]) / assets[i - 1])
        if not returns:
            return None
        arr = np.array(returns)
        mean_r = arr.mean()
        std_r = arr.std()
        if std_r == 0:
            return None
        daily_rf = risk_free_rate / 252
        sharpe = (mean_r - daily_rf) / std_r * np.sqrt(252)
        return float(sharpe)

    def _calc_win_rate(self, trades: List[dict]) -> Optional[float]:
        """计算盈利交易占比（基于买卖配对）"""
        # 简单按顺序配对
        buys = {}
        wins = 0
        total_pairs = 0
        for t in trades:
            code = t["etf_code"]
            if t["direction"] == "buy":
                buys[code] = t["price"]
            elif t["direction"] == "sell" and code in buys:
                if t["price"] > buys[code]:
                    wins += 1
                total_pairs += 1
                del buys[code]
        return (wins / total_pairs * 100) if total_pairs > 0 else None


_engine: Optional[BacktestEngine] = None


def get_backtest_engine() -> BacktestEngine:
    global _engine
    if _engine is None:
        _engine = BacktestEngine()
    return _engine
