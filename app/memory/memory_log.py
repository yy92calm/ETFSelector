import os
import json
import logging
from datetime import date, datetime
from typing import Dict, Optional
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "memory_logs")


class MemoryLog:
    def __init__(self, strategy_id: int):
        self.strategy_id = strategy_id
        os.makedirs(MEMORY_DIR, exist_ok=True)
        self.filepath = os.path.join(MEMORY_DIR, f"strategy_{strategy_id}.md")

    def record_decision(self, decision: Dict):
        entry = self._format_entry(decision)
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(entry + "\n\n---\n\n")

    def record_outcome(self, decision_date: str, actual_return: float, outcome: str):
        append = (
            f"### 结果反馈 [{decision_date}]\n"
            f"- 实际收益: {actual_return:.2f}%\n"
            f"- 结果评估: {outcome}\n"
            f"- 反馈时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        )
        with open(self.filepath, "a", encoding="utf-8") as f:
            f.write(append + "\n---\n\n")

    def _format_entry(self, d: Dict) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        lines = [
            f"## 决策记录 [{d.get('analysis_date', now)}]",
            f"- 生成时间: {now}",
            f"- 市场阶段: {d.get('market_regime', 'unknown')}",
            f"- 市场情绪: {d.get('market_sentiment', 'unknown')}",
            f"- 建议操作: {d.get('suggested_action', 'unknown')}",
            f"- 置信度: {d.get('confidence_level', 'unknown')}",
            f"- 决策理由: {d.get('action_reason', '无')}",
            "",
            "### 技术分析师观点",
            f"- 趋势判断: {d.get('technical_report', {}).get('overall_trend', '未知')}",
            f"- 总览: {d.get('technical_report', {}).get('summary', '无')}",
            "",
            "### 情绪分析师观点",
            f"- 情绪判断: {d.get('sentiment_report', {}).get('market_sentiment', '未知')}",
            f"- 总览: {d.get('sentiment_report', {}).get('summary', '无')}",
            "",
            "### 最终配置",
            json.dumps(d.get('suggested_allocation', {}), ensure_ascii=False, indent=2),
            "",
            "### 风险预警",
            f"- 级别: {d.get('risk_alert', {}).get('level', '无')}",
            f"- 因素: {', '.join(d.get('risk_alert', {}).get('factors', []))}",
            "",
            "### 分析师一致性",
            f"- 一致性: {d.get('agreement_level', 'unknown')}",
            f"- 分歧说明: {d.get('agreement_note', '无')}",
        ]
        return "\n".join(lines)
