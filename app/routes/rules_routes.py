"""
规则训练相关API路由
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse
from app.services.rule_trainer import get_rule_trainer

router = APIRouter(prefix="/api/rules", tags=["规则训练"])


@router.get("", response_model=APIResponse)
def get_rules(db: Session = Depends(get_db)):
    """获取当前训练后的规则表"""
    trainer = get_rule_trainer()
    rules = trainer.train(db, days=90)
    return APIResponse(data=rules)


@router.post("/train", response_model=APIResponse)
def train_rules(db: Session = Depends(get_db)):
    """重新训练规则表"""
    trainer = get_rule_trainer()
    rules = trainer.train(db, days=90)

    # 清除RuleEngine缓存，使新规则立即生效
    from app.services.rule_engine import get_rule_engine
    get_rule_engine().invalidate_cache()

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
