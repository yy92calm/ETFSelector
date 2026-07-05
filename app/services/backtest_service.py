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
from app.strategies.base import PortfolioContext, compute_adjustment

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
            rebalance_freq=strategy.rebalance_freq or "monthly",
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
        last_prices: Dict[str, float] = {}  # 前值填充用

        for td in trade_dates:
            # 获取当日价格（缺数据的 ETF 用前值填充，保证资产估值连续）
            current_prices = {}
            for code in etf_codes:
                if code in all_data:
                    price_df = all_data[code]
                    day_price = price_df[price_df['trade_date'] == td]
                    if not day_price.empty:
                        current_prices[code] = day_price.iloc[0]['close']
                        last_prices[code] = day_price.iloc[0]['close']
                    elif code in last_prices:
                        current_prices[code] = last_prices[code]

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

                        result = compute_adjustment(
                            action, amount, price,
                            holdings.get(etf_code, 0), cash
                        )
                        if not result:
                            continue

                        holdings[etf_code] = result["new_qty"]
                        cash += result["cash_delta"]

                        rebalance_record["adjustments"].append({
                            "etf_code": etf_code,
                            "action": "买入" if result["direction"] == "buy" else "卖出",
                            "quantity": result["quantity"],
                            "price": price,
                            "amount": round(result["actual_amount"], 2)
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
        
        # 计算交易次数和胜率
        # 胜率定义：每次再平衡（initial/time_based）后持有到下次再平衡（或期末）的收益为正的比例
        sig_records = [
            r for r in rebalance_records
            if r.get("trigger_type") in ["initial", "time_based"]
        ]
        trade_count = len(sig_records)
        win_count = 0

        # 建立日期->资产索引，避免重复遍历
        asset_by_date = {d["date"]: d["total_asset"] for d in daily_data}
        final_date = daily_data[-1]["date"] if daily_data else None

        for i, record in enumerate(sig_records):
            start_asset = asset_by_date.get(record["date"])
            next_date = sig_records[i + 1]["date"] if i + 1 < len(sig_records) else final_date
            end_asset = asset_by_date.get(next_date) if next_date else None

            if start_asset and end_asset and start_asset > 0 and end_asset > start_asset:
                win_count += 1

        # 胜率（再平衡周期间盈利比例）
        win_rate = (win_count / trade_count * 100) if trade_count > 0 else None
        
        # 计算区间收益（按月度分段）
        period_returns = self._calc_period_returns(daily_data, start_date, end_date)
        
        # 计算时间段收益（1个月、3个月、6个月、12个月）
        time_period_returns = self._calc_time_period_returns(daily_data, start_date, end_date)
        
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
            "trade_count": trade_count,
            "win_count": win_count,
            "win_rate": round(win_rate, 2) if win_rate else None,
            "period_returns": period_returns,
            "time_period_returns": time_period_returns,
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
        """获取交易日列表（取并集，缺数据的 ETF 在回测时用前值填充）"""
        trade_dates = set()

        for code, df in all_data.items():
            dates = set(df[df['trade_date'] >= start_date]['trade_date'].tolist())
            trade_dates.update(dates)

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
    
    def _calc_period_returns(self, daily_data: List[dict], start_date: date, 
                              end_date: date) -> List[dict]:
        """
        计算区间收益（按月度分段）
        
        Returns:
            [
                {
                    "period": "2025-01",
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-31",
                    "start_asset": 100000,
                    "end_asset": 105000,
                    "return_pct": 5.0
                },
                ...
            ]
        """
        if not daily_data:
            return []
        
        # 按月份分组
        period_returns = []
        
        # 获取起始月份和结束月份
        from datetime import datetime
        
        start_month = datetime.strptime(start_date.isoformat(), "%Y-%m-%d").strftime("%Y-%m")
        end_month = datetime.strptime(end_date.isoformat(), "%Y-%m-%d").strftime("%Y-%m")
        
        # 遍历daily_data，按月份分组
        monthly_data = {}
        for d in daily_data:
            date_str = d["date"]
            month = datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m")
            
            if month not in monthly_data:
                monthly_data[month] = []
            
            monthly_data[month].append(d)
        
        # 计算每个月的收益
        prev_end_asset = None
        for month in sorted(monthly_data.keys()):
            month_data = monthly_data[month]
            
            # 月初资产
            start_asset = month_data[0]["total_asset"]
            
            # 月末资产
            end_asset = month_data[-1]["total_asset"]
            
            # 如果有上个月的月末资产，使用它作为月初资产
            if prev_end_asset:
                start_asset = prev_end_asset
            
            # 计算收益率
            if start_asset > 0:
                return_pct = (end_asset - start_asset) / start_asset * 100
            else:
                return_pct = 0
            
            period_returns.append({
                "period": month,
                "start_date": month_data[0]["date"],
                "end_date": month_data[-1]["date"],
                "start_asset": round(start_asset, 2),
                "end_asset": round(end_asset, 2),
                "return_pct": round(return_pct, 4)
            })
            
            prev_end_asset = end_asset
        
        return period_returns

    def _calc_time_period_returns(self, daily_data: List[dict], start_date: date, 
                                    end_date: date) -> List[dict]:
        """
        计算时间段收益（1个月、3个月、6个月、12个月）
        
        Returns:
            [
                {
                    "period": "最近1个月",
                    "days": 30,
                    "return_pct": 2.5,
                    "start_asset": 100000,
                    "end_asset": 102500
                },
                ...
            ]
        """
        if not daily_data:
            return []
        
        periods = [
            {"label": "最近1个月", "days": 30},
            {"label": "最近3个月", "days": 90},
            {"label": "最近半年", "days": 180},
            {"label": "最近1年", "days": 365},
        ]
        
        period_returns = []
        total_days = len(daily_data)
        
        for period_info in periods:
            days = period_info["days"]
            
            # 如果数据不足该时间段，跳过
            if total_days < days:
                continue
            
            # 取最近N天的数据
            start_idx = total_days - days
            end_idx = total_days - 1
            
            start_data = daily_data[start_idx]
            end_data = daily_data[end_idx]
            
            start_asset = start_data["total_asset"]
            end_asset = end_data["total_asset"]
            
            # 计算收益率
            if start_asset > 0:
                return_pct = (end_asset - start_asset) / start_asset * 100
            else:
                return_pct = 0
            
            period_returns.append({
                "period": period_info["label"],
                "days": days,
                "start_date": start_data["date"],
                "end_date": end_data["date"],
                "start_asset": round(start_asset, 2),
                "end_asset": round(end_asset, 2),
                "return_pct": round(return_pct, 4)
            })
        
        return period_returns


_engine: Optional[BacktestEngine] = None


def get_backtest_engine() -> BacktestEngine:
    global _engine
    if _engine is None:
        _engine = BacktestEngine()
    return _engine