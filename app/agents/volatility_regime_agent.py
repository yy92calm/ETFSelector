import json
import logging
from typing import Dict, List
from sqlalchemy.orm import Session
import numpy as np

from app.agents.base import BaseAgent
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)


class VolatilityRegimeAgent(BaseAgent):
    name = "volatility_regime"

    PROMPT = """你是波动率体制分析师。基于以下波动率数据和市场新闻语义，判断当前波动率体制。

## 波动率数据
{vol_data}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "regime": "low_trend/low_accumulation/high_panic/high_distribution",
  "confidence": 0.0-1.0,
  "vol_percentile": 0-100,
  "regime_description": "当前体制特征描述",
  "position_implication": {{
    "max_equity_exposure": 0.0-1.0,
    "recommended_cash_ratio": 0.0-1.0,
    "reason": "仓位建议理由"
  }},
  "transition_signals": ["可能切换体制的前兆信号"],
  "summary": "一句话总结"
}}

体制定义：
- low_trend: 低波动+趋势明确，适合持仓不动
- low_accumulation: 低波动+横盘蓄势，适合逐步建仓
- high_panic: 高波动+下跌恐慌，应减仓观望
- high_distribution: 高波动+宽幅震荡，适合高抛低吸

关键区分：低波动不等于安全，需结合趋势方向判断是"趋势延续"还是"暴风雨前的宁静"。"""

    def analyze(self, etf_codes: List[str], db: Session, news_context: str = "") -> Dict:
        vol_data = self._compute_volatility(etf_codes, db)
        if not vol_data:
            return {"error": "数据不足，无法计算波动率"}

        if news_context:
            vol_data["news_context"] = news_context[:500]

        prompt = self.PROMPT.format(
            vol_data=json.dumps(vol_data, ensure_ascii=False, indent=2)
        )
        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["vol_metrics"] = vol_data
        return result

    def _compute_volatility(self, etf_codes: List[str], db: Session) -> Dict:
        result = {}
        for code in etf_codes[:8]:
            quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(60).all()

            if len(quotes) < 20:
                continue

            quotes.reverse()
            prices = [q.close_price for q in quotes]
            daily_returns = [(prices[i] / prices[i-1] - 1) for i in range(1, len(prices))]

            vol_5d = np.std(daily_returns[-5:]) * np.sqrt(252) * 100
            vol_20d = np.std(daily_returns[-20:]) * np.sqrt(252) * 100
            vol_60d = np.std(daily_returns) * np.sqrt(252) * 100

            percentile = self._percentile_rank(daily_returns, vol_20d)

            trend_20d = (prices[-1] / prices[-20] - 1) * 100 if prices[-20] else 0

            result[code] = {
                "vol_5d_annualized": round(vol_5d, 2),
                "vol_20d_annualized": round(vol_20d, 2),
                "vol_60d_annualized": round(vol_60d, 2),
                "vol_percentile_60d": round(percentile, 1),
                "trend_20d_pct": round(trend_20d, 2),
                "vol_expanding": bool(vol_5d > vol_20d),
            }

        return result

    def _percentile_rank(self, returns: List[float], current_vol: float) -> float:
        window_vols = []
        for i in range(20, len(returns) + 1):
            w = returns[i-20:i]
            window_vols.append(np.std(w) * np.sqrt(252) * 100)
        if not window_vols:
            return 50.0
        below = sum(1 for v in window_vols if v <= current_vol)
        return (below / len(window_vols)) * 100
