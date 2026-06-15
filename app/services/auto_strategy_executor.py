"""自动策略执行器"""

import logging
from datetime import date
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.experience import Experience, ExperienceUsageRecord
from app.models.etf import ETFBasic, ETFQuotation

logger = logging.getLogger(__name__)


class AutoStrategyExecutor:
    """自动策略执行器 - 基于AI分析结果执行策略调整"""

    SAFETY_LIMITS = {
        "max_daily_adjustments": 1,
        "max_allocation_change": 0.10,
        "min_confidence_level": "medium",
    }

    def execute_full_pipeline(
        self,
        strategy_id: int,
        execution_date: date,
        db: Session,
        skip_llm: bool = False,
    ) -> Dict:
        """串行执行 AI 自驱动全管道：风险检查 → AI分析 → ETF验证 → 配置变更 → 交易执行

        Args:
            strategy_id: 策略ID
            execution_date: 执行日期
            db: 数据库会话
            skip_llm: 仅用于测试时跳过LLM调用

        Returns:
            Dict 包含各阶段状态和最终结果
        """
        pipeline = {"stages": [], "status": "running", "overall_message": ""}

        # ---------- 阶段 1：策略状态验证 ----------
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"stages": [], "status": "failed", "overall_message": "策略不存在"}
        if strategy.strategy_source != "auto_generated":
            return {"stages": [{"stage": "validate", "status": "skipped"}], "status": "skipped", "overall_message": "非自动策略"}
        if strategy.auto_strategy_status != "running":
            return {"stages": [{"stage": "validate", "status": "skipped"}], "status": "skipped", "overall_message": f"策略状态: {strategy.auto_strategy_status}"}

        pipeline["stages"].append({"stage": "validate", "status": "passed"})

        # ---------- 阶段 2：每日调整次数检查 ----------
        if strategy.auto_adjustment_count >= strategy.max_daily_adjustments:
            msg = "超过每日最大调整次数"
            self._log_execution(strategy_id, execution_date, "skipped", {"reason": msg}, db)
            pipeline["stages"].append({"stage": "safety_limit", "status": "skipped", "reason": msg})
            pipeline["status"] = "skipped"
            pipeline["overall_message"] = msg
            return pipeline

        pipeline["stages"].append({"stage": "safety_limit", "status": "passed"})

        # ---------- 阶段 3：风险检查（熔断 + 回撤保护） ----------
        risk_check = self._run_risk_checks(strategy_id, db)
        pipeline["stages"].append(risk_check)
        if risk_check.get("status") in ("triggered", "critical"):
            self._log_execution(strategy_id, execution_date, "skipped", {
                "reason": f"风险检查未通过: {risk_check.get('reason', '')}",
            }, db)
            pipeline["status"] = "skipped"
            pipeline["overall_message"] = risk_check.get("reason", "风险检查未通过")
            return pipeline

        # ---------- 阶段 4：AI 市场分析 ----------
        analysis_stage = self._run_analysis(strategy_id, execution_date, db, skip_llm)
        pipeline["stages"].append(analysis_stage)
        if analysis_stage.get("status") == "failed":
            pipeline["status"] = "failed"
            pipeline["overall_message"] = analysis_stage.get("reason", "AI分析失败")
            return pipeline

        analysis = analysis_stage.get("analysis", {})
        if analysis.get("suggested_action") == "hold":
            self._log_execution(strategy_id, execution_date, "hold", {"analysis": analysis}, db)
            pipeline["stages"].append({"stage": "hold", "status": "hold", "reason": "AI建议维持"})
            pipeline["status"] = "hold"
            pipeline["overall_message"] = "AI建议维持"
            return pipeline

        # ---------- 阶段 5：ETF 代码验证 ----------
        suggested = analysis.get("suggested_allocation", {})
        if not suggested:
            pipeline["stages"].append({"stage": "validate_etf", "status": "skipped", "reason": "无建议配置"})
            pipeline["status"] = "skipped"
            pipeline["overall_message"] = "无建议配置"
            return pipeline

        validation = self._validate_etf_codes(suggested, db)
        pipeline["stages"].append(validation["stage"])
        if not validation["passed"]:
            self._log_execution(strategy_id, execution_date, "skipped", {
                "reason": f"ETF验证失败: {validation.get('reason', '')}",
                "analysis": analysis,
            }, db)
            pipeline["status"] = "skipped"
            pipeline["overall_message"] = validation.get("reason", "ETF验证未通过")
            return pipeline

        # ---------- 阶段 6：配置变化检查 ----------
        old_allocation = strategy.allocation_config.copy()
        change_pct = self._calculate_max_change(old_allocation, suggested)

        if change_pct > self.SAFETY_LIMITS["max_allocation_change"] or (
            change_pct == 0 and set(suggested.keys()) == set(old_allocation.keys())
        ):
            msg = f"配置变化过大({change_pct:.2%})" if change_pct > 0.001 else "配置无变化"
            self._log_execution(strategy_id, execution_date, "skipped", {
                "reason": msg, "analysis": analysis,
            }, db)
            pipeline["stages"].append({"stage": "check_change", "status": "skipped", "reason": msg})
            pipeline["status"] = "skipped"
            pipeline["overall_message"] = msg
            return pipeline

        pipeline["stages"].append({"stage": "check_change", "status": "passed", "change_pct": round(change_pct, 4)})

        # ---------- 阶段 7：写入配置 + 交易执行（事务保护）----------
        old_allocation_copy = strategy.allocation_config.copy()
        old_adjustment_count = strategy.auto_adjustment_count

        strategy.allocation_config = suggested
        strategy.auto_adjustment_count += 1
        strategy.last_auto_analysis_date = execution_date

        try:
            trade_result = self._execute_trades(strategy, execution_date, db)
            pipeline["stages"].append(trade_result)

            if trade_result.get("status") == "failed":
                raise RuntimeError(trade_result.get("reason", "交易执行失败"))

            db.commit()

            from app.memory.memory_log import MemoryLog
            mem = MemoryLog(strategy_id)
            mem.record_decision(analysis)

            self._log_execution(strategy_id, execution_date, "adjusted", {
                "old_allocation": old_allocation,
                "new_allocation": suggested,
                "analysis": analysis,
                "change_pct": round(change_pct, 4),
            }, db)

            self._record_experience_usage(strategy_id, analysis, db)

            pipeline["status"] = "adjusted"
            pipeline["overall_message"] = f"策略调整完成，变化幅度{change_pct:.2%}"
            pipeline["old_allocation"] = old_allocation
            pipeline["new_allocation"] = suggested
            pipeline["change_pct"] = round(change_pct, 4)

            logger.info(f"全管道执行完成 策略{strategy_id}: {pipeline['overall_message']}")
        except Exception as e:
            strategy.allocation_config = old_allocation_copy
            strategy.auto_adjustment_count = old_adjustment_count
            strategy.last_auto_analysis_date = None

            db.rollback()

            self._log_execution(strategy_id, execution_date, "failed", {
                "reason": str(e), "analysis": analysis,
            }, db)

            pipeline["stages"].append({"stage": "commit", "status": "rolled_back", "reason": str(e)})
            pipeline["status"] = "failed"
            pipeline["overall_message"] = f"交易执行失败: {str(e)}"

            logger.error(f"全管道执行失败 策略{strategy_id}: {e}")
            return pipeline

        return pipeline

    # ---------- 内部方法 ----------

    def _run_risk_checks(self, strategy_id: int, db: Session) -> Dict:
        """执行风险检查（三方风控辩论）"""
        from app.agents.risk_agents.risk_debate_orchestrator import RiskDebateOrchestrator
        from app.services.risk_controller import RiskController

        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        market_regime = "unknown"
        if strategy and strategy.last_analysis_result:
            market_regime = strategy.last_analysis_result.get("market_regime", "unknown")

        debate = RiskDebateOrchestrator()
        result = debate.evaluate(strategy_id, market_regime, db)

        if result.get("status") in ("triggered", "critical"):
            return result

        if result.get("status") == "passed":
            return result

        ctrl = RiskController()
        cb = ctrl.check_circuit_breaker(strategy_id, db)
        if cb["status"] == "triggered":
            return {"stage": "risk_check", "status": "triggered",
                    "reason": f"熔断触发: {cb.get('reason', '')}",
                    "action": cb.get("action", "pause_strategy")}

        dd = ctrl.apply_drawdown_protection(strategy_id, db)
        if dd["status"] == "critical":
            return {"stage": "risk_check", "status": "critical",
                    "reason": f"回撤临界: {dd.get('message', '')}",
                    "action": "reduce_position",
                    "suggested_allocation": dd.get("suggested_allocation")}

        if dd["status"] == "warning":
            logger.warning(f"策略{strategy_id} 回撤预警: {dd.get('message', '')}")

        return {"stage": "risk_check", "status": "passed"}

    def _run_analysis(self, strategy_id: int, execution_date: date, db: Session, skip_llm: bool) -> Dict:
        """运行 AI 市场分析（多Agent协作版）"""
        from app.agents.orchestrator import Orchestrator

        if skip_llm:
            strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
            if strategy and strategy.last_analysis_result:
                return {"stage": "analysis", "status": "passed", "analysis": strategy.last_analysis_result}
            return {"stage": "analysis", "status": "failed", "reason": "skip_llm=True 且无缓存结果"}

        orchestrator = Orchestrator()
        result = orchestrator.analyze(strategy_id, execution_date, db)

        if "error" in result:
            return {"stage": "analysis", "status": "failed", "reason": result["error"]}

        return {"stage": "analysis", "status": "passed", "analysis": result}

    def _validate_etf_codes(self, allocation: Dict[str, float], db: Session) -> Dict:
        """验证建议配置中的所有 ETF 代码：
        - 在 etf_basic 表中存在
        - 近期有行情数据
        """
        codes = list(allocation.keys())
        if not codes:
            return {"passed": False, "reason": "建议配置为空", "stage": {"stage": "validate_etf", "status": "failed"}}

        # 检查存在性
        existing = set(
            r[0] for r in db.query(ETFBasic.etf_code)
            .filter(ETFBasic.etf_code.in_(codes))
            .all()
        )

        missing = [c for c in codes if c not in existing]
        if missing:
            return {
                "passed": False,
                "reason": f"以下ETF代码不存在于数据库: {', '.join(missing)}",
                "stage": {"stage": "validate_etf", "status": "failed", "invalid_codes": missing},
            }

        # 检查近期行情（最近30天）
        from datetime import timedelta
        cutoff = date.today() - timedelta(days=30)
        with_quotes = set(
            r[0] for r in db.query(ETFQuotation.etf_code)
            .filter(
                ETFQuotation.etf_code.in_(codes),
                ETFQuotation.trade_date >= cutoff,
            )
            .distinct()
            .all()
        )

        no_quotes = [c for c in codes if c not in with_quotes]
        if no_quotes:
            return {
                "passed": False,
                "reason": f"以下ETF近30天无行情数据: {', '.join(no_quotes)}",
                "stage": {"stage": "validate_etf", "status": "failed", "no_quotes_codes": no_quotes},
            }

        return {"passed": True, "stage": {"stage": "validate_etf", "status": "passed"}}

    def _execute_trades(self, strategy: Strategy, execution_date: date, db: Session) -> Dict:
        """执行再平衡交易（不处理回滚，由外层事务管理）"""
        from app.services.portfolio_service import get_portfolio_service

        try:
            svc = get_portfolio_service()
            svc.run_strategy_for_date(strategy, execution_date, db)
            return {"stage": "trade_execution", "status": "passed"}
        except Exception as e:
            logger.error(f"策略{strategy.id} 交易执行失败: {e}")
            return {"stage": "trade_execution", "status": "failed", "reason": str(e)}

    # ---------- 下方保留原有方法，供外部 API 兼容使用 ----------

    def execute_auto_strategy(self, strategy_id: int, execution_date: date, db: Session) -> Dict:
        """执行自动策略（老接口，保留兼容性）"""
        return self.execute_full_pipeline(strategy_id, execution_date, db)

    def _check_safety_limits(self, strategy: Strategy) -> Dict:
        """安全检查（保留供外部调用）"""
        if strategy.auto_adjustment_count >= strategy.max_daily_adjustments:
            return {"passed": False, "reason": "超过每日最大调整次数"}
        analysis = strategy.last_analysis_result
        if analysis and analysis.get("confidence_level") == "low":
            return {"passed": False, "reason": "AI信心等级过低"}
        return {"passed": True}

    def _calculate_max_change(self, old_alloc: Dict, new_alloc: Dict) -> float:
        """计算最大配置变化"""
        all_codes = set(old_alloc.keys()) | set(new_alloc.keys())
        max_change = 0.0
        for code in all_codes:
            old_val = old_alloc.get(code, 0.0)
            new_val = new_alloc.get(code, 0.0)
            change = abs(new_val - old_val)
            if change > max_change:
                max_change = change
        return max_change

    def _log_execution(self, strategy_id: int, log_date: date, action_type: str,
                       details: Dict, db: Session):
        """记录执行日志"""
        log = AutoStrategyLog(
            strategy_id=strategy_id,
            log_date=log_date,
            status="success" if action_type not in ("skipped", "failed") else action_type,
            action_type=action_type,
            analysis_result=details.get("analysis"),
            old_allocation=details.get("old_allocation"),
            new_allocation=details.get("new_allocation"),
            allocation_change=details.get("change_pct"),
            safety_reason=details.get("reason"),
            safety_check_passed=details.get("safety_check_passed", True),
        )
        db.add(log)
        db.commit()

    def _record_experience_usage(self, strategy_id: int, analysis: Dict, db: Session):
        """记录经验使用"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.expires_date >= date.today(),
        ).limit(5).all()

        for exp in experiences:
            exp.application_count += 1
            usage = ExperienceUsageRecord(
                experience_id=exp.id,
                strategy_id=strategy_id,
                usage_date=date.today(),
                decision_made=analysis.get("suggested_allocation"),
            )
            db.add(usage)
        db.commit()

    def run_all_auto_strategies(self, execution_date: date, db: Session) -> Dict:
        """执行所有自动策略（跑全管道）"""
        strategies = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).all()

        results = {"total": len(strategies), "adjusted": 0, "hold": 0, "skipped": 0, "failed": 0}

        for strategy in strategies:
            result = self.execute_full_pipeline(strategy.id, execution_date, db)
            status = result.get("status", "failed")
            if status in results:
                results[status] += 1

        logger.info(f"自动策略执行完成: {results}")
        return results
