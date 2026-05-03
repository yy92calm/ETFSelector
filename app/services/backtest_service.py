"""
ETF配置组合回测引擎
基于配置比例和再平衡逻辑的回测
"""

import logging
from datetime import date, timedelta
from typing import List, Optional, Dict

import pandas as pd
import numpy as np
from sqlalchemy.orm import Session

from app.models.etf import ETFQuotation
from app.models.strategy import Strategy
from app.strategies.portfolio_rebalance import PortfolioRebalanceStrategy
from app.strategies.base import PortfolioContext

logger = logging.getLogger(__name__)


class BacktestEngine:
    """配置组合回测引擎"""

    def run(self, strategy: Strategy, start_date: date, end_date: date,
            initial_capital: float, db: Session) -> dict:
        """
        执行配置组合回测
        
        流程：
        1. 第1天：按allocation_config比例买入
        2. 每季度末检查偏离度，触发再平衡
        3. 记录每日资产净值、再平衡记录
        
        返回:
        {
            strategy_id, start_date, end_date, initial_capital, final_asset,
            total_return_pct, max_drawdown_pct, sharpe_ratio, rebalance_count,
            daily_data: [{date, total_asset, cash, market_value, profit_pct}],
            rebalance_records: [{date, trigger_type, adjustments}],
        }
        """
        # 验证策略配置
        if not strategy.allocation_config:
            raise ValueError("策略未设置配置比例 (allocation_config)")
        
        etf_codes = strategy.get_etf_codes()
        if not etf_codes:
            raise ValueError("配置比例为空")
        
        # 创建策略实例
        strategy_instance = PortfolioRebalanceStrategy(
            allocation_config=strategy.allocation_config,
            rebalance_freq=strategy.rebalance_freq or "quarterly",
            rebalance_threshold=strategy.rebalance_threshold or 0.05
        )
        
        # 加载历史数据
        all_data = self._load_history_data(etf_codes, start_date, end_date, db)
        
        if not all_data:
            raise ValueError("没有找到历史行情数据")
        
        # 获取交易日列表（取交集）
        trade_dates = self._get_trade_dates(all_data, start_date, end_date)
        
        if not trade_dates:
            raise ValueError("回测区间内无交易日")
        
        # 模拟回测
        cash = initial_capital
        holdings: Dict[str, int] = {}      # etf_code -> quantity
        daily_data = []
        rebalance_records = []
        
        for td in trade_dates:
            # 获取当日价格
            current_prices = {}
            for code in etf_codes:
                if code in all_data:
                    price_df = all_data[code]
                    day_price = price_df[price_df['trade_date'] == td]
                    if not day_price.empty:
                        current_prices[code] = day_price.iloc[0]['close']
            
            if not current_prices:
                continue
            
            # 计算当前市值和总资产
            market_value = 0
            for code, qty in holdings.items():
                if qty > 0 and code in current_prices:
                    market_value += qty * current_prices[code]
            
            total_asset = cash + market_value
            
            # 构建上下文
            ctx = PortfolioContext(
                current_date=td,
                total_asset=total_asset,
                holdings=holdings.copy(),
                current_prices=current_prices,
                allocation_config=strategy.allocation_config,
                rebalance_threshold=strategy.rebalance_threshold,
                history_dates=trade_dates
            )
            
            # 检查是否需要再平衡
            if strategy_instance.check_rebalance(ctx):
                signals = strategy_instance.generate_rebalance_signals(ctx)
                
                # 执行再平衡
                for signal in signals:
                    rebalance_record = {
                        "date": td.isoformat(),
                        "trigger_type": signal.trigger_type.value,
                        "reason": signal.reason,
                        "adjustments": []
                    }
                    
                    for adj in signal.adjustments:
                        etf_code = adj['etf_code']
                        action = adj['action']
                        amount = adj['amount']
                        price = current_prices.get(etf_code, 0)
                        
                        if price <= 0:
                            continue
                        
                        if action == "buy" and cash >= amount:
                            # 买入（按100股整数倍）
                            quantity = int(amount / price / 100) * 100
                            if quantity > 0:
                                actual_amount = quantity * price
                                holdings[etf_code] = holdings.get(etf_code, 0) + quantity
                                cash -= actual_amount
                                
                                rebalance_record["adjustments"].append({
                                    "etf_code": etf_code,
                                    "action": "买入",
                                    "quantity": quantity,
                                    "price": price,
                                    "amount": round(actual_amount, 2)
                                })
                        
                        elif action == "sell" and holdings.get(etf_code, 0) > 0:
                            # 卖出（计算需要卖出的数量）
                            target_sell_amount = amount
                            current_qty = holdings[etf_code]
                            sell_qty = min(int(target_sell_amount / price / 100) * 100, current_qty)
                            
                            if sell_qty > 0:
                                actual_amount = sell_qty * price
                                holdings[etf_code] -= sell_qty
                                if holdings[etf_code] <= 0:
                                    holdings[etf_code] = 0
                                cash += actual_amount
                                
                                rebalance_record["adjustments"].append({
                                    "etf_code": etf_code,
                                    "action": "卖出",
                                    "quantity": sell_qty,
                                    "price": price,
                                    "amount": round(actual_amount, 2)
                                })
                    
                    if rebalance_record["adjustments"]:
                        rebalance_records.append(rebalance_record)
            
            # 重新计算市值（执行调整后）
            market_value = 0
            for code, qty in holdings.items():
                if qty > 0 and code in current_prices:
                    market_value += qty * current_prices[code]
            
            total_asset = cash + market_value
            profit_pct = (total_asset - initial_capital) / initial_capital * 100
            
            daily_data.append({
                "date": td.isoformat(),
                "total_asset": round(total_asset, 2),
                "cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "profit_pct": round(profit_pct, 4),
                "holdings": {code: qty for code, qty in holdings.items() if qty > 0}
            })
        
        # 统计指标
        final_asset = daily_data[-1]["total_asset"] if daily_data else initial_capital
        total_return_pct = (final_asset - initial_capital) / initial_capital * 100
        
        # 最大回撤
        max_drawdown = self._calc_max_drawdown([d["total_asset"] for d in daily_data])
        
        # Sharpe比率
        sharpe = self._calc_sharpe([d["total_asset"] for d in daily_data])
        
        return {
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "allocation_config": strategy.allocation_config,
            "rebalance_freq": strategy.rebalance_freq,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "initial_capital": initial_capital,
            "final_asset": round(final_asset, 2),
            "total_return_pct": round(total_return_pct, 4),
            "max_drawdown_pct": round(max_drawdown, 4),
            "sharpe_ratio": round(sharpe, 4) if sharpe else None,
            "rebalance_count": len(rebalance_records),
            "daily_data": daily_data,
            "rebalance_records": rebalance_records,
        }
    
    def _load_history_data(self, etf_codes: List[str], start_date: date, end_date: date, 
                           db: Session) -> Dict[str, pd.DataFrame]:
        """加载历史行情数据"""
        all_data = {}
        
        for code in etf_codes:
            rows = (
                db.query(ETFQuotation)
                .filter(
                    ETFQuotation.etf_code == code,
                    ETFQuotation.trade_date >= start_date - timedelta(days=10),
                    ETFQuotation.trade_date <= end_date,
                )
                .order_by(ETFQuotation.trade_date.asc())
                .all()
            )
            
            if rows:
                all_data[code] = pd.DataFrame([{
                    "trade_date": r.trade_date,
                    "open": r.open_price,
                    "close": r.close_price,
                    "high": r.high_price,
                    "low": r.low_price,
                    "volume": r.volume,
                    "amount": r.amount,
                    "change_pct": r.change_pct,
                } for r in rows])
        
        return all_data
    
    def _get_trade_dates(self, all_data: Dict[str, pd.DataFrame], start_date: date, 
                         end_date: date) -> List[date]:
        """获取交易日列表（取交集）"""
        trade_dates = None
        
        for code, df in all_data.items():
            dates = set(df[df['trade_date'] >= start_date]['trade_date'].tolist())
            
            if trade_dates is None:
                trade_dates = dates
            else:
                trade_dates = trade_dates.intersection(dates)
        
        if trade_dates:
            return sorted(list(trade_dates))
        
        return []
    
    def _calc_max_drawdown(self, assets: List[float]) -> float:
        """计算最大回撤"""
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
        """计算夏普比率"""
        if len(assets) < 2:
            return None
        
        returns = []
        for i in range(1, len(assets)):
            if assets[i - 1] > 0:
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


_engine: Optional[BacktestEngine] = None


def get_backtest_engine() -> BacktestEngine:
    global _engine
    if _engine is None:
        _engine = BacktestEngine()
    return _engine