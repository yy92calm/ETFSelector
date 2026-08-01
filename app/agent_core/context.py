"""上下文构建器 - 为LLM提供系统状态摘要"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


class ContextBuilder:
    """构建系统状态上下文，注入到 LLM system prompt 中"""

    def build_system_context(self, db: Session) -> str:
        """构建当前系统状态摘要（压缩版，避免token过长）"""
        parts = []

        # 1. 活跃策略概况
        strategies_summary = self._get_strategies_summary(db)
        if strategies_summary:
            parts.append(f"【活跃策略】\n{strategies_summary}")

        # 2. 最新市场概况（前5只涨跌幅最大的）
        market_summary = self._get_market_summary(db)
        if market_summary:
            parts.append(f"【市场概况】\n{market_summary}")

        # 3. 风控状态
        risk_summary = self._get_risk_summary(db)
        if risk_summary:
            parts.append(f"【风控状态】\n{risk_summary}")

        # 4. 最近AI决策
        recent_actions = self._get_recent_actions(db)
        if recent_actions:
            parts.append(f"【最近AI决策】\n{recent_actions}")

        if not parts:
            return "系统刚初始化，暂无历史数据。"

        return "\n\n".join(parts)

    def _get_strategies_summary(self, db: Session) -> str:
        from app.models.strategy import Strategy

        strategies = db.query(Strategy).filter(Strategy.status == "active").all()
        if not strategies:
            return ""

        lines = []
        for s in strategies[:5]:
            alloc = s.allocation_config or {}
            alloc_str = ", ".join(f"{k}:{v:.0%}" for k, v in list(alloc.items())[:4])
            status = s.auto_strategy_status or s.status
            lines.append(f"- [{s.id}] {s.name} | 状态:{status} | 配置:{alloc_str}")

        return "\n".join(lines)

    def _get_market_summary(self, db: Session) -> str:
        from app.models.etf import ETFQuotation, ETFBasic
        from sqlalchemy import func

        latest_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        if not latest_date:
            return ""

        quotes = (
            db.query(ETFQuotation)
            .filter(ETFQuotation.trade_date == latest_date)
            .all()
        )
        if not quotes:
            return ""

        # 涨跌幅排序取前5
        sorted_quotes = sorted(quotes, key=lambda q: q.change_pct or 0, reverse=True)
        top = sorted_quotes[:3]
        bottom = sorted_quotes[-3:] if len(sorted_quotes) > 3 else []

        up_count = sum(1 for q in quotes if (q.change_pct or 0) > 0)
        down_count = sum(1 for q in quotes if (q.change_pct or 0) < 0)

        lines = [f"交易日:{latest_date.isoformat()} | 上涨:{up_count} 下跌:{down_count} 总计:{len(quotes)}"]

        # 获取ETF名称映射
        codes = [q.etf_code for q in (top + bottom)]
        names = {e.etf_code: e.etf_name for e in db.query(ETFBasic).filter(ETFBasic.etf_code.in_(codes)).all()}

        for q in top:
            name = names.get(q.etf_code, q.etf_code)
            lines.append(f"  ↑ {name}({q.etf_code}) {q.change_pct:+.2f}%")
        for q in bottom:
            name = names.get(q.etf_code, q.etf_code)
            lines.append(f"  ↓ {name}({q.etf_code}) {q.change_pct:+.2f}%")

        return "\n".join(lines)

    def _get_risk_summary(self, db: Session) -> str:
        from app.models.strategy import Strategy
        from app.services.risk_controller import RiskController

        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).all()

        if not auto_strategies:
            return ""

        ctrl = RiskController()
        lines = []
        for s in auto_strategies[:3]:
            cb = ctrl.check_circuit_breaker(s.id, db)
            dd = ctrl.apply_drawdown_protection(s.id, db)
            risk_status = "正常"
            if cb.get("status") == "triggered":
                risk_status = f"⚠️熔断:{cb.get('reason', '')}"
            elif dd.get("status") == "critical":
                risk_status = f"⚠️回撤临界:{dd.get('drawdown_pct', 0)}%"
            elif dd.get("status") == "warning":
                risk_status = f"注意回撤:{dd.get('drawdown_pct', 0)}%"
            lines.append(f"- [{s.id}] {s.name}: {risk_status}")

        return "\n".join(lines)

    def _get_recent_actions(self, db: Session) -> str:
        from app.models.chat import AIActionLog

        logs = (
            db.query(AIActionLog)
            .order_by(AIActionLog.created_at.desc())
            .limit(3)
            .all()
        )
        if not logs:
            return ""

        lines = []
        for log in logs:
            time_str = log.created_at.strftime("%m-%d %H:%M") if log.created_at else "?"
            reasoning_short = (log.reasoning or "")[:60]
            lines.append(f"- [{time_str}] {log.trigger_type} | {log.status} | {reasoning_short}")

        return "\n".join(lines)
