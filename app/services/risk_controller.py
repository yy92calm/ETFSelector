"""风险控制服务"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.models.strategy import Strategy
from app.models.portfolio import PortfolioSnapshot
from app.models.auto_strategy_log import AutoStrategyLog

logger = logging.getLogger(__name__)


class RiskController:
    """风险控制器 - 熔断、回撤保护、风险预算、压力测试"""
    
    RISK_CONFIG = {
        "circuit_breaker": {
            "consecutive_loss_threshold": 3,
            "single_day_loss_threshold": -0.03,
            "total_loss_threshold": -0.10,
            "cooldown_days": 3,
        },
        "drawdown_protection": {
            "warning_level": -0.05,
            "action_level": -0.08,
            "critical_level": -0.12,
            "reduce_ratio_at_critical": 0.5,
        },
        "risk_budget": {
            "max_single_position": 0.30,
            "max_sector_concentration": 0.40,
            "volatility_budget": 0.15,
        },
    }
    
    def check_circuit_breaker(self, strategy_id: int, db: Session) -> Dict:
        """检查熔断条件"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        
        config = self.RISK_CONFIG["circuit_breaker"]
        
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id
        ).order_by(PortfolioSnapshot.trade_date.desc()).limit(10).all()
        
        if len(snapshots) < 2:
            return {"status": "normal", "message": "数据不足，无需熔断"}
        
        consecutive_losses = 0
        for i in range(min(len(snapshots)-1, 5)):
            if snapshots[i].profit_pct and snapshots[i].profit_pct < 0:
                consecutive_losses += 1
            else:
                break
        
        if consecutive_losses >= config["consecutive_loss_threshold"]:
            return {
                "status": "triggered",
                "type": "consecutive_loss",
                "reason": f"连续{consecutive_losses}次亏损",
                "action": "pause_strategy",
                "cooldown_days": config["cooldown_days"],
            }
        
        latest_snapshot = snapshots[0]
        if latest_snapshot.profit_pct and latest_snapshot.profit_pct < config["single_day_loss_threshold"]:
            return {
                "status": "triggered",
                "type": "single_day_loss",
                "reason": f"单日亏损{latest_snapshot.profit_pct:.2%}",
                "action": "pause_strategy",
                "cooldown_days": config["cooldown_days"],
            }
        
        if latest_snapshot.total_asset and strategy.initial_capital:
            total_return = (latest_snapshot.total_asset - strategy.initial_capital) / strategy.initial_capital
            if total_return < config["total_loss_threshold"]:
                return {
                    "status": "triggered",
                    "type": "total_loss",
                    "reason": f"累计亏损{total_return:.2%}",
                    "action": "emergency_stop",
                    "cooldown_days": 7,
                }
        
        return {"status": "normal", "message": "未触发熔断条件"}
    
    def apply_drawdown_protection(self, strategy_id: int, db: Session) -> Dict:
        """应用回撤保护"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        
        config = self.RISK_CONFIG["drawdown_protection"]
        
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id
        ).order_by(PortfolioSnapshot.trade_date.desc()).limit(60).all()
        
        if len(snapshots) < 5:
            return {"status": "normal", "message": "数据不足"}
        
        peak_value = max(s.total_asset for s in snapshots if s.total_asset)
        current_value = snapshots[0].total_asset
        
        drawdown = (peak_value - current_value) / peak_value if peak_value > 0 else 0
        
        if drawdown > abs(config["critical_level"]):
            allocation = strategy.allocation_config or {}
            reduced_allocation = {}
            
            for code, ratio in allocation.items():
                reduced_allocation[code] = ratio * config["reduce_ratio_at_critical"]
            
            return {
                "status": "critical",
                "drawdown_pct": round(drawdown * 100, 2),
                "peak_value": round(peak_value, 2),
                "current_value": round(current_value, 2),
                "action": "reduce_position",
                "suggested_allocation": reduced_allocation,
                "message": f"回撤达到临界水平{drawdown:.2%}，建议降低仓位至{config['reduce_ratio_at_critical']:.0%}",
            }
        
        elif drawdown > abs(config["action_level"]):
            return {
                "status": "warning",
                "drawdown_pct": round(drawdown * 100, 2),
                "action": "monitor_closely",
                "message": f"回撤接近警戒水平{drawdown:.2%}，密切关注",
            }
        
        elif drawdown > abs(config["warning_level"]):
            return {
                "status": "alert",
                "drawdown_pct": round(drawdown * 100, 2),
                "action": "prepare_defense",
                "message": f"回撤达到预警水平{drawdown:.2%}，准备防御措施",
            }
        
        return {
            "status": "normal",
            "drawdown_pct": round(drawdown * 100, 2),
            "message": "回撤在安全范围内",
        }
    
    def check_risk_budget(self, strategy_id: int, db: Session) -> Dict:
        """检查风险预算"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        
        config = self.RISK_CONFIG["risk_budget"]
        allocation = strategy.allocation_config or {}
        
        violations = []
        
        max_position = max(allocation.values()) if allocation else 0
        if max_position > config["max_single_position"]:
            violations.append({
                "type": "single_position_exceeded",
                "current": round(max_position, 2),
                "limit": config["max_single_position"],
                "message": f"单一持仓{max_position:.2%}超过限制{config['max_single_position']:.2%}",
            })
        
        if violations:
            return {
                "status": "violation",
                "violations": violations,
                "suggested_action": "rebalance_to_limit",
            }
        
        return {
            "status": "compliant",
            "max_single_position": round(max_position, 2),
            "message": "风险预算合规",
        }
    
    def run_stress_test(self, strategy_id: int, db: Session) -> Dict:
        """运行压力测试"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"status": "error", "message": "策略不存在"}
        
        allocation = strategy.allocation_config or {}
        
        scenarios = [
            {"name": "市场暴跌", "shock": -0.20, "description": "模拟市场整体下跌20%"},
            {"name": "金融危机", "shock": -0.30, "description": "模拟极端金融危机场景"},
            {"name": "流动性危机", "shock": -0.15, "description": "模拟流动性枯竭"},
            {"name": "政策冲击", "shock": -0.10, "description": "模拟政策重大变化"},
        ]
        
        initial_capital = strategy.initial_capital or 100000
        
        results = []
        for scenario in scenarios:
            shock_pct = scenario["shock"]
            
            estimated_loss = initial_capital * shock_pct
            estimated_final_value = initial_capital + estimated_loss
            
            severity = "critical" if abs(shock_pct) > 0.25 else "severe" if abs(shock_pct) > 0.15 else "moderate"
            
            results.append({
                "scenario": scenario["name"],
                "description": scenario["description"],
                "shock_pct": shock_pct,
                "estimated_loss": round(estimated_loss, 2),
                "estimated_final_value": round(estimated_final_value, 2),
                "severity": severity,
                "recovery_time_estimate": self._estimate_recovery_time(abs(shock_pct)),
            })
        
        worst_case = min(results, key=lambda x: x["estimated_final_value"])
        
        return {
            "status": "completed",
            "scenarios": results,
            "worst_case": worst_case,
            "risk_assessment": self._generate_risk_assessment(worst_case),
            "recommendations": self._generate_risk_recommendations(results),
        }
    
    def _estimate_recovery_time(self, loss_pct: float) -> str:
        """估算恢复时间"""
        avg_monthly_return = 0.02
        
        months_needed = abs(loss_pct) / avg_monthly_return
        
        if months_needed < 3:
            return "1-3个月"
        elif months_needed < 6:
            return "3-6个月"
        elif months_needed < 12:
            return "6-12个月"
        else:
            return "超过1年"
    
    def _generate_risk_assessment(self, worst_case: Dict) -> str:
        """生成风险评估"""
        severity = worst_case["severity"]
        
        if severity == "critical":
            return f"极端风险: {worst_case['scenario']}下可能损失{abs(worst_case['shock_pct']):.0%}，建议立即优化配置"
        elif severity == "severe":
            return f"高风险: {worst_case['scenario']}下可能损失{abs(worst_case['shock_pct']):.0%}，需加强风险防范"
        else:
            return f"中等风险: 当前配置在压力测试下表现稳健"
    
    def _generate_risk_recommendations(self, results: List[Dict]) -> List[str]:
        """生成风险建议"""
        recommendations = []
        
        critical_scenarios = [r for r in results if r["severity"] == "critical"]
        if critical_scenarios:
            recommendations.append("建议降低整体仓位，应对极端场景")
        
        severe_scenarios = [r for r in results if r["severity"] == "severe"]
        if severe_scenarios:
            recommendations.append("建议增加避险资产配置（如债券ETF）")
        
        long_recovery = [r for r in results if "超过1年" in r["recovery_time_estimate"]]
        if long_recovery:
            recommendations.append("建议设置止损线，控制最大损失")
        
        if not recommendations:
            recommendations.append("当前风险水平适中，建议维持现状")
        
        return recommendations
    
    def get_risk_dashboard(self, strategy_id: int, db: Session) -> Dict:
        """获取风险仪表盘"""
        circuit_breaker = self.check_circuit_breaker(strategy_id, db)
        drawdown = self.apply_drawdown_protection(strategy_id, db)
        budget = self.check_risk_budget(strategy_id, db)
        stress_test = self.run_stress_test(strategy_id, db)
        
        overall_risk_level = self._calculate_overall_risk_level(
            circuit_breaker, drawdown, budget
        )
        
        return {
            "strategy_id": strategy_id,
            "overall_risk_level": overall_risk_level,
            "circuit_breaker": circuit_breaker,
            "drawdown_protection": drawdown,
            "risk_budget": budget,
            "stress_test_summary": {
                "worst_case": stress_test["worst_case"],
                "risk_assessment": stress_test["risk_assessment"],
            },
            "risk_alerts": self._collect_risk_alerts(circuit_breaker, drawdown, budget),
            "timestamp": date.today().isoformat(),
        }
    
    def _calculate_overall_risk_level(
        self,
        circuit_breaker: Dict,
        drawdown: Dict,
        budget: Dict
    ) -> str:
        """计算整体风险等级"""
        risk_score = 0
        
        if circuit_breaker.get("status") == "triggered":
            risk_score += 3
        
        if drawdown.get("status") == "critical":
            risk_score += 3
        elif drawdown.get("status") == "warning":
            risk_score += 2
        elif drawdown.get("status") == "alert":
            risk_score += 1
        
        if budget.get("status") == "violation":
            risk_score += 1
        
        if risk_score >= 5:
            return "critical"
        elif risk_score >= 3:
            return "high"
        elif risk_score >= 1:
            return "medium"
        else:
            return "low"
    
    def _collect_risk_alerts(
        self,
        circuit_breaker: Dict,
        drawdown: Dict,
        budget: Dict
    ) -> List[str]:
        """收集风险警报"""
        alerts = []
        
        if circuit_breaker.get("status") == "triggered":
            alerts.append(f"⚠️ 熔断触发: {circuit_breaker['reason']}")
        
        if drawdown.get("status") in ["critical", "warning", "alert"]:
            alerts.append(f"📉 回撤警报: {drawdown['message']}")
        
        if budget.get("status") == "violation":
            for v in budget.get("violations", []):
                alerts.append(f"⚠️ {v['message']}")
        
        return alerts
    
    def should_pause_strategy(self, strategy_id: int, db: Session) -> bool:
        """判断是否应暂停策略"""
        circuit_breaker = self.check_circuit_breaker(strategy_id, db)
        
        if circuit_breaker.get("status") == "triggered":
            strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
            if strategy and strategy.auto_strategy_status == "running":
                strategy.auto_strategy_status = "paused"
                strategy.paused_reason = circuit_breaker.get("reason")
                strategy.paused_date = date.today()
                db.commit()
                
                logger.warning(f"策略{strategy_id}因{circuit_breaker['reason']}已暂停")
                return True
        
        return False