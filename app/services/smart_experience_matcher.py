"""智能经验匹配服务"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Tuple
from sqlalchemy.orm import Session
import numpy as np

from app.models.experience import Experience, ExperienceUsageRecord
from app.models.sentiment import SentimentData
from app.models.etf import ETFQuotation

logger = logging.getLogger(__name__)


class SmartExperienceMatcher:
    """智能经验匹配服务 - 场景标签匹配、动态权重、冲突检测"""
    
    MATCHING_CONFIG = {
        "scenario_similarity_threshold": 0.6,
        "weight_boost_factor": 1.5,
        "failure_weight_multiplier": 2.0,
        "conflict_detection_threshold": 0.7,
    }
    
    def match_experiences_by_scenario(
        self,
        strategy_id: int,
        current_scenario: Dict,
        db: Session
    ) -> List[Dict]:
        """基于场景标签匹配经验"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
        ).all()
        
        matched = []
        for exp in experiences:
            similarity = self._calculate_scenario_similarity(
                current_scenario,
                exp.scenario_tags or []
            )
            
            if similarity >= self.MATCHING_CONFIG["scenario_similarity_threshold"]:
                adjusted_weight = self._adjust_weight_by_scenario(exp, similarity)
                
                matched.append({
                    "experience": exp,
                    "scenario_similarity": round(similarity, 3),
                    "adjusted_weight": round(adjusted_weight, 3),
                    "match_type": "scenario_based",
                    "tags_matched": self._get_matched_tags(current_scenario, exp.scenario_tags),
                })
        
        matched.sort(key=lambda x: x["adjusted_weight"], reverse=True)
        
        return matched[:10]
    
    def _calculate_scenario_similarity(self, current_scenario: Dict, exp_tags: List[str]) -> float:
        """计算场景相似度"""
        if not exp_tags:
            return 0.0
        
        current_tags = set()
        for key, value in current_scenario.items():
            if isinstance(value, str):
                current_tags.add(value.lower())
            elif isinstance(value, list):
                current_tags.update([v.lower() for v in value])
        
        exp_tags_set = set([t.lower() for t in exp_tags])
        
        intersection = len(current_tags & exp_tags_set)
        union = len(current_tags | exp_tags_set)
        
        if union == 0:
            return 0.0
        
        jaccard_similarity = intersection / union
        
        return jaccard_similarity
    
    def _adjust_weight_by_scenario(self, exp: Experience, similarity: float) -> float:
        """根据场景相似度调整权重"""
        base_weight = exp.weight or 1.0
        
        boost = self.MATCHING_CONFIG["weight_boost_factor"]
        adjusted = base_weight * (1 + similarity * boost)
        
        if exp.experience_type == "failure":
            adjusted *= self.MATCHING_CONFIG["failure_weight_multiplier"]
        
        return adjusted
    
    def _get_matched_tags(self, current_scenario: Dict, exp_tags: List[str]) -> List[str]:
        """获取匹配的标签"""
        matched = []
        exp_tags_lower = [t.lower() for t in exp_tags]
        
        for key, value in current_scenario.items():
            if isinstance(value, str):
                if value.lower() in exp_tags_lower:
                    matched.append(value)
            elif isinstance(value, list):
                for v in value:
                    if v.lower() in exp_tags_lower:
                        matched.append(v)
        
        return matched
    
    def detect_experience_conflicts(self, experiences: List[Experience]) -> List[Dict]:
        """检测经验冲突"""
        conflicts = []
        
        for i, exp1 in enumerate(experiences):
            for exp2 in experiences[i+1:]:
                conflict_score = self._calculate_conflict_score(exp1, exp2)
                
                if conflict_score >= self.MATCHING_CONFIG["conflict_detection_threshold"]:
                    conflicts.append({
                        "experience_1": {
                            "id": exp1.id,
                            "title": exp1.title,
                            "type": exp1.experience_type,
                            "action": exp1.action_taken,
                        },
                        "experience_2": {
                            "id": exp2.id,
                            "title": exp2.title,
                            "type": exp2.experience_type,
                            "action": exp2.action_taken,
                        },
                        "conflict_score": round(conflict_score, 3),
                        "conflict_type": self._identify_conflict_type(exp1, exp2),
                        "resolution_hint": self._generate_resolution_hint(exp1, exp2),
                    })
        
        return conflicts
    
    def _calculate_conflict_score(self, exp1: Experience, exp2: Experience) -> float:
        """计算冲突分数"""
        score = 0
        
        if exp1.experience_type == "failure" and exp2.experience_type == "success":
            score += 0.5
        elif exp1.experience_type == "success" and exp2.experience_type == "failure":
            score += 0.5
        
        tags1 = set(exp1.scenario_tags or [])
        tags2 = set(exp2.scenario_tags or [])
        
        common_tags = len(tags1 & tags2)
        total_tags = len(tags1 | tags2)
        
        if total_tags > 0 and common_tags > 0:
            overlap = common_tags / total_tags
            score += overlap * 0.3
        
        if exp1.action_taken and exp2.action_taken:
            if exp1.action_taken != exp2.action_taken:
                score += 0.2
        
        return score
    
    def _identify_conflict_type(self, exp1: Experience, exp2: Experience) -> str:
        """识别冲突类型"""
        if exp1.experience_type == "failure" and exp2.experience_type == "success":
            return "action_conflict"
        
        if exp1.scenario_tags and exp2.scenario_tags:
            common = set(exp1.scenario_tags) & set(exp2.scenario_tags)
            if common:
                return "scenario_overlap"
        
        return "general_conflict"
    
    def _generate_resolution_hint(self, exp1: Experience, exp2: Experience) -> str:
        """生成解决提示"""
        if exp1.experience_type == "failure":
            return f"优先参考失败经验: {exp1.title}"
        elif exp2.experience_type == "failure":
            return f"优先参考失败经验: {exp2.title}"
        else:
            return "根据市场环境具体情况选择更相关的经验"
    
    def prioritize_failure_experiences(self, experiences: List[Experience]) -> List[Experience]:
        """优先展示失败经验"""
        failures = [e for e in experiences if e.experience_type == "failure"]
        successes = [e for e in experiences if e.experience_type == "success"]
        insights = [e for e in experiences if e.experience_type == "insight"]
        
        failures.sort(key=lambda x: x.effectiveness_score or 0, reverse=True)
        successes.sort(key=lambda x: x.effectiveness_score or 0, reverse=True)
        insights.sort(key=lambda x: x.effectiveness_score or 0, reverse=True)
        
        prioritized = failures[:3] + successes[:3] + insights[:2]
        
        return prioritized
    
    def calculate_dynamic_experience_weights(
        self,
        strategy_id: int,
        current_date: date,
        db: Session
    ) -> Dict[int, float]:
        """计算动态经验权重"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
        ).all()
        
        weights = {}
        
        for exp in experiences:
            base_weight = exp.weight or 1.0
            
            days_since_generated = (current_date - exp.generated_date).days
            age_factor = max(0.5, 1 - days_since_generated / 90)
            
            usage_records = db.query(ExperienceUsageRecord).filter(
                ExperienceUsageRecord.experience_id == exp.id,
                ExperienceUsageRecord.is_validated == True,
            ).limit(10).all()
            
            if usage_records:
                positive_count = sum(1 for r in usage_records if r.result == "positive")
                success_rate = positive_count / len(usage_records)
                performance_factor = 0.5 + success_rate
            else:
                performance_factor = 0.7
            
            if exp.experience_type == "failure":
                type_factor = 1.5
            elif exp.experience_type == "success":
                type_factor = 1.0
            else:
                type_factor = 0.8
            
            dynamic_weight = base_weight * age_factor * performance_factor * type_factor
            
            weights[exp.id] = round(dynamic_weight, 3)
        
        return weights
    
    def get_current_market_scenario(self, target_date: date, db: Session) -> Dict:
        """获取当前市场场景标签"""
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == target_date
        ).limit(20).all()
        
        scenario_tags = []
        
        if sentiments:
            avg_score = sum(s.sentiment_score or 0 for s in sentiments) / len(sentiments)
            if avg_score > 0.2:
                scenario_tags.append("情绪乐观")
            elif avg_score < -0.2:
                scenario_tags.append("情绪悲观")
            else:
                scenario_tags.append("情绪中性")
            
            key_factors = []
            for s in sentiments:
                if s.key_factors:
                    key_factors.extend(s.key_factors)
            
            unique_factors = list(set(key_factors))[:5]
            scenario_tags.extend(unique_factors)
        
        etfs = db.query(ETFQuotation).order_by(
            ETFQuotation.trade_date.desc()
        ).limit(10).all()
        
        if etfs:
            latest = etfs[0]
            if len(etfs) >= 5:
                change_5d = (latest.close_price - etfs[4].close_price) / etfs[4].close_price * 100
                if change_5d > 2:
                    scenario_tags.append("上涨趋势")
                elif change_5d < -2:
                    scenario_tags.append("下跌趋势")
                else:
                    scenario_tags.append("震荡整理")
        
        return {
            "scenario_tags": scenario_tags,
            "sentiment_level": scenario_tags[0] if scenario_tags else "未知",
            "trend_hint": scenario_tags[-1] if len(scenario_tags) > 1 else "未知",
        }
    
    def update_experience_weights(self, strategy_id: int, db: Session) -> int:
        """更新经验权重"""
        weights = self.calculate_dynamic_experience_weights(strategy_id, date.today(), db)
        
        updated_count = 0
        for exp_id, weight in weights.items():
            exp = db.query(Experience).filter(Experience.id == exp_id).first()
            if exp:
                exp.weight = weight
                updated_count += 1
        
        db.commit()
        logger.info(f"更新{updated_count}条经验权重")
        
        return updated_count