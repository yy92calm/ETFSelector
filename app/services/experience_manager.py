"""经验生命周期管理"""

import logging
import math
from datetime import date, datetime, timedelta
from typing import List, Dict
from sqlalchemy.orm import Session, object_session

from app.config import get_settings
from app.models.experience import Experience, ExperienceUsageRecord
from app.models.portfolio import PortfolioSnapshot

logger = logging.getLogger(__name__)


class ExperienceManager:
    """经验生命周期管理 - 权重衰减、过期清理、有效性验证、智能匹配"""

    LIFECYCLE_CONFIG = {
        "expire_days": 90,
        "min_weight": 0.3,
        "max_weight": 2.0,
        "validation_threshold": 3,
        "effectiveness_threshold": 6.0,
        "prune_effectiveness_threshold": 3.0,
        "failure_priority_boost": 2.0,
    }

    # 权重写入优先级：低优先级的规则不得覆盖高优先级刚算出的结果
    WEIGHT_SOURCE_RANK = {"decay": 1, "scenario": 2, "boost": 3}
    
    def record_usage(self, strategy_id: int, db: Session, experience_ids: List[int] = None,
                     decision: Dict = None, source: str = "explicit",
                     market_condition: Dict = None) -> int:
        """记录经验应用（当日幂等）：写 ExperienceUsageRecord 并累加 application_count

        source: explicit=决策时显式查阅（工具返回的经验 id）/ implicit=按活跃经验兜底记录
        返回新写入的记录数。
        """
        from app.models.experience import Experience as _Exp

        ids = [int(i) for i in (experience_ids or []) if i is not None]
        if not ids:
            ids = [
                e.id for e in db.query(_Exp).filter(
                    _Exp.strategy_id == strategy_id,
                    _Exp.is_active == True,  # noqa: E712
                    _Exp.expires_date >= date.today(),
                ).order_by(_Exp.weight.desc()).limit(5).all()
            ]
            source = "implicit"
        if not ids:
            return 0

        today = date.today()
        recorded = 0
        for exp_id in ids:
            exists = db.query(ExperienceUsageRecord).filter(
                ExperienceUsageRecord.experience_id == exp_id,
                ExperienceUsageRecord.strategy_id == strategy_id,
                ExperienceUsageRecord.usage_date == today,
            ).first()
            if exists:
                continue
            db.add(ExperienceUsageRecord(
                experience_id=exp_id,
                strategy_id=strategy_id,
                usage_date=today,
                market_condition=market_condition,
                decision_made={"source": source, **(decision or {})},
            ))
            exp = db.query(_Exp).filter(_Exp.id == exp_id).first()
            if exp:
                exp.application_count = (exp.application_count or 0) + 1
            recorded += 1

        if recorded:
            db.commit()
            logger.info(f"[经验] 策略{strategy_id} 记录应用 {recorded} 条（{source}）")
        return recorded

    def evaluate_experience_usages(self, strategy_id: int, db: Session):
        """评估待验证的经验应用记录，计算收益和结果

        注意：此处计算的 return_pct 反映的是经验应用后组合的整体表现，
        属于"关联性"而非严格的"因果性"——组合收益受市场环境与所有决策共同影响。
        effectiveness_score 应作为辅助参考，不宜单独作为经验去留的决定性依据。
        """
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
            self._set_weight(exp, self._decay_weight(exp), "decay")
            self._check_expiration(exp)
            self._validate_if_needed(exp)
            self._check_effectiveness(exp)
        
        db.commit()
        logger.info(f"策略{strategy_id}经验生命周期更新完成")
    
    def _decay_weight(self, exp: Experience) -> float:
        """指数衰减基线：max(min_weight, initial_weight × e^(-rate × 月数))

        以 initial_weight 为起点（生成时的权重），不再从 1.0 硬重算——
        旧写法会把 boost 过的经验拉回线性基线，强化结果每跑一次生命周期就丢一次。
        """
        months_since = (date.today() - exp.generated_date).days / 30
        initial = exp.initial_weight or exp.weight or 1.0
        rate = get_settings().experience_weight_decay_rate
        return max(self.LIFECYCLE_CONFIG["min_weight"], initial * math.exp(-rate * months_since))

    def _set_weight(self, exp: Experience, value: float, source: str):
        """权重单一权威写入：按优先级结算后再落库

        已有更高优先级来源（如 boost）且现值不低于本次结果时整笔作废——
        值与来源都保持，衰减不能把强化结果抹回基线；
        同优先级或更高优先级则正常覆盖（衰减仍要能随时间下降）。
        统一封顶 max_weight，避免场景调权被重复调用逐次放大。
        """
        value = round(min(self.LIFECYCLE_CONFIG["max_weight"], value), 3)
        current_rank = self.WEIGHT_SOURCE_RANK.get(exp.weight_source or "decay", 1)
        if (current_rank > self.WEIGHT_SOURCE_RANK[source]
                and exp.weight is not None and exp.weight >= value):
            return
        if exp.weight == value and exp.weight_source == source:
            return
        exp.weight = value
        exp.weight_source = source
    
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
        """检查效果评分：过低先待审，确实评得很低就直接停用

        success_rate 为 None 表示还没有足量使用数据，此时 effectiveness_score 是默认值，
        不能拿去做去留判断（否则新生成的经验会在第一轮生命周期里被全部停用）。
        """
        if exp.success_rate is None:
            return
        score = exp.effectiveness_score or 0.0
        if score >= self.LIFECYCLE_CONFIG["effectiveness_threshold"]:
            return
        exp.review_status = "pending"
        logger.warning(f"经验{exp.id}效果评分过低: {score}")
        if score < self.LIFECYCLE_CONFIG["prune_effectiveness_threshold"]:
            exp.is_active = False
            logger.info(f"经验{exp.id}效果评分{score}低于停用线，已停用")
    
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
        """清理低效经验（阈值与生命周期停用线同源）"""
        low_effect = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.is_validated == True,
            Experience.effectiveness_score < self.LIFECYCLE_CONFIG["prune_effectiveness_threshold"],
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
        from app.services.smart_experience_matcher import get_smart_experience_matcher
        matcher = get_smart_experience_matcher()
        
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
        from app.services.smart_experience_matcher import get_smart_experience_matcher
        matcher = get_smart_experience_matcher()
        
        matched = matcher.match_experiences_by_scenario(strategy_id, current_scenario, db)
        
        adjustment_records = []
        for m in matched:
            exp = m["experience"]
            old_weight = exp.weight or 1.0
            # 场景加成换算成「系数」再乘到衰减基线上：直接写 adjusted_weight
            # 会让每次调用都在上一次的结果上再乘一遍，权重只增不减地漂走
            factor = (m["adjusted_weight"] / old_weight) if old_weight > 0 else 1.0
            proposed = self._decay_weight(exp) * factor
            self._set_weight(exp, proposed, "scenario")

            adjustment_records.append({
                "experience_id": exp.id,
                "title": exp.title,
                "old_weight": old_weight,
                "new_weight": exp.weight,
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
            # 同样以衰减基线为参照：重复调用不会把权重越乘越离谱，
            # 而 boost 结果由 _set_weight 的优先级保护，不会被后续 decay 抹回基线
            proposed = self._decay_weight(exp) * self.LIFECYCLE_CONFIG["failure_priority_boost"]
            self._set_weight(exp, proposed, "boost")
            boosted_count += 1
        
        db.commit()
        logger.info(f"强化{boosted_count}条失败经验权重")
        
        return boosted_count

_service = None


def get_experience_manager() -> ExperienceManager:
    global _service
    if _service is None:
        _service = ExperienceManager()
    return _service
