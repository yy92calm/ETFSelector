import json
import logging
from typing import Dict, List
from sqlalchemy.orm import Session
import numpy as np

from app.agents.base import BaseAgent
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)


class CrossAssetAgent(BaseAgent):
    name = "cross_asset"

    PROMPT = """你是跨资产相关性分析师。基于以下ETF间相关性矩阵变化，判断市场风险偏好切换信号。

## 相关性数据
{correlation_data}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "risk_mode": "risk_on/risk_off/transition",
  "confidence": 0.0-1.0,
  "correlation_shift": "相关性结构变化的核心特征",
  "diversification_signal": "分散化是否有效（相关性同升=分散失效）",
  "defensive_recommendation": {{
    "should_reduce_equity": true/false,
    "suggested_defensive_etfs": ["防御性ETF代码"],
    "reason": "原因"
  }},
  "key_observations": ["关键观察"],
  "summary": "一句话总结跨资产状态"
}}

判断逻辑：
- 股债负相关增强 → risk_on（正常配置环境）
- 股债同涨同跌（相关性趋正） → 流动性驱动或恐慌，需警惕
- 商品与股票同涨 → 通胀交易/过热
- 所有资产相关性飙升 → 系统性风险，分散失效"""

    def analyze(self, etf_codes: List[str], db: Session) -> Dict:
        corr_data = self._compute_correlations(etf_codes, db)
        if not corr_data:
            return {"error": "数据不足，无法计算相关性"}

        prompt = self.PROMPT.format(
            correlation_data=json.dumps(corr_data, ensure_ascii=False, indent=2)
        )
        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["correlation_matrix"] = corr_data.get("current_matrix")
        return result

    def _compute_correlations(self, etf_codes: List[str], db: Session) -> Dict:
        returns_map = {}
        for code in etf_codes[:8]:
            quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(40).all()

            if len(quotes) < 20:
                continue

            quotes.reverse()
            prices = [q.close_price for q in quotes]
            daily_returns = [(prices[i] / prices[i-1] - 1) for i in range(1, len(prices))]
            returns_map[code] = daily_returns

        if len(returns_map) < 3:
            return {}

        codes = list(returns_map.keys())
        min_len = min(len(v) for v in returns_map.values())

        recent_matrix = self._corr_matrix(returns_map, codes, min_len, window=10)
        prior_matrix = self._corr_matrix(returns_map, codes, min_len, window=20, offset=10)

        return {
            "etf_codes": codes,
            "current_matrix": recent_matrix,
            "prior_matrix": prior_matrix,
            "avg_correlation_current": self._avg_corr(recent_matrix, codes),
            "avg_correlation_prior": self._avg_corr(prior_matrix, codes),
        }

    def _corr_matrix(self, returns_map: Dict, codes: List[str], min_len: int,
                     window: int, offset: int = 0) -> Dict:
        end = min_len - offset
        start = max(0, end - window)
        matrix = {}
        for c1 in codes:
            matrix[c1] = {}
            for c2 in codes:
                r1 = returns_map[c1][start:end]
                r2 = returns_map[c2][start:end]
                if len(r1) < 5 or len(r2) < 5:
                    matrix[c1][c2] = 0.0
                else:
                    corr = np.corrcoef(r1, r2)[0, 1]
                    matrix[c1][c2] = round(float(corr), 3) if not np.isnan(corr) else 0.0
        return matrix

    def _avg_corr(self, matrix: Dict, codes: List[str]) -> float:
        vals = []
        for i, c1 in enumerate(codes):
            for c2 in codes[i+1:]:
                vals.append(matrix.get(c1, {}).get(c2, 0.0))
        return round(sum(vals) / len(vals), 3) if vals else 0.0
