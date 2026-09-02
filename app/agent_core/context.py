"""上下文构建器 - 为LLM提供系统状态快照

对齐 deepseek-harness 的上下文模式：
- 系统身份与规则留在 system prompt（跨轮字节稳定，保 provider prompt cache）
- 动态状态按命名 section 组装成「每轮快照」，以 user-role 消息注入（不落库）
- 单 section 查询失败不拖垮整体，缺失部分静默跳过
"""

import logging
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.utils.trading_calendar import is_trading_day, is_market_open_now, now_cn

logger = logging.getLogger(__name__)


class ContextBuilder:
    """构建系统状态上下文，组装为每轮注入的快照消息"""

    def build_turn_snapshot(self, db: Session, summary: str = "") -> str:
        """组装一轮的完整快照文本（时间头 + 可选历史摘要 + 状态 section）

        Args:
            db: 数据库会话
            summary: 上下文压缩摘要（压缩触发过时非空），置于快照头部

        Returns:
            快照文本（注入为 user-role 消息，不落库）
        """
        parts = [self._time_header()]
        if summary:
            parts.append(f"[历史对话摘要]\n{summary}")
        sections = self.build_state_sections(db)
        if sections:
            parts.append(sections)
        if len(parts) == 1 and not sections:
            parts.append("系统刚初始化，暂无历史数据。")
        return "\n\n".join(parts)

    def _time_header(self) -> str:
        """时间上下文头：模型获得「现在几点、是否交易日」的事实来源"""
        now = now_cn()
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        trading = is_trading_day(now.date())
        if trading:
            session_state = "交易时段内" if is_market_open_now() else "非交易时段"
        else:
            session_state = "非交易日"
        return (f"当前时间: {now.strftime('%Y-%m-%d %H:%M')}（北京时间，"
                f"周{weekday_names[now.weekday()]}）| {session_state}")

    def build_state_sections(self, db: Session) -> str:
        """构建系统状态 section（压缩版，避免token过长）

        单 section 独立容错：查询失败记录日志并跳过，不影响其余部分。
        """
        builders = [
            ("活跃策略", self._get_strategies_summary),
            ("策略进化提示词", self._get_evolved_prompts),
            ("市场概况", self._get_market_summary),
            ("风控状态", self._get_risk_summary),
            ("最近AI决策", self._get_recent_actions),
        ]
        parts = []
        for title, builder in builders:
            try:
                content = builder(db)
                if content:
                    parts.append(f"【{title}】\n{content}")
            except Exception as e:
                logger.warning(f"上下文 section [{title}] 构建失败，跳过: {e}")
        return "\n\n".join(parts)

    def build_system_context(self, db: Session) -> str:
        """兼容旧调用：仅返回状态 section（不含时间头与摘要）"""
        sections = self.build_state_sections(db)
        return sections or "系统刚初始化，暂无历史数据。"

    def _get_strategies_summary(self, db: Session) -> str:
        from app.models.strategy import Strategy
        from app.services.portfolio_service import get_portfolio_service

        strategies = db.query(Strategy).filter(Strategy.status == "active").all()
        if not strategies:
            return ""

        lines = []
        for s in strategies[:5]:
            alloc = s.allocation_config or {}
            alloc_str = ", ".join(f"{k}:{v:.0%}" for k, v in list(alloc.items())[:4])
            status = s.auto_strategy_status or s.status
            # 收益目标与当月进度（不可变使命，AI须以此校准行动）
            try:
                progress = get_portfolio_service().get_monthly_progress(s.id, db)
                progress_text = progress["text"] if progress else ""
            except Exception as e:
                logger.warning(f"策略{s.id}目标进度查询失败: {e}")
                progress_text = ""
            t_min = s.target_monthly_min if s.target_monthly_min is not None else 0.05
            t_max = s.target_monthly_max if s.target_monthly_max is not None else 0.10
            lines.append(
                f"- [{s.id}] {s.name} | 状态:{status} | 配置:{alloc_str} | "
                f"收益目标:月{t_min:.0%}~{t_max:.0%}（不可变）"
                + (f" | {progress_text}" if progress_text else "")
            )

        return "\n".join(lines)

    def _get_evolved_prompts(self, db: Session) -> str:
        """策略级进化提示词（复盘产出，自进化层）"""
        from app.models.strategy import Strategy, StrategyEvolvedPrompt

        rows = (
            db.query(StrategyEvolvedPrompt, Strategy.name)
            .join(Strategy, Strategy.id == StrategyEvolvedPrompt.strategy_id)
            .filter(Strategy.status == "active")
            .all()
        )
        if not rows:
            return ""

        parts = []
        for evolved, name in rows:
            parts.append(f"[{evolved.strategy_id}] {name}（v{evolved.version}）:\n{evolved.prompt_text}")
        return "\n\n".join(parts)

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
