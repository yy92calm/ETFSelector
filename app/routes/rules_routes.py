"""
规则训练相关API路由
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.rule_trainer import get_rule_trainer

router = APIRouter(prefix="/api/rules", tags=["规则训练"])


def _latest_snapshot_meta(db: Session, strategy_id: Optional[int]) -> Optional[dict]:
    """最新落库快照信息（供前端展示数据新鲜度）"""
    from app.models.strategy import RuleSnapshot

    query = db.query(RuleSnapshot)
    query = query.filter(RuleSnapshot.strategy_id.is_(None) if strategy_id is None
                         else RuleSnapshot.strategy_id == strategy_id)
    row = query.order_by(RuleSnapshot.created_at.desc()).first()
    if not row:
        return None
    return {
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "source": row.source,
        "days_covered": row.days_covered,
    }


@router.get("", response_model=APIResponse)
def get_rules(strategy_id: Optional[int] = None, db: Session = Depends(get_db)):
    """获取训练后的规则表（strategy_id 缺省=全局规则）"""
    trainer = get_rule_trainer()
    rules = trainer.train(db, days=90, strategy_id=strategy_id)
    rules["latest_snapshot"] = _latest_snapshot_meta(db, strategy_id)
    return APIResponse(data=rules)


@router.post("/train", response_model=APIResponse)
def train_rules(strategy_id: Optional[int] = None, db: Session = Depends(get_db)):
    """重新训练规则表并落快照"""
    from datetime import datetime
    from app.models.strategy import RuleSnapshot

    trainer = get_rule_trainer()
    rules = trainer.train(db, days=90, strategy_id=strategy_id)

    # 落一份快照，使 rule_engine / 进化读到最新规则
    try:
        db.add(RuleSnapshot(
            strategy_id=strategy_id,
            snapshot=rules,
            source="manual",
            days_covered=(rules.get("training_period") or {}).get("days"),
        ))
        db.commit()
    except Exception:
        db.rollback()

    # 清除RuleEngine缓存，使新规则立即生效
    from app.services.rule_engine import get_rule_engine
    get_rule_engine().invalidate_cache()

    rules["latest_snapshot"] = _latest_snapshot_meta(db, strategy_id)
    return APIResponse(
        message="规则训练完成",
        data=rules
    )


@router.get("/regime/{regime}", response_model=APIResponse)
def get_regime_detail(regime: str, db: Session = Depends(get_db)):
    """获取指定regime的详细规则说明"""
    trainer = get_rule_trainer()
    rules = trainer.train(db, days=90)

    regime_rules = rules.get("regime_rules", {})
    rule = regime_rules.get(regime)
    if not rule:
        return APIResponse(code=404, message="无此regime数据")

    explanation = trainer.explain_regime(regime, rules)
    return APIResponse(data={
        "regime": regime,
        "rule": rule,
        "explanation": explanation,
    })
