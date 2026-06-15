import json
import logging
from typing import Dict
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class RiskManager(BaseAgent):
    name = "risk_manager"

    PROMPT = """你是风控主管（Chief Risk Officer）。你需要审阅三位不同风格风控官的报告，综合做出最终风控决策。

## 📗 激进风控官报告
{aggressive_report}

## 📙 保守风控官报告
{conservative_report}

## 📘 中性风控官报告
{neutral_report}

## 决策要求
综合三方意见，输出最终风控决策JSON（不要包含其他文字）：
{{
  "final_risk_level": "low/medium/high/critical",
  "final_suggested_action": "proceed/caution/reduce/stop",
  "final_position_adjustment": 0.0-1.0,
  "agreement": "consensus/partial/disagreement",
  "adopted_philosophy": "aggressive/conservative/neutral/compromise",
  "adopted_reason": "为什么采信该风控意见",
  "risk_factors": ["最终确定的风险因素"],
  "action": "proceed/caution/reduce/stop",
  "reason": "最终决策理由"
}}"""

    def analyze(self, aggressive: Dict, conservative: Dict, neutral: Dict) -> Dict:
        prompt = self.PROMPT.format(
            aggressive_report=json.dumps(aggressive, ensure_ascii=False, indent=2),
            conservative_report=json.dumps(conservative, ensure_ascii=False, indent=2),
            neutral_report=json.dumps(neutral, ensure_ascii=False, indent=2),
        )
        result = self.call_llm(prompt, temperature=0.2)
        return result if result else {"error": "RiskManager分析失败"}
