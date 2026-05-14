"""经验生命周期管理"""

import logging
from datetime import date, timedelta
from typing import List
from sqlalchemy.orm import Session

from app.models.experience import Experience, ExperienceUsageRecord

logger = logging.getLogger(__name__)


class ExperienceManager:
    """经验生命周期管理 - 权重衰减、过期清理、有效性验证"""

    LIFECYCLE_CONFIG = {
        "expire_days": 90,
        "weight_decay_rate": 0.1,
        "min_weight": 0.3,
        "validation_threshold": 3,
        "effectiveness_threshold": 6.0,
    }
    
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
        if exp.application_count >= self.LIFECYCLE_CONFIG["validation_threshold"] and not exp.is_validated:
            self._calculate_effectiveness(exp)
            exp.is_validated = True
    
    def _calculate_effectiveness(self, exp: Experience):
        """计算有效性"""
        records = self._get_usage_records(exp.id, limit=10)
        
        if len(records) >= 3:
            positive = sum(1 for r in records if r.result == "positive")
            exp.success_rate = positive / len(records)
            exp.effectiveness_score = min(10, exp.success_rate * 10)
            
            exp.success_count = positive
            exp.failure_count = len(records) - positive
    
    def _check_effectiveness(self, exp: Experience):
        """检查效果评分"""
        if exp.effectiveness_score < self.LIFECYCLE_CONFIG["effectiveness_threshold"]:
            if exp.is_validated:
                exp.review_status = "pending"
                logger.warning(f"经验{exp.id}效果评分过低: {exp.effectiveness_score}")
    
    def _get_usage_records(self, experience_id: int, limit: int = 10) -> List[ExperienceUsageRecord]:
        """获取使用记录"""
        pass
    
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