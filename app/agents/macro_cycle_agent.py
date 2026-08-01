import json
import logging
from typing import Dict, List
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)


class MacroCycleAgent(BaseAgent):
    name = "macro_cycle"

    PROMPT = """你是宏观经济周期分析师。基于以下市场数据推断当前经济周期阶段，并给出ETF板块轮动建议。

## 市场代理数据
{market_data}

## 分析要求
通过价格动量、成交量变化、板块分化等信号推断宏观周期。输出JSON（不要包含其他文字）：
{{
  "cycle_phase": "recovery/overheating/stagflation/recession",
  "confidence": 0.0-1.0,
  "evidence": ["支撑判断的关键信号"],
  "sector_rotation": {{
    "overweight": ["建议超配的板块/ETF代码"],
    "underweight": ["建议低配的板块/ETF代码"],
    "rationale": "轮动逻辑说明"
  }},
  "duration_estimate": "预计当前阶段持续时长（周/月）",
  "transition_risk": "向下一阶段转换的风险信号",
  "summary": "一句话总结当前宏观状态"
}}

周期定义：
- recovery: 经济复苏，周期股/金融/可选消费领涨
- overheating: 经济过热，商品/能源/通胀保护资产领涨
- stagflation: 滞胀，防御板块/现金/黄金占优
- recession: 衰退，债券/公用事业/必选消费占优"""

    def analyze(self, etf_codes: List[str], db: Session) -> Dict:
        market_data = self._build_market_proxy(etf_codes, db)
        if not market_data:
            return {"error": "市场数据不足，无法判断宏观周期"}

        prompt = self.PROMPT.format(
            market_data=json.dumps(market_data, ensure_ascii=False, indent=2)
        )
        result = self.call_llm(prompt)
        if result and "error" not in result:
            result["data_source"] = "price_proxy"
        return result

    def _build_market_proxy(self, etf_codes: List[str], db: Session) -> Dict:
        proxy = {}
        for code in etf_codes[:10]:
            quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(60).all()

            if len(quotes) < 20:
                continue

            quotes.reverse()
            prices = [q.close_price for q in quotes]
            volumes = [q.volume for q in quotes]

            ret_20d = (prices[-1] / prices[-20] - 1) * 100 if prices[-20] else 0
            ret_5d = (prices[-1] / prices[-5] - 1) * 100 if prices[-5] else 0
            vol_ratio = (sum(volumes[-5:]) / 5) / (sum(volumes[-20:]) / 20) if sum(volumes[-20:]) else 1

            proxy[code] = {
                "return_5d": round(ret_5d, 2),
                "return_20d": round(ret_20d, 2),
                "volume_ratio_5d_20d": round(vol_ratio, 2),
                "latest_price": prices[-1],
            }

        return proxy
