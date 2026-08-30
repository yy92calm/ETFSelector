import json
import logging
from datetime import date
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.agents.base import BaseAgent
from app.models.strategy import Strategy
from app.models.etf import ETFQuotation, ETFBasic
from app.models.experience import Experience
from app.services.market_environment_service import MarketEnvironmentService

logger = logging.getLogger(__name__)


class MarketAnalystAgent(BaseAgent):
    name = "market_analyst"

    PROMPT = """你是ETF策略投资决策官（Research Manager）。你需要审阅多头和空头两位研究员的分析报告，结合市场环境数据，做出最终配置决策。

## 📗 多头研究员报告（Bull Case）
{bull_report}

## 📕 空头研究员报告（Bear Case）
{bear_report}

## 技术面分析报告
{technical_report}

## 情绪面分析报告
{sentiment_report}

## 当前配置ETF表现
{nav_changes}

## 当前仓位配置
{current_allocation}

## 历史相似环境案例
{similar_environments}

## 历史经验参考
{experience_section}

## ⚠️ 可用ETF列表（仅限从中选择）
{available_etfs}

## 【策略核心目标与交易成本】
{monthly_target}
- 每次买卖收取手续费（{commission_note}），调仓前须权衡预期收益改善是否覆盖交易成本；
- 目标进度落后时：优先提高权益暴露的进攻性，但不得突破风控上限；
- 达标时：维持当前配置，减少无谓调仓；
- 超额时：逐步兑现收益、增配防御资产，保护既有收益。

## 决策要求
综合多空双方论证和多维度数据，做出最终决策。输出JSON格式（不要包含其他文字）：
{{
  "market_regime": "bull_quiet/bull_volatile/bear_quiet/bear_panic/crisis/neutral",
  "regime_confidence": "high/medium/low",
  "bull_case_weight": 0.0-1.0,
  "bear_case_weight": 0.0-1.0,
  "agreement_level": "consensus/partial/disagreement",
  "agreement_note": "多空分歧点的判断和权衡说明",
  "suggested_action": "hold/rebalance",
  "suggested_allocation": {{"ETF代码": 0.x}},
  "action_reason": "权衡多空观点后的决策理由",
  "risk_alert": {{
    "level": "low/medium/high",
    "factors": ["风险因素"]
  }},
  "key_signals_summary": ["关键信号列表"]
}}

严格约束：
- allocation比例总和必须等于1.0
- suggested_allocation中的ETF代码必须来自"可用ETF列表"
- bull_case_weight + bear_case_weight 不一定等于1，它们反映你对多空论证强度的独立评估"""
    SIMILAR_PROMPT = """你是ETF市场环境分析师。请基于当前市场环境数据和历史经验，分析当前所处的市场阶段。

## 当前技术面分析
{technical_report}

## 当前情绪面分析
{sentiment_report}

## 当前配置ETF表现
{nav_changes}

## 历史相似环境案例
{similar_environments}

## 历史经验参考
{experience_section}

## 可用ETF列表
{available_etfs}

## 分析要求
基于以上数据，识别当前市场阶段，输出JSON格式（不要包含其他文字）：
{{
  "market_regime": "bull_quiet/bull_volatile/bear_quiet/bear_panic/crisis/neutral",
  "historical_pattern": {{
    "similar_case_count": 0,
    "avg_future_return": 0.0,
    "success_rate": 0.0
  }},
  "regime_characteristics": ["特征描述"],
  "summary": "一句话总结市场环境判断"
}}"""

    def analyze(self, strategy_id: int, analysis_date: date,
                technical_report: Dict, sentiment_report: Dict, db: Session,
                bull_report: Dict = None, bear_report: Dict = None,
                monthly_target: str = "") -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}

        current_allocation = strategy.allocation_config or {}
        nav_changes = self._get_nav_changes(list(current_allocation.keys()), db, analysis_date)
        similar_environments = self._find_similar_environments(strategy_id, analysis_date, db)
        experiences = self._get_relevant_experiences(strategy_id, analysis_date, db)
        available_etfs = self._get_available_etfs(db)

        from app.config import get_settings
        _s = get_settings()
        commission_note = f"费率{_s.commission_rate:.1%}、最低{_s.commission_min:.0f}元"

        prompt = self.PROMPT.format(
            monthly_target=monthly_target or "（未设定月收益目标）",
            commission_note=commission_note,
            bull_report=json.dumps(bull_report or {}, ensure_ascii=False, indent=2),
            bear_report=json.dumps(bear_report or {}, ensure_ascii=False, indent=2),
            technical_report=json.dumps(technical_report, ensure_ascii=False, indent=2),
            sentiment_report=json.dumps(sentiment_report, ensure_ascii=False, indent=2),
            nav_changes=json.dumps(nav_changes, ensure_ascii=False, indent=2),
            current_allocation=json.dumps(current_allocation, ensure_ascii=False, indent=2),
            similar_environments=json.dumps(similar_environments, ensure_ascii=False, indent=2),
            experience_section=self._format_experiences(experiences),
            available_etfs=available_etfs,
        )

        result = self.call_llm(prompt, temperature=0.3)

        if result and "error" not in result:
            result["analysis_date"] = analysis_date.isoformat()
            result["similar_environments_used"] = len(similar_environments)
        return result

    def _get_nav_changes(self, etf_codes: List[str], db: Session, lock_date: Optional[date] = None) -> Dict:
        result = {}
        for code in etf_codes:
            query = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            )
            if lock_date is not None:
                query = query.filter(ETFQuotation.trade_date <= lock_date)
            quotations = query.order_by(ETFQuotation.trade_date.desc()).limit(5).all()
            if quotations:
                latest = quotations[0]
                change_5d = None
                if len(quotations) >= 5:
                    change_5d = (latest.close_price - quotations[4].close_price) / quotations[4].close_price * 100
                result[code] = {
                    "latest_nav": latest.close_price,
                    "change_5d_pct": round(change_5d, 2) if change_5d else None,
                }
        return result

    def _find_similar_environments(self, strategy_id: int, target_date: date, db: Session) -> List[Dict]:
        env_svc = MarketEnvironmentService()
        similar = env_svc.find_similar_market_environments(strategy_id, target_date, db, top_k=5)
        summary = []
        for env in similar[:3]:
            summary.append({
                "date": env["date"],
                "similarity": env["similarity"],
                "future_return": env.get("future_return"),
                "allocation": env.get("allocation"),
            })
        return summary

    def _get_relevant_experiences(self, strategy_id: int, target_date: date, db: Session) -> List[Experience]:
        from app.services.smart_experience_matcher import SmartExperienceMatcher

        matcher = SmartExperienceMatcher()
        current_scenario = matcher.get_current_market_scenario(target_date, db)
        matched = matcher.match_experiences_by_scenario(strategy_id, current_scenario, db)
        experiences = [m["experience"] for m in matched]

        # 场景无匹配时退回按有效性排序，避免经验完全缺失
        if not experiences:
            experiences = db.query(Experience).filter(
                Experience.strategy_id == strategy_id,
                Experience.is_active == True,
                Experience.expires_date >= target_date,
            ).order_by(
                Experience.effectiveness_score.desc(),
                Experience.application_count.desc()
            ).limit(8).all()

        return experiences

    def _format_experiences(self, experiences: List[Experience]) -> str:
        if not experiences:
            return "暂无历史经验"
        sections = []
        for exp in experiences:
            prefix = "【成功经验】" if exp.experience_type == "success" else "【失败教训】" if exp.experience_type == "failure" else "【洞察】"
            sections.append(f"{prefix} {exp.title}: {exp.key_insight or exp.description[:100]}")
        return "\n".join(sections)

    def _get_available_etfs(self, db: Session) -> str:
        etfs = db.query(ETFBasic).all()
        if not etfs:
            return "暂无可用ETF"
        lines = [f"{etf.etf_code}: {etf.etf_name}" for etf in etfs]
        return "\n".join(lines)
