"""
配置组合策略管理服务
"""

import logging
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.strategies.registry import get_template_config, list_templates
from app.strategies.portfolio_rebalance import PortfolioRebalanceStrategy

logger = logging.getLogger(__name__)


class StrategyService:

    def create_template_strategy(self, data: dict, db: Session) -> Strategy:
        """创建模板配置策略"""
        template_name = data.get("template_name")
        
        # 获取模板配置
        template_config = get_template_config(template_name)
        
        strategy = Strategy(
            name=data["name"],
            description=data.get("description", "") or template_config["description"],
            strategy_type="template",
            
            # 配置组合字段
            allocation_config=template_config["allocation_config"],
            rebalance_freq=template_config["rebalance_freq"],
            rebalance_threshold=template_config["rebalance_threshold"],
            
            initial_capital=data.get("initial_capital", 100000),
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        logger.info(f"创建配置组合策略: {strategy.name}, 配置: {strategy.allocation_config}")
        return strategy
    
    def create_custom_strategy(self, data: dict, db: Session) -> Strategy:
        """创建自定义配置策略"""
        allocation_config = data.get("allocation_config")
        
        if not allocation_config:
            raise ValueError("必须提供 allocation_config 配置比例")
        
        # 验证配置比例总和为1
        total = sum(allocation_config.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"配置比例总和应为1.0，当前为 {total:.2f}")
        
        strategy = Strategy(
            name=data["name"],
            description=data.get("description", ""),
            strategy_type="custom",
            
            allocation_config=allocation_config,
            rebalance_freq=data.get("rebalance_freq", "quarterly"),
            rebalance_threshold=data.get("rebalance_threshold", 0.05),
            
            initial_capital=data.get("initial_capital", 100000),
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        logger.info(f"创建自定义配置策略: {strategy.name}")
        return strategy

    def create_ai_strategy(self, description: str, initial_capital: int, 
                           rebalance_freq: str, rebalance_threshold: float, 
                           db: Session, model: str = "qwen3.6-plus") -> Strategy:
        """创建AI生成的配置策略"""
        from app.strategies.generator import generate_allocation_config
        
        allocation_config = generate_allocation_config(description, model=model)
        if not allocation_config:
            raise ValueError("AI配置生成失败，请检查LLM配置或重新描述")
        
        strategy = Strategy(
            name=f"AI配置-{description[:20]}",
            description=description,
            strategy_type="ai_generated",
            
            allocation_config=allocation_config,
            rebalance_freq=rebalance_freq,
            rebalance_threshold=rebalance_threshold,
            
            initial_capital=initial_capital,
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        logger.info(f"创建AI配置策略: {strategy.name}, 配置: {allocation_config}")
        return strategy

    def get_strategy_instance(self, strategy: Strategy) -> PortfolioRebalanceStrategy:
        """获取可执行的策略实例"""
        if not strategy.allocation_config:
            raise ValueError(f"策略 {strategy.id} 未设置配置比例")
        
        return PortfolioRebalanceStrategy(
            allocation_config=strategy.allocation_config,
            rebalance_freq=strategy.rebalance_freq or "quarterly",
            rebalance_threshold=strategy.rebalance_threshold or 0.05
        )

    def get_strategy(self, strategy_id: int, db: Session) -> Optional[Strategy]:
        return db.query(Strategy).filter(Strategy.id == strategy_id).first()

    def list_strategies(self, db: Session) -> List[Strategy]:
        return db.query(Strategy).order_by(Strategy.created_at.desc()).all()

    def update_strategy_status(self, strategy_id: int, status: str, db: Session) -> Optional[Strategy]:
        strategy = self.get_strategy(strategy_id, db)
        if strategy:
            strategy.status = status
            db.commit()
            db.refresh(strategy)
        return strategy

    def delete_strategy(self, strategy_id: int, db: Session) -> bool:
        strategy = self.get_strategy(strategy_id, db)
        if strategy:
            db.delete(strategy)
            db.commit()
            return True
        return False

    def update_strategy(self, strategy_id: int, data: dict, db: Session) -> Optional[Strategy]:
        """更新策略信息"""
        strategy = self.get_strategy(strategy_id, db)
        if not strategy:
            return None
        
        # 验证配置比例（如果更新了）
        if "allocation_config" in data and data["allocation_config"]:
            total = sum(data["allocation_config"].values())
            if abs(total - 1.0) > 0.01:
                raise ValueError(f"配置比例总和应为1.0，当前为 {total:.2f}")
        
        # 更新字段
        for field, value in data.items():
            if hasattr(strategy, field) and value is not None:
                setattr(strategy, field, value)
        
        db.commit()
        db.refresh(strategy)
        logger.info(f"更新策略 {strategy_id}: {data.keys()}")
        return strategy


_service: Optional[StrategyService] = None


def get_strategy_service() -> StrategyService:
    global _service
    if _service is None:
        _service = StrategyService()
    return _service
