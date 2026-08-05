"""失败模式库服务 - 将失败操作沉淀为可规避的模式（FSA式）"""

import logging
from datetime import date
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.models.experience import Experience

logger = logging.getLogger(__name__)


class FailureModeService:
    """失败模式库 - 记录重复失败操作，供生成阶段自动规避"""

    CONFIG = {
        "ban_threshold": 3,          # 同一签名出现≥3次即进入规避名单
        "default_expire_days": 90,
    }

    def record_failure(
        self,
        strategy_id: int,
        signature: str,
        title: str,
        description: str,
        scenario_tags: List[str],
        key_insight: str = "",
        db: Session = None,
    ) -> Experience:
        """记录失败模式（幂等：相同 signature 累计 occurrence_count）"""
        if not signature:
            logger.warning("失败模式签名不能为空，跳过记录")
            return None

        exp = db.query(Experience).filter(
            Experience.failure_signature == signature,
            Experience.experience_type == "failure",
            Experience.is_active == True,
        ).first()

        if exp:
            exp.occurrence_count = (exp.occurrence_count or 1) + 1
            exp.last_triggered_date = date.today()
            exp.description = description
            exp.key_insight = key_insight or exp.key_insight
            db.commit()
            logger.info(f"失败模式已存在 {signature}，次数累计为 {exp.occurrence_count}")
            return exp

        exp = Experience(
            strategy_id=strategy_id,
            experience_type="failure",
            scenario_tags=scenario_tags,
            title=title,
            description=description,
            result="negative",
            key_insight=key_insight,
            failure_signature=signature,
            occurrence_count=1,
            last_triggered_date=date.today(),
            generated_date=date.today(),
            expires_date=Experience.get_default_expires_date(),
        )
        db.add(exp)
        db.commit()
        logger.info(f"记录失败模式: {signature}")
        return exp

    def get_banned_codes(self, db: Session, threshold: int = None) -> Dict[str, int]:
        """获取规避名单：重复失败≥阈值的 ETF 代码 → 出现次数

        从 failure_signature 解析 ETF 代码（签名格式: 买入{code}... 或 卖出{code}...）
        """
        threshold = threshold or self.CONFIG["ban_threshold"]
        exps = db.query(Experience).filter(
            Experience.experience_type == "failure",
            Experience.is_active == True,
            Experience.occurrence_count >= threshold,
        ).all()

        banned: Dict[str, int] = {}
        import re
        for exp in exps:
            sig = exp.failure_signature or ""
            for code in re.findall(r"[0-9]{6}", sig):
                banned[code] = max(banned.get(code, 0), exp.occurrence_count or 1)
        return banned

    def get_active_failure_modes(
        self,
        db: Session,
        scenario_tags: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[Dict]:
        """获取活跃失败模式（供LLM提示词注入，规避已失败操作）"""
        exps = db.query(Experience).filter(
            Experience.experience_type == "failure",
            Experience.is_active == True,
        ).order_by(Experience.occurrence_count.desc(), Experience.last_triggered_date.desc()).all()

        if scenario_tags:
            tag_set = set(t.lower() for t in scenario_tags)
            exps = [
                e for e in exps
                if e.scenario_tags and (tag_set & set(t.lower() for t in e.scenario_tags))
            ]

        exps = exps[:limit]
        return [{
            "signature": e.failure_signature,
            "title": e.title,
            "description": e.description,
            "key_insight": e.key_insight,
            "occurrence_count": e.occurrence_count,
            "scenario_tags": e.scenario_tags,
            "last_triggered_date": e.last_triggered_date.isoformat() if e.last_triggered_date else None,
        } for e in exps]

    def is_code_banned(self, code: str, db: Session, threshold: int = None) -> bool:
        """判断ETF代码是否在规避名单"""
        return code in self.get_banned_codes(db, threshold)


_service: FailureModeService | None = None


def get_failure_mode_service() -> FailureModeService:
    global _service
    if _service is None:
        _service = FailureModeService()
    return _service
