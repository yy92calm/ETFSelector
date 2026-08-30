import json
import logging
from typing import Dict, List
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.strategy import Strategy
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)


class RebalanceTimingAgent(BaseAgent):
    name = "rebalance_timing"

    PROMPT = """你是再平衡时机判断分析师。基于以下组合偏离度和市场状态，判断是否应立即执行再平衡。

## 偏离度与市场数据
{timing_data}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "decision": "immediate/wait/staged",
  "confidence": 0.0-1.0,
  "reasoning": "判断理由",
  "drift_severity": "high/medium/low",
  "market_condition": "favorable/neutral/unfavorable",
  "staged_plan": {{
    "tranches": 分批次数,
    "interval_days": 每批间隔天数,
    "note": "分批说明"
  }},
  "cost_benefit": {{
    "expected_drift_cost": "不调整的预期损失描述",
    "transaction_cost": "调整的交易成本描述",
    "net_benefit": "正/负/不确定"
  }},
  "summary": "一句话总结"
}}

判断逻辑：
- immediate: 偏离度>5%且市场流动性充足，立即调整
- wait: 偏离度<2%或市场极端波动（调整成本高），等待
- staged: 偏离度3-5%或市场不确定，分2-3批执行

注意：频繁再平衡的摩擦成本可能超过收益，需权衡。"""

    def analyze(self, strategy_id: int, db: Session) -> Dict:
        timing_data = self._collect_timing_data(strategy_id, db)
        if not timing_data:
            return {"error": "数据不足，无法判断再平衡时机"}

        prompt = self.PROMPT.format(
            timing_data=json.dumps(timing_data, ensure_ascii=False, indent=2)
        )
        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["strategy_id"] = strategy_id
        return result

    def _collect_timing_data(self, strategy_id: int, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy or not strategy.allocation_config:
            return {}

        allocation = strategy.allocation_config
        drift_info = {}

        for code, target_weight in list(allocation.items())[:8]:
            quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(20).all()

            if len(quotes) < 5:
                continue

            period_return = (quotes[0].close_price / quotes[-1].close_price - 1)
            avg_volume_5d = sum(q.volume for q in quotes[:5]) / 5
            avg_volume_20d = sum(q.volume for q in quotes) / len(quotes)
            liquidity_ratio = avg_volume_5d / avg_volume_20d if avg_volume_20d else 1

            drift_info[code] = {
                "target_weight": target_weight,
                "recent_return_pct": round(period_return * 100, 2),
                "estimated_drift": round(period_return * target_weight * 100, 2),
                "liquidity_ratio": round(liquidity_ratio, 2),
                "volatility_5d": round(
                    (max(q.close_price for q in quotes[:5]) /
                     min(q.close_price for q in quotes[:5]) - 1) * 100, 2
                ),
            }

        total_drift = sum(abs(d.get("estimated_drift", 0)) for d in drift_info.values())

        return {
            "total_drift_pct": round(total_drift, 2),
            "rebalance_frequency": strategy.rebalance_freq,
            "days_since_last_adjustment": self._days_since_last(strategy),
            "etf_drift_details": drift_info,
        }

    def _days_since_last(self, strategy: Strategy) -> int:
        if not strategy.last_auto_analysis_date:
            return 999
        from datetime import date
        return (date.today() - strategy.last_auto_analysis_date).days
