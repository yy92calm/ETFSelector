import json
import logging
from typing import Dict, List
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.portfolio import PortfolioSnapshot
from app.models.etf import ETFQuotation
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)


class DrawdownAttributionAgent(BaseAgent):
    name = "drawdown_attribution"

    PROMPT = """你是组合回撤归因分析师。基于以下组合回撤数据，将损失归因到具体因素并生成自然语言复盘。

## 回撤数据
{drawdown_data}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "attribution": [
    {{
      "factor": "归因因素（行业/风格/个券/系统性）",
      "contribution_pct": 0.0-1.0,
      "affected_etfs": ["相关ETF代码"],
      "explanation": "该因素如何导致回撤"
    }}
  ],
  "primary_cause": "最主要回撤原因",
  "is_systemic": true/false,
  "recovery_outlook": "recovery_likely/sideways/further_risk",
  "lessons": ["可写入经验库的教训"],
  "narrative": "完整的自然语言复盘叙述（3-5句话）"
}}

归因维度：
- 系统性风险：大盘整体下跌
- 行业集中：某行业ETF拖累
- 风格偏移：成长/价值/小盘风格不利
- 个券风险：单只ETF异常下跌
- 相关性崩溃：多资产同跌（分散失效）"""

    def analyze(self, strategy_id: int, db: Session) -> Dict:
        drawdown_data = self._collect_drawdown_data(strategy_id, db)
        if not drawdown_data:
            return {"error": "无足够快照数据计算回撤"}

        prompt = self.PROMPT.format(
            drawdown_data=json.dumps(drawdown_data, ensure_ascii=False, indent=2)
        )
        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["strategy_id"] = strategy_id
        return result

    def _collect_drawdown_data(self, strategy_id: int, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {}

        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id
        ).order_by(PortfolioSnapshot.snapshot_date.desc()).limit(30).all()

        if len(snapshots) < 5:
            return {}

        snapshots.reverse()
        values = [s.total_value for s in snapshots]
        peak = max(values)
        current = values[-1]
        drawdown_pct = (current / peak - 1) * 100 if peak else 0

        etf_performance = {}
        allocation = strategy.allocation_config or {}
        for code in list(allocation.keys())[:8]:
            quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(20).all()
            if len(quotes) >= 2:
                period_ret = (quotes[0].close_price / quotes[-1].close_price - 1) * 100
                etf_performance[code] = {
                    "period_return_pct": round(period_ret, 2),
                    "weight": allocation.get(code, 0),
                }

        return {
            "peak_value": round(peak, 2),
            "current_value": round(current, 2),
            "drawdown_pct": round(drawdown_pct, 2),
            "period_days": len(snapshots),
            "etf_performance": etf_performance,
            "allocation": allocation,
        }
