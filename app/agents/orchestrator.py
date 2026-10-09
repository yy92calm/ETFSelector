import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Callable, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.config import get_settings
from app.db.database import SessionLocal
from app.agents.technical_analyst import TechnicalAnalystAgent
from app.agents.sentiment_analyst import SentimentAnalystAgent
from app.agents.market_analyst import MarketAnalystAgent
from app.agents.bull_researcher import BullResearcher
from app.agents.bear_researcher import BearResearcher
from app.agents.macro_cycle_agent import MacroCycleAgent
from app.agents.cross_asset_agent import CrossAssetAgent
from app.agents.volatility_regime_agent import VolatilityRegimeAgent
from app.agents.theme_discovery_agent import ThemeDiscoveryAgent
from app.agents.drawdown_attribution_agent import DrawdownAttributionAgent
from app.agents.rebalance_timing_agent import RebalanceTimingAgent
from app.models.strategy import Strategy
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)
settings = get_settings()


class Orchestrator:
    def __init__(self):
        self.technical_analyst = TechnicalAnalystAgent()
        self.sentiment_analyst = SentimentAnalystAgent()
        self.market_analyst = MarketAnalystAgent()
        self.bull_researcher = BullResearcher()
        self.bear_researcher = BearResearcher()
        self.macro_cycle = MacroCycleAgent()
        self.cross_asset = CrossAssetAgent()
        self.volatility_regime = VolatilityRegimeAgent()
        self.theme_discovery = ThemeDiscoveryAgent()
        self.drawdown_attribution = DrawdownAttributionAgent()
        self.rebalance_timing = RebalanceTimingAgent()

    def _run_parallel(self, tasks: List[Tuple[str, Callable[[Session], Dict]]]) -> Dict[str, Dict]:
        """并行执行互不依赖的 Agent 调用，返回 {任务名: 结果}。

        每个任务使用独立 Session —— SQLAlchemy 的 Session 不能跨线程共享，
        直接把主管道 session 交给工作线程会产生并发读写问题。
        单任务异常不抛穿，转成 {"error": ...} 以保持原有的容错语义。
        """
        def invoke(fn: Callable[[Session], Dict]) -> Dict:
            session = SessionLocal()
            try:
                return fn(session)
            except Exception as e:
                logger.warning(f"[Orchestrator] 并行Agent执行异常: {e}")
                return {"error": str(e)}
            finally:
                session.close()

        if len(tasks) == 1:
            name, fn = tasks[0]
            return {name: invoke(fn)}

        results: Dict[str, Dict] = {}
        workers = min(settings.agent_parallel_max_workers, len(tasks))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agent") as pool:
            futures = {pool.submit(invoke, fn): name for name, fn in tasks}
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        return results

    @staticmethod
    def _warn_if_error(label: str, report: Dict) -> None:
        if isinstance(report, dict) and "error" in report:
            logger.warning(f"[Orchestrator] {label}失败: {report.get('error')}")

    def analyze(self, strategy_id: int, analysis_date: date, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}

        etf_codes = list((strategy.allocation_config or {}).keys())

        logger.info(f"[Orchestrator] 策略{strategy_id} 开始多Agent辩论式分析")

        # 方向1: 数据新鲜度检查（过期则先同步再分析）
        freshness = self._ensure_fresh_data(etf_codes, analysis_date, db)
        logger.info(f"[Orchestrator] 数据新鲜度: {freshness['status']} (最新 {freshness['latest_date']}, 滞后 {freshness['lag_days']}天)")

        # 方向4: 快照锁定 - 所有 agent 只读截至 data_lock_date 的数据，防止运行期间错位
        data_lock_date = self._compute_lock_date(etf_codes, analysis_date, db)
        data_date_str = data_lock_date.isoformat() if data_lock_date else "未知"

        # 阶段1 + 阶段1.5: 数据消化与增强分析（五个Agent互不依赖，并行以省串行等待）
        stage1 = self._run_parallel([
            ("technical", lambda s: self.technical_analyst.analyze(
                etf_codes, s, lock_date=data_lock_date)),
            ("sentiment", lambda s: self.sentiment_analyst.analyze(analysis_date, s)),
            ("macro", lambda s: self.macro_cycle.analyze(etf_codes, s)),
            ("cross_asset", lambda s: self.cross_asset.analyze(etf_codes, s)),
            ("volatility", lambda s: self.volatility_regime.analyze(etf_codes, s)),
        ])
        technical_report = stage1["technical"]
        sentiment_report = stage1["sentiment"]
        macro_report = stage1["macro"]
        cross_asset_report = stage1["cross_asset"]
        vol_report = stage1["volatility"]
        self._warn_if_error("技术分析师", technical_report)
        self._warn_if_error("情绪分析师", sentiment_report)
        self._warn_if_error("宏观周期分析", macro_report)
        self._warn_if_error("跨资产分析", cross_asset_report)
        self._warn_if_error("波动率体制分析", vol_report)

        # 阶段2 多空辩论（方向2: 开放工具取数；方向3: 喂入宏观/跨资产/波动率）
        # 与阶段4 辅助决策一并并行：主题发现与再平衡时机只进最终汇总，不参与裁决，无前后依赖
        debate_kwargs = {
            "macro_report": macro_report,
            "cross_asset_report": cross_asset_report,
            "volatility_report": vol_report,
            "data_date": data_date_str,
        }
        stage2 = self._run_parallel([
            ("bull", lambda s: self.bull_researcher.analyze(
                technical_report, sentiment_report, db=s, **debate_kwargs)),
            ("bear", lambda s: self.bear_researcher.analyze(
                technical_report, sentiment_report, db=s, **debate_kwargs)),
            ("theme", lambda s: self.theme_discovery.analyze(etf_codes, s)),
            ("rebalance", lambda s: self.rebalance_timing.analyze(strategy_id, s)),
        ])
        bull_report = stage2["bull"]
        bear_report = stage2["bear"]
        theme_report = stage2["theme"]
        rebalance_report = stage2["rebalance"]
        self._warn_if_error("多头研究员", bull_report)
        self._warn_if_error("空头研究员", bear_report)
        self._warn_if_error("主题发现", theme_report)
        self._warn_if_error("再平衡时机判断", rebalance_report)

        # 月收益目标进度（供裁决Agent提示词使用）
        monthly_status = None
        try:
            from app.services.portfolio_service import get_portfolio_service
            monthly_status = get_portfolio_service().get_monthly_progress(
                strategy_id, db, analysis_date
            )
        except Exception as e:
            logger.warning(f"[Orchestrator] 月目标进度计算失败: {e}")

        # 阶段3: 研究主管裁决
        final_decision = self.market_analyst.analyze(
            strategy_id=strategy_id,
            analysis_date=analysis_date,
            technical_report=technical_report,
            sentiment_report=sentiment_report,
            db=db,
            bull_report=bull_report,
            bear_report=bear_report,
            monthly_target=(monthly_status or {}).get("text", ""),
        )

        combined = {
            "analysis_date": analysis_date.isoformat(),
            "data_freshness": freshness,
            "data_lock_date": data_date_str,
            "technical_report": technical_report,
            "sentiment_report": sentiment_report,
            "macro_report": macro_report,
            "cross_asset_report": cross_asset_report,
            "volatility_report": vol_report,
            "bull_report": bull_report,
            "bear_report": bear_report,
            "theme_report": theme_report,
            "rebalance_timing": rebalance_report,
            "monthly_target_status": monthly_status,
            **final_decision,
        }

        if "error" not in final_decision:
            strategy.last_analysis_result = combined
            strategy.last_auto_analysis_date = analysis_date
            db.commit()
            self._persist_analysis_log(strategy_id, analysis_date, combined, db)
            logger.info(f"[Orchestrator] 策略{strategy_id} 辩论分析完成: {final_decision.get('market_regime')}")

        return combined

    def _persist_analysis_log(self, strategy_id: int, analysis_date: date, combined: Dict, db: Session):
        """将分析结果落库为每日 analyzed 记录（分析Tab数据源）

        按策略+日期去重：同日重复运行（断点续跑/手动重触发）不产生重复记录。
        """
        from app.models.auto_strategy_log import AutoStrategyLog

        try:
            existing = db.query(AutoStrategyLog.id).filter(
                AutoStrategyLog.strategy_id == strategy_id,
                AutoStrategyLog.log_date == analysis_date,
                AutoStrategyLog.action_type == "analyzed",
            ).first()
            if existing:
                db.query(AutoStrategyLog).filter(AutoStrategyLog.id == existing).update(
                    {"analysis_result": combined}
                )
                db.commit()
                logger.info(f"[Orchestrator] {analysis_date} 已有分析记录，更新之")
                return

            db.add(AutoStrategyLog(
                strategy_id=strategy_id,
                log_date=analysis_date,
                status="success",
                action_type="analyzed",
                analysis_result=combined,
            ))
            db.commit()
            logger.info(f"[Orchestrator] {analysis_date} 分析记录已落库")
        except Exception as e:
            db.rollback()
            logger.error(f"[Orchestrator] 分析记录落库失败: {e}")

    def _ensure_fresh_data(self, etf_codes: List[str], analysis_date: date, db: Session) -> Dict:
        """方向1: 检查数据新鲜度，过期先同步。

        返回 {status: fresh|synced|stale, latest_date, lag_days}
        """
        latest = self._latest_quote_date(etf_codes, db)
        if latest is None:
            return {"status": "stale", "latest_date": None, "lag_days": None, "message": "无行情数据"}

        lag_days = (analysis_date - latest).days
        max_lag = settings.debate_max_data_lag_days

        if lag_days <= max_lag:
            return {"status": "fresh", "latest_date": latest.isoformat(), "lag_days": lag_days}

        # 数据滞后，先同步
        logger.warning(f"[Orchestrator] 数据滞后 {lag_days} 天，尝试同步")
        try:
            from app.services.data_service import get_data_service
            svc = get_data_service()
            svc.update_today_quotes(db)
        except Exception as e:
            logger.error(f"[Orchestrator] 数据同步失败: {e}")

        new_latest = self._latest_quote_date(etf_codes, db)
        if new_latest is None:
            return {"status": "stale", "latest_date": latest.isoformat(), "lag_days": lag_days, "message": "同步后仍无数据"}
        new_lag = (analysis_date - new_latest).days
        if new_lag <= max_lag:
            return {"status": "synced", "latest_date": new_latest.isoformat(), "lag_days": new_lag}
        return {"status": "stale", "latest_date": new_latest.isoformat(), "lag_days": new_lag,
                "message": f"数据滞后到 {new_latest.isoformat()}"}

    def _latest_quote_date(self, etf_codes: List[str], db: Session) -> Optional[date]:
        """策略所持 ETF 的最新交易日"""
        if not etf_codes:
            return None
        max_date = (
            db.query(func.max(ETFQuotation.trade_date))
            .filter(ETFQuotation.etf_code.in_(etf_codes))
            .scalar()
        )
        return max_date

    def _compute_lock_date(self, etf_codes: List[str], analysis_date: date, db: Session) -> Optional[date]:
        """方向4: 快照锁定日期 = min(最新交易日, analysis_date)，供各 agent 查询上限"""
        latest = self._latest_quote_date(etf_codes, db)
        if latest is None:
            return analysis_date
        return min(latest, analysis_date)

    def analyze_drawdown(self, strategy_id: int, db: Session) -> Dict:
        return self.drawdown_attribution.analyze(strategy_id, db)
