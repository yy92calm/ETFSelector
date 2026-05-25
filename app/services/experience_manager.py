"""经验生命周期管理"""

import logging
from datetime import date, datetime, timedelta
from typing import List, Dict
from sqlalchemy.orm import Session, object_session

from app.models.experience import Experience, ExperienceUsageRecord
from app.models.portfolio import PortfolioSnapshot

logger = logging.getLogger(__name__)


class ExperienceManager:
    """经验生命周期管理 - 权重衰减、过期清理、有效性验证、智能匹配"""

    LIFECYCLE_CONFIG = {
        "expire_days": 90,
        "weight_decay_rate": 0.1,
        "min_weight": 0.3,
        "validation_threshold": 3,
        "effectiveness_threshold": 6.0,
        "failure_priority_boost": 2.0,
    }
    
    def evaluate_experience_usages(self, strategy_id: int, db: Session):
        """评估待验证的经验应用记录，计算收益和结果"""
        pending_records = db.query(ExperienceUsageRecord).filter(
            ExperienceUsageRecord.strategy_id == strategy_id,
            ExperienceUsageRecord.result == None
        ).all()
        
        logger.info(f"策略{strategy_id}发现{len(pending_records)}条待评估的经验使用记录")
        for rec in pending_records:
            # 找到 usage_date 当日或之前的最后一个快照作为 A_0
            base_snapshot = db.query(PortfolioSnapshot).filter(
                PortfolioSnapshot.strategy_id == strategy_id,
                PortfolioSnapshot.trade_date <= rec.usage_date
            ).order_by(PortfolioSnapshot.trade_date.desc()).first()
            
            if not base_snapshot:
                continue
                
            # 寻找 usage_date 之后的所有快照，按日期升序排列
            future_snapshots = db.query(PortfolioSnapshot).filter(
                PortfolioSnapshot.strategy_id == strategy_id,
                PortfolioSnapshot.trade_date > rec.usage_date
            ).order_by(PortfolioSnapshot.trade_date.asc()).all()
            
            # 如果未来快照数量 >= 5，我们取第 5 个（即 index 4）进行评估
            # 如果未来快照数量 < 5，但当前日期距离 usage_date 已经超过 7 天，我们也取最新的快照进行评估
            if len(future_snapshots) >= 5:
                target_snapshot = future_snapshots[4]
            elif len(future_snapshots) > 0 and (date.today() - rec.usage_date).days >= 7:
                target_snapshot = future_snapshots[-1]
            else:
                # 依然等待更多数据，跳过
                continue
                
            # 计算收益率
            a_0 = base_snapshot.total_asset
            a_t = target_snapshot.total_asset
            if a_0 > 0:
                return_pct = (a_t - a_0) / a_0 * 100
            else:
                return_pct = 0.0
                
            # 结果分类:
            # 收益率 > 0.05% -> positive
            # 收益率 < -0.05% -> negative
            # 其他 -> neutral
            if return_pct > 0.05:
                result = "positive"
            elif return_pct < -0.05:
                result = "negative"
            else:
                result = "neutral"
                
            rec.result = result
            rec.return_pct = round(return_pct, 4)
            rec.is_validated = True
            rec.validated_at = datetime.utcnow()
            
        db.commit()
        logger.info(f"策略{strategy_id}经验使用记录评估完成")
        
    def update_experience_lifecycle(self, strategy_id: int, db: Session):
        """更新经验生命周期"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
        ).all()
        
        for exp in experiences:
            self._apply_weight_decay(exp)
            self._check_expiration(exp)
            self._validate_if_needed(exp)
            self._check_effectiveness(exp)
        
        db.commit()
        logger.info(f"策略{strategy_id}经验生命周期更新完成")
    
    def _apply_weight_decay(self, exp: Experience):
        """权重衰减"""
        days_since = (date.today() - exp.generated_date).days
        months_since = days_since / 30
        
        new_weight = 1.0 - months_since * self.LIFECYCLE_CONFIG["weight_decay_rate"]
        exp.weight = max(self.LIFECYCLE_CONFIG["min_weight"], new_weight)
    
    def _check_expiration(self, exp: Experience):
        """过期检查"""
        if date.today() > exp.expires_date:
            exp.is_active = False
            logger.info(f"经验{exp.id}已过期")
    
    def _validate_if_needed(self, exp: Experience):
        """有效性验证"""
        if exp.application_count >= self.LIFECYCLE_CONFIG["validation_threshold"]:
            self._calculate_effectiveness(exp)
            exp.is_validated = True
    
    def _calculate_effectiveness(self, exp: Experience):
        """计算有效性"""
        records = self._get_usage_records(exp, limit=10)
        
        if len(records) >= 3:
            positive = sum(1 for r in records if r.result == "positive")
            exp.success_rate = positive / len(records)
            exp.effectiveness_score = min(10.0, exp.success_rate * 10)
            
            exp.success_count = positive
            exp.failure_count = len(records) - positive
    
    def _check_effectiveness(self, exp: Experience):
        """检查效果评分"""
        if exp.effectiveness_score < self.LIFECYCLE_CONFIG["effectiveness_threshold"]:
            if exp.is_validated:
                exp.review_status = "pending"
                logger.warning(f"经验{exp.id}效果评分过低: {exp.effectiveness_score}")
    
    def _get_usage_records(self, exp: Experience, limit: int = 10) -> List[ExperienceUsageRecord]:
        """获取使用记录"""
        db = object_session(exp)
        if db:
            return db.query(ExperienceUsageRecord).filter(
                ExperienceUsageRecord.experience_id == exp.id,
                ExperienceUsageRecord.is_validated == True
            ).order_by(ExperienceUsageRecord.usage_date.desc()).limit(limit).all()
        return []
    
    def prune_low_effectiveness(self, strategy_id: int, db: Session) -> int:
        """清理低效经验"""
        low_effect = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.is_validated == True,
            Experience.effectiveness_score < 3.0,
        ).all()
        
        for exp in low_effect:
            exp.is_active = False
        
        db.commit()
        return len(low_effect)
    
    def get_active_experiences_count(self, strategy_id: int, db: Session) -> int:
        """获取活跃经验数量"""
        return db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
        ).count()
    
    def get_experience_stats(self, strategy_id: int, db: Session) -> dict:
        """获取经验统计"""
        total = db.query(Experience).filter(Experience.strategy_id == strategy_id).count()
        active = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
        ).count()
        validated = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_validated == True,
        ).count()
        
        return {
            "total": total,
            "active": active,
            "validated": validated,
            "pending_review": db.query(Experience).filter(
                Experience.strategy_id == strategy_id,
                Experience.review_status == "pending",
            ).count(),
        }
    
    def get_smart_matched_experiences(
        self,
        strategy_id: int,
        current_scenario: Dict,
        db: Session
    ) -> List[Dict]:
        """获取智能匹配的经验"""
        from app.services.smart_experience_matcher import SmartExperienceMatcher
        matcher = SmartExperienceMatcher()
        
        matched = matcher.match_experiences_by_scenario(strategy_id, current_scenario, db)
        
        experiences = [m["experience"] for m in matched]
        conflicts = matcher.detect_experience_conflicts(experiences)
        
        prioritized = matcher.prioritize_failure_experiences(experiences)
        
        return {
            "matched_experiences": matched[:10],
            "conflicts": conflicts,
            "prioritized_experiences": [
                {
                    "id": e.id,
                    "title": e.title,
                    "type": e.experience_type,
                    "key_insight": e.key_insight,
                    "effectiveness_score": e.effectiveness_score,
                } for e in prioritized
            ],
            "total_matched": len(matched),
            "total_conflicts": len(conflicts),
        }
    
    def apply_scenario_based_weight_adjustment(
        self,
        strategy_id: int,
        current_scenario: Dict,
        db: Session
    ) -> Dict:
        """应用场景权重调整"""
        from app.services.smart_experience_matcher import SmartExperienceMatcher
        matcher = SmartExperienceMatcher()
        
        matched = matcher.match_experiences_by_scenario(strategy_id, current_scenario, db)
        
        adjustment_records = []
        for m in matched:
            exp = m["experience"]
            old_weight = exp.weight or 1.0
            new_weight = m["adjusted_weight"]
            
            exp.weight = new_weight
            
            adjustment_records.append({
                "experience_id": exp.id,
                "title": exp.title,
                "old_weight": old_weight,
                "new_weight": new_weight,
                "scenario_similarity": m["scenario_similarity"],
            })
        
        db.commit()
        
        return {
            "adjusted_count": len(adjustment_records),
            "adjustments": adjustment_records,
        }
    
    def boost_failure_experience_weights(self, strategy_id: int, db: Session) -> int:
        """强化失败经验权重"""
        failure_experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.experience_type == "failure",
        ).all()
        
        boosted_count = 0
        for exp in failure_experiences:
            current_weight = exp.weight or 1.0
            boosted_weight = current_weight * self.LIFECYCLE_CONFIG["failure_priority_boost"]
            
            exp.weight = min(2.0, boosted_weight)
            boosted_count += 1
        
        db.commit()
        logger.info(f"强化{boosted_count}条失败经验权重")
        
        return boosted_count