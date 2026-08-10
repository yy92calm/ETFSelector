"""
配置组合策略管理服务
"""

import logging
from datetime import date
from typing import List, Optional, Dict
from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.strategies.portfolio_rebalance import PortfolioRebalanceStrategy

logger = logging.getLogger(__name__)


class StrategyService:

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
        """列出所有策略"""
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
        
        # 配置比例变更走 t+1 待生效，其余字段直接更新
        allocation_config = data.pop("allocation_config", None)

        for field, value in data.items():
            if hasattr(strategy, field) and value is not None:
                setattr(strategy, field, value)

        mode = None
        if allocation_config:
            mode = self.stage_allocation_change(strategy, allocation_config, db)

        db.commit()
        db.refresh(strategy)
        logger.info(f"更新策略 {strategy_id}: {list(data.keys()) + (['allocation_config'] if mode else [])}")
        return strategy

    def stage_allocation_change(self, strategy: Strategy, new_allocation: Dict[str, float], db: Session) -> str:
        """提交配置变更（t+1 生效，不 commit，由调用方提交事务）

        已有持仓记录（运行中）的策略：写入待生效配置，下一交易日执行；
        尚无持仓记录的新策略：立即生效（首次建仓按新配置执行）。

        Returns:
            "pending" 或 "immediate"
        """
        total = sum(new_allocation.values())
        if abs(total - 1.0) > 0.01:
            raise ValueError(f"配置比例总和应为1.0，当前为 {total:.4f}")

        from app.models.portfolio import PortfolioSnapshot
        has_history = (
            db.query(PortfolioSnapshot.id)
            .filter(PortfolioSnapshot.strategy_id == strategy.id)
            .first()
            is not None
        )

        if has_history:
            strategy.pending_allocation = dict(new_allocation)
            strategy.pending_set_date = date.today()
            logger.info(f"策略 {strategy.id} 配置变更已提交，待下一交易日生效: {new_allocation}")
            return "pending"

        strategy.allocation_config = dict(new_allocation)
        strategy.pending_allocation = None
        strategy.pending_set_date = None
        return "immediate"

    def get_effective_target_allocation(self, strategy: Strategy) -> Dict[str, float]:
        """当前有效目标配置：有待生效配置时返回待生效配置，否则返回现行配置"""
        return dict(strategy.pending_allocation or strategy.allocation_config or {})


_service: Optional[StrategyService] = None


def get_strategy_service() -> StrategyService:
    global _service
    if _service is None:
        _service = StrategyService()
    return _service
