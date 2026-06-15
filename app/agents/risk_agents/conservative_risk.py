import json
import logging
from typing import Dict
from app.agents.base import BaseAgent

logger = logging.getLogger(__name__)


class ConservativeRiskAgent(BaseAgent):
    name = "conservative_risk"

    PROMPT = """你是保守型风控官。你的风控哲学是：**保住本金是第一原则，宁可错过不可做错，
一旦出现风险信号立即减仓。**你倾向于低波动、低回撤，对任何风险信号都高度敏感。

## 熔断检查结果
{circuit_breaker}

## 回撤保护结果
{drawdown}

## 风险预算检查
{risk_budget}

## 压力测试结果
{stress_test}

## 当前市场阶段
{market_regime}

## 决策要求
基于你的保守风控哲学，输出JSON格式的风控意见（不要包含其他文字）：
{{
  "risk_philosophy": "conservative",
  "overall_assessment": "对当前风险的总体判断",
  "risk_level": "low/medium/high/critical",
  "suggested_action": "proceed/caution/reduce/stop",
  "max_drawdown_tolerance": 0.0-1.0,
  "position_adjustment": 0.0-1.0,
  "reasoning": "基于保守视角的推理过程",
  "key_concerns": ["关注点"],
  "summary": "一句话总结保守风控意见"
}}

position_adjustment: 建议的仓位调整系数，1.0=满仓，0.5=半仓，0.0=空仓"""

    def analyze(self, risk_data: Dict) -> Dict:
        prompt = self.PROMPT.format(
            circuit_breaker=json.dumps(risk_data.get("circuit_breaker", {}), ensure_ascii=False, indent=2),
            drawdown=json.dumps(risk_data.get("drawdown", {}), ensure_ascii=False, indent=2),
            risk_budget=json.dumps(risk_data.get("risk_budget", {}), ensure_ascii=False, indent=2),
            stress_test=json.dumps(risk_data.get("stress_test", {}), ensure_ascii=False, indent=2),
            market_regime=risk_data.get("market_regime", "unknown"),
        )
        result = self.call_llm(prompt, temperature=0.3)
        return result if result else {"error": "ConservativeRiskAgent分析失败"}
