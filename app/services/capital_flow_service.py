import json
import logging
from typing import Dict, List
from openai import OpenAI
from sqlalchemy.orm import Session
import numpy as np

from app.config import get_settings
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)
settings = get_settings()


class CapitalFlowService:
    """资金流向分析服务 - 通过量价关系推断资金方向"""

    FLOW_PROMPT = """你是资金流向分析师。基于以下ETF量价数据，识别聪明钱方向与散户方向的背离信号。

## 量价数据
{flow_data}

## 分析要求
输出JSON（不要包含其他文字）：
{{
  "capital_flow_signals": [
    {{
      "etf_code": "ETF代码",
      "flow_direction": "inflow/outflow/neutral",
      "smart_money_signal": "accumulating/distributing/neutral",
      "divergence": true/false,
      "divergence_description": "背离描述（如有）",
      "strength": 0.0-1.0
    }}
  ],
  "aggregate_flow": {{
    "net_direction": "inflow/outflow/balanced",
    "concentration": "资金集中流入/流出的板块",
    "rotation_signal": "是否观察到板块轮动"
  }},
  "contrarian_signal": {{
    "exists": true/false,
    "description": "聪明钱与价格方向背离的描述"
  }},
  "summary": "一句话总结资金流向状态"
}}

判断逻辑：
- 价跌量增 → 恐慌抛售（散户）或主力吸筹（需结合幅度）
- 价涨量缩 → 上涨乏力，可能是诱多
- 价平量增 → 换手活跃，可能有资金在布局
- 大幅低开后放量收平 → 典型主力吸筹信号"""

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )

    def analyze_capital_flow(self, etf_codes: List[str], db: Session) -> Dict:
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}

        flow_data = self._compute_flow_indicators(etf_codes, db)
        if not flow_data:
            return {"error": "数据不足，无法分析资金流向"}

        prompt = self.FLOW_PROMPT.format(
            flow_data=json.dumps(flow_data, ensure_ascii=False, indent=2)
        )

        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            content = response.choices[0].message.content
            return self._parse_json(content)
        except Exception as e:
            logger.error(f"资金流向分析LLM调用失败: {e}")
            return {"error": str(e)}

    def _compute_flow_indicators(self, etf_codes: List[str], db: Session) -> Dict:
        result = {}
        for code in etf_codes[:10]:
            quotes = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(20).all()

            if len(quotes) < 10:
                continue

            quotes.reverse()
            prices = [q.close_price for q in quotes]
            volumes = [q.volume for q in quotes]
            amounts = [q.amount for q in quotes]

            avg_vol_5 = np.mean(volumes[-5:])
            avg_vol_20 = np.mean(volumes)
            vol_surge = avg_vol_5 / avg_vol_20 if avg_vol_20 else 1

            price_chg_5d = (prices[-1] / prices[-6] - 1) * 100 if len(prices) > 5 else 0

            obv_trend = self._obv_slope(prices, volumes)

            result[code] = {
                "price_change_5d_pct": round(price_chg_5d, 2),
                "volume_surge_ratio": round(vol_surge, 2),
                "avg_amount_5d": round(np.mean(amounts[-5:]), 0) if amounts else 0,
                "obv_trend": obv_trend,
                "price_volume_divergence": (price_chg_5d > 0 and vol_surge < 0.8) or
                                           (price_chg_5d < 0 and vol_surge > 1.3),
            }

        return result

    def _obv_slope(self, prices: List[float], volumes: List[float]) -> str:
        obv = [0.0]
        for i in range(1, len(prices)):
            if prices[i] > prices[i-1]:
                obv.append(obv[-1] + volumes[i])
            elif prices[i] < prices[i-1]:
                obv.append(obv[-1] - volumes[i])
            else:
                obv.append(obv[-1])

        if len(obv) < 5:
            return "flat"
        recent_slope = obv[-1] - obv[-5]
        if recent_slope > 0:
            return "rising"
        elif recent_slope < 0:
            return "falling"
        return "flat"

    def _parse_json(self, content: str) -> Dict:
        import re
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"资金流向JSON解析失败: {e}")
        return {"error": "无法解析响应"}


_service: CapitalFlowService | None = None


def get_capital_flow_service() -> CapitalFlowService:
    global _service
    if _service is None:
        _service = CapitalFlowService()
    return _service
