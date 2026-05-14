"""自动策略执行器"""

import logging
from datetime import date
from typing import Dict
from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.models.auto_strategy_log import AutoStrategyLog
from app.models.experience import Experience, ExperienceUsageRecord

logger = logging.getLogger(__name__)


class AutoStrategyExecutor:
    """自动策略执行器 - 基于AI分析结果执行策略调整"""
    
    SAFETY_LIMITS = {
        "max_daily_adjustments": 1,
        "max_allocation_change": 0.10,
        "min_confidence_level": "medium",
    }
    
    def execute_auto_strategy(self, strategy_id: int, execution_date: date, db: Session) -> Dict:
        """执行自动策略"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "failed", "error": "策略不存在"}
        
        if strategy.strategy_source != "auto_generated":
            return {"status": "skipped", "reason": "非自动策略"}
        
        if strategy.auto_strategy_status != "running":
            return {"status": "skipped", "reason": f"策略状态: {strategy.auto_strategy_status}"}
        
        safety_result = self._check_safety_limits(strategy)
        if not safety_result["passed"]:
            self._log_execution(strategy_id, execution_date, "skipped", safety_result, db)
            return {"status": "skipped", "reason": safety_result["reason"]}
        
        analysis = strategy.last_analysis_result
        if not analysis:
            return {"status": "skipped", "reason": "无AI分析结果"}
        
        if analysis.get("suggested_action") == "hold":
            self._log_execution(strategy_id, execution_date, "hold", {"analysis": analysis}, db)
            return {"status": "hold", "reason": "AI建议维持"}
        
        old_allocation = strategy.allocation_config.copy()
        suggested = analysis.get("suggested_allocation", {})
        
        if not suggested:
            return {"status": "skipped", "reason": "无建议配置"}
        
        change_pct = self._calculate_max_change(old_allocation, suggested)
        if change_pct > self.SAFETY_LIMITS["max_allocation_change"]:
            self._log_execution(strategy_id, execution_date, "skipped", {
                "reason": f"配置变化过大({change_pct:.2%})",
                "analysis": analysis,
            }, db)
            return {"status": "skipped", "reason": f"配置变化超过{self.SAFETY_LIMITS['max_allocation_change']:.0%}限制"}
        
        strategy.allocation_config = suggested
        strategy.auto_adjustment_count += 1
        db.commit()
        
        self._log_execution(strategy_id, execution_date, "adjusted", {
            "old_allocation": old_allocation,
            "new_allocation": suggested,
            "analysis": analysis,
            "change_pct": round(change_pct, 4),
        }, db)
        
        self._record_experience_usage(strategy_id, analysis, db)
        
        logger.info(f"策略{strategy_id}调整完成: {change_pct:.2%}")
        
        return {
            "status": "adjusted",
            "old_allocation": old_allocation,
            "new_allocation": suggested,
            "change_pct": round(change_pct, 4),
        }
    
    def _check_safety_limits(self, strategy: Strategy) -> Dict:
        """安全检查"""
        if strategy.auto_adjustment_count >= strategy.max_daily_adjustments:
            return {"passed": False, "reason": "超过每日最大调整次数"}
        
        analysis = strategy.last_analysis_result
        if analysis and analysis.get("confidence_level") == "low":
            return {"passed": False, "reason": "AI信心等级过低"}
        
        return {"passed": True}
    
    def _calculate_max_change(self, old_alloc: Dict, new_alloc: Dict) -> float:
        """计算最大配置变化"""
        max_change = 0
        for code in old_alloc:
            if code in new_alloc:
                change = abs(new_alloc[code] - old_alloc[code])
                max_change = max(max_change, change)
        return max_change
    
    def _log_execution(self, strategy_id: int, log_date: date, action_type: str, 
                       details: Dict, db: Session):
        """记录执行日志"""
        log = AutoStrategyLog(
            strategy_id=strategy_id,
            log_date=log_date,
            status="success" if action_type != "skipped" else "skipped",
            action_type=action_type,
            analysis_result=details.get("analysis"),
            old_allocation=details.get("old_allocation"),
            new_allocation=details.get("new_allocation"),
            allocation_change=details.get("change_pct"),
            safety_reason=details.get("reason"),
        )
        db.add(log)
        db.commit()
    
    def _record_experience_usage(self, strategy_id: int, analysis: Dict, db: Session):
        """记录经验使用"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
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
        """执行所有自动策略"""
        strategies = db.query(Strategy).filter(
            Strategy.strategy_source == "auto_generated",
            Strategy.auto_strategy_status == "running",
        ).all()
        
        results = {"total": len(strategies), "adjusted": 0, "hold": 0, "skipped": 0, "failed": 0}
        
        for strategy in strategies:
            result = self.execute_auto_strategy(strategy.id, execution_date, db)
            status = result.get("status", "failed")
            if status in results:
                results[status] += 1
        
        logger.info(f"自动策略执行完成: {results}")
        return results