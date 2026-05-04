"""策略管理路由"""

import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse, StrategyCreate, AIStrategyRequest, StrategyUpdate, AIChatRequest
from app.services.strategy_service import get_strategy_service
from app.strategies.registry import list_templates

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/strategy", tags=["配置组合策略"])


@router.get("/templates", response_model=APIResponse)
def get_strategy_templates():
    """获取所有配置模板"""
    templates = list_templates()
    return APIResponse(data={"templates": templates})


@router.get("/list", response_model=APIResponse)
def get_strategy_list(db: Session = Depends(get_db)):
    """获取所有策略"""
    svc = get_strategy_service()
    strategies = svc.list_strategies(db)
    return APIResponse(data={
        "strategies": [{
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "strategy_type": s.strategy_type,
            
            # 配置组合字段
            "allocation_config": s.allocation_config,
            "rebalance_freq": s.rebalance_freq,
            "rebalance_threshold": s.rebalance_threshold,
            
            "initial_capital": s.initial_capital,
            "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        } for s in strategies],
    })


@router.post("/create", response_model=APIResponse)
def create_strategy(req: StrategyCreate, db: Session = Depends(get_db)):
    """创建模板配置策略"""
    svc = get_strategy_service()
    try:
        strategy = svc.create_template_strategy(req.model_dump(), db)
        return APIResponse(message="配置策略创建成功", data={
            "strategy_id": strategy.id,
            "allocation_config": strategy.allocation_config
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/create-custom", response_model=APIResponse)
def create_custom_strategy(req: StrategyCreate, db: Session = Depends(get_db)):
    """创建自定义配置策略"""
    svc = get_strategy_service()
    try:
        strategy = svc.create_custom_strategy(req.model_dump(), db)
        return APIResponse(message="自定义配置策略创建成功", data={
            "strategy_id": strategy.id,
            "allocation_config": strategy.allocation_config
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/ai-chat", response_model=APIResponse)
def ai_strategy_chat(req: dict, db: Session = Depends(get_db)):
    """AI对话式生成策略配置"""
    from app.strategies.generator import ETFAllocationAgent
    
    message = req.get("message", "")
    chat_history = req.get("chat_history", "")
    current_allocation = req.get("current_allocation")
    model = req.get("model", "qwen3.6-plus")
    
    if not message or len(message) < 5:
        raise HTTPException(status_code=400, detail="消息至少需要5个字符")
    
    try:
        # 创建Agent实例
        agent = ETFAllocationAgent()
        
        # 生成配置（支持迭代优化）
        result = agent.chat_and_generate(
            user_message=message,
            chat_history=chat_history,
            current_allocation=current_allocation,
            model=model,
            db=db
        )
        
        return APIResponse(message="对话成功", data=result)
    
    except Exception as e:
        logger.error(f"AI对话失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"AI对话失败: {str(e)}")


@router.post("/create-ai", response_model=APIResponse)
def create_ai_strategy(req: AIStrategyRequest, db: Session = Depends(get_db)):
    """通过自然语言描述创建AI配置策略"""
    svc = get_strategy_service()
    try:
        strategy = svc.create_ai_strategy(
            description=req.description,
            initial_capital=req.initial_capital,
            rebalance_freq=req.rebalance_freq,
            rebalance_threshold=req.rebalance_threshold,
            db=db,
            model=req.model,
        )
        return APIResponse(message="AI配置策略生成成功", data={
            "strategy_id": strategy.id,
            "allocation_config": strategy.allocation_config,
        })
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{strategy_id}", response_model=APIResponse)
def get_strategy_detail(strategy_id: int, db: Session = Depends(get_db)):
    """获取策略详情"""
    svc = get_strategy_service()
    s = svc.get_strategy(strategy_id, db)
    if not s:
        raise HTTPException(status_code=404, detail="策略不存在")
    
    return APIResponse(data={
        "id": s.id,
        "name": s.name,
        "description": s.description,
        "strategy_type": s.strategy_type,
        
        # 配置组合字段
        "allocation_config": s.allocation_config,
        "rebalance_freq": s.rebalance_freq,
        "rebalance_threshold": s.rebalance_threshold,
        
        "initial_capital": s.initial_capital,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    })


@router.put("/{strategy_id}/status", response_model=APIResponse)
def update_status(strategy_id: int, status: str, db: Session = Depends(get_db)):
    """更新策略状态 (active / paused / archived)"""
    if status not in ("active", "paused", "archived"):
        raise HTTPException(status_code=400, detail="无效状态")
    svc = get_strategy_service()
    s = svc.update_strategy_status(strategy_id, status, db)
    if not s:
        raise HTTPException(status_code=404, detail="策略不存在")
    return APIResponse(message=f"策略状态已更新为 {status}")


@router.delete("/{strategy_id}", response_model=APIResponse)
def delete_strategy(strategy_id: int, db: Session = Depends(get_db)):
    """删除策略"""
    svc = get_strategy_service()
    if svc.delete_strategy(strategy_id, db):
        return APIResponse(message="策略已删除")
    raise HTTPException(status_code=404, detail="策略不存在")


@router.put("/{strategy_id}", response_model=APIResponse)
def update_strategy(strategy_id: int, req: StrategyUpdate, db: Session = Depends(get_db)):
    """更新策略信息"""
    svc = get_strategy_service()
    s = svc.update_strategy(strategy_id, req.model_dump(exclude_unset=True), db)
    if not s:
        raise HTTPException(status_code=404, detail="策略不存在")
    return APIResponse(message="策略更新成功", data={
        "strategy_id": s.id,
        "allocation_config": s.allocation_config
    })
