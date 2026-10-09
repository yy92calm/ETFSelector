"""上下文构建器 - 为LLM提供系统状态快照

对齐 deepseek-harness 的上下文模式：
- 系统身份与规则留在 system prompt（跨轮字节稳定，保 provider prompt cache）
- 动态状态按命名 section 组装成「每轮快照」，以 user-role 消息注入（不落库）
- 单 section 查询失败不拖垮整体，缺失部分静默跳过
"""

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.utils.trading_calendar import is_trading_day, is_market_open_now, now_cn

logger = logging.getLogger(__name__)

# 大盘代理标的：LLM 需要「指数级别」的坐标系，而不是逐只ETF的涨跌幅
INDEX_ETF_CODES = {
    "510300": "沪深300",
    "510500": "中证500",
    "159915": "创业板指",
}

# 快照里最多展开的策略数
CONTEXT_STRATEGY_LIMIT = 5


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
            ("大盘环境与仓位基准", self._get_index_regime_summary),
            ("持仓盈亏", self._get_holdings_summary),
            ("近期成交", self._get_recent_trades),
            ("仓位与待生效配置", self._get_position_summary),
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

    def _active_strategies(self, db: Session) -> List:
        """快照各 section 共用的策略集（活跃策略）"""
        from app.models.strategy import Strategy

        return db.query(Strategy).filter(Strategy.status == "active").all()

    @staticmethod
    def _truncation_note(total: int) -> str:
        """截断要可见：静默只列前 N 个会让 LLM 以为系统只有 N 个策略"""
        if total <= CONTEXT_STRATEGY_LIMIT:
            return ""
        return f"（共 {total} 个活跃策略，以上仅列前 {CONTEXT_STRATEGY_LIMIT} 个）"

    def _get_strategies_summary(self, db: Session) -> str:
        from app.services.portfolio_service import get_portfolio_service

        strategies = self._active_strategies(db)
        if not strategies:
            return ""

        lines = []
        for s in strategies[:CONTEXT_STRATEGY_LIMIT]:
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

        note = self._truncation_note(len(strategies))
        if note:
            lines.append(note)

        return "\n".join(lines)

    def _get_index_regime_summary(self, db: Session) -> str:
        """主要指数代理的当日/5日涨跌 + 市场状态快照给出的仓位基准"""
        from app.models.etf import ETFQuotation
        from app.models.market_regime import MarketRegimeSnapshot

        lines = []
        latest_date = db.query(func.max(ETFQuotation.trade_date)).scalar()
        if latest_date:
            # 5日涨幅需要 6 个收盘价，日历窗口放宽到 15 天覆盖长假
            rows = (
                db.query(ETFQuotation.etf_code, ETFQuotation.trade_date,
                         ETFQuotation.close_price)
                .filter(ETFQuotation.etf_code.in_(list(INDEX_ETF_CODES)),
                        ETFQuotation.trade_date >= latest_date - timedelta(days=15),
                        ETFQuotation.close_price != None)
                .order_by(ETFQuotation.trade_date.asc())
                .all()
            )
            series = {}
            for code, trade_date, close_price in rows:
                series.setdefault(code, []).append((trade_date, close_price))

            for code, name in INDEX_ETF_CODES.items():
                points = series.get(code, [])
                if not points:
                    continue
                last_change = points[-1][1] / points[-2][1] - 1 if len(points) >= 2 else None
                five_change = (points[-1][1] / points[-6][1] - 1
                               if len(points) >= 6 else None)
                day_text = f"当日{last_change:+.2%}" if last_change is not None else "当日—"
                five_text = f"5日{five_change:+.2%}" if five_change is not None else "5日—"
                lines.append(f"- {name}({code}) {day_text} {five_text}")

        regime = (
            db.query(MarketRegimeSnapshot)
            .order_by(MarketRegimeSnapshot.trade_date.desc())
            .first()
        )
        if regime:
            equity = regime.suggested_equity_range or []
            equity_text = (f" 建议权益仓位:{equity[0]:.0%}~{equity[1]:.0%}"
                           if len(equity) >= 2 else "")
            lines.append(
                f"- 市场状态({regime.trade_date}): {regime.state_label or '未知'}"
                f" | 风险偏好:{regime.risk_label or '未知'}"
                f"{equity_text} | 分化度:{regime.dispersion_label or '未知'}"
            )

        if lines:
            return "\n".join(lines)
        # 完全没有行情数据（全新安装）时留空，由「系统刚初始化」兜底语说明；
        # 有行情但指数代理/状态快照缺失才显式标注，否则 LLM 会以为大盘坐标被主动省略而忽略仓位基准
        return "暂无指数行情与市场状态快照" if latest_date else ""

    def _get_holdings_summary(self, db: Session) -> str:
        """每只持仓的成本/现价/浮盈与最短持有期剩余（LLM 需知道能不能换）"""
        from app.models.portfolio import Holding, TradeRecord
        from app.services.rotation_service import MIN_HOLD_DAYS

        strategies = self._active_strategies(db)[:CONTEXT_STRATEGY_LIMIT]
        if not strategies:
            return ""

        strategy_ids = [s.id for s in strategies]
        today = now_cn().date()
        # 一次取回全部持仓与买入日，按策略分组（逐策略查会让快照随策略数线性增加查询）
        holdings_by_strategy = defaultdict(list)
        for h in db.query(Holding).filter(Holding.strategy_id.in_(strategy_ids)).all():
            holdings_by_strategy[h.strategy_id].append(h)

        last_buys = {
            (r[0], r[1]): r[2]
            for r in db.query(TradeRecord.strategy_id, TradeRecord.etf_code,
                              func.max(TradeRecord.trade_date))
            .filter(TradeRecord.strategy_id.in_(strategy_ids),
                    TradeRecord.direction == "buy")
            .group_by(TradeRecord.strategy_id, TradeRecord.etf_code)
            .all()
        }

        lines = []
        for s in strategies:
            holdings = holdings_by_strategy.get(s.id, [])
            if not holdings:
                lines.append(f"- [{s.id}] {s.name}: 暂无持仓")
                continue
            parts = []
            for h in holdings:
                pnl = ((h.current_price - h.avg_cost) / h.avg_cost * 100
                       if h.avg_cost else 0.0)
                last_buy = last_buys.get((s.id, h.etf_code))
                if last_buy:
                    held_days = (today - last_buy).days
                    locked = max(0, MIN_HOLD_DAYS - held_days)
                    hold_text = f"持{held_days}日" + (f"(还可换{locked}日后)" if locked else "(可换)")
                else:
                    hold_text = "建仓日未知"
                parts.append(f"{h.etf_code} 成本{h.avg_cost:.3f}/现价{h.current_price:.3f} "
                             f"{pnl:+.1f}% {hold_text}")
            lines.append(f"- [{s.id}] {s.name}: " + "; ".join(parts))

        return "\n".join(lines)

    def _get_recent_trades(self, db: Session) -> str:
        """最近 5 笔成交（跨活跃策略），让 LLM 知道刚做了什么、避免重复操作"""
        from app.models.portfolio import TradeRecord

        strategies = self._active_strategies(db)[:CONTEXT_STRATEGY_LIMIT]
        if not strategies:
            return ""

        rows = (
            db.query(TradeRecord)
            .filter(TradeRecord.strategy_id.in_([s.id for s in strategies]))
            .order_by(TradeRecord.trade_date.desc(), TradeRecord.id.desc())
            .limit(5)
            .all()
        )
        if not rows:
            return "暂无成交记录（近期无买卖）"

        lines = []
        for r in rows:
            direction = "买入" if r.direction == "buy" else "卖出"
            reason = f" | {(r.reason or '')[:30]}" if r.reason else ""
            lines.append(f"- {r.trade_date} [{r.strategy_id}] {r.etf_code} {direction} "
                         f"{r.quantity}股 @{r.price:.3f} 金额{r.amount:,.0f}{reason}")
        return "\n".join(lines)

    def _get_position_summary(self, db: Session) -> str:
        """目标权重合计、待生效配置、现金利用率（LLM 常误判「还有多少子弹」）"""
        from app.models.portfolio import PortfolioSnapshot

        strategies = self._active_strategies(db)[:CONTEXT_STRATEGY_LIMIT]
        if not strategies:
            return ""

        strategy_ids = [s.id for s in strategies]
        # 每个策略的最新一条组合快照（单次分组查询，不逐策略查）
        latest_dates = (
            db.query(PortfolioSnapshot.strategy_id,
                     func.max(PortfolioSnapshot.trade_date).label("latest"))
            .filter(PortfolioSnapshot.strategy_id.in_(strategy_ids))
            .group_by(PortfolioSnapshot.strategy_id)
            .subquery()
        )
        snapshots = {
            snap.strategy_id: snap
            for snap in db.query(PortfolioSnapshot).join(
                latest_dates,
                (PortfolioSnapshot.strategy_id == latest_dates.c.strategy_id)
                & (PortfolioSnapshot.trade_date == latest_dates.c.latest),
            ).all()
        }

        lines = []
        for s in strategies:
            alloc = s.allocation_config or {}
            total = sum(alloc.values())
            text = f"- [{s.id}] {s.name} 目标权重合计:{total:.0%}"

            pending = s.pending_allocation or {}
            if pending:
                pending_str = ", ".join(f"{k}:{v:.0%}" for k, v in pending.items())
                text += f" | 待生效:{pending_str}"
            else:
                text += " | 无待生效配置"

            snap = snapshots.get(s.id)
            if snap and snap.total_asset:
                cash_ratio = snap.cash / snap.total_asset
                text += (f" | 现金{cash_ratio:.0%}（已用{1 - cash_ratio:.0%}）"
                         f" 市值{snap.market_value:,.0f}/{snap.total_asset:,.0f}"
                         f" 累计{snap.profit_pct:+.2f}% @{snap.trade_date}")
            else:
                text += " | 组合快照暂无数据"
            lines.append(text)

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
