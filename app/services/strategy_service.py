"""
策略管理服务
负责策略的CRUD以及关联策略引擎
"""

import logging
from typing import List, Optional
from sqlalchemy.orm import Session

from app.models.strategy import Strategy
from app.strategies.registry import get_template_strategy, list_templates
from app.strategies.generator import generate_strategy_code, compile_strategy
from app.strategies.base import BaseStrategy

logger = logging.getLogger(__name__)


class StrategyService:

    def create_template_strategy(self, data: dict, db: Session) -> Strategy:
        """创建模板策略"""
        template_name = data.get("template_name")
        params = data.get("params", {})

        # 校验模板存在
        _ = get_template_strategy(template_name, params)

        strategy = Strategy(
            name=data["name"],
            description=data.get("description", ""),
            strategy_type="template",
            template_name=template_name,
            params=params,
            etf_codes=data.get("etf_codes", []),
            initial_capital=data.get("initial_capital", 100000),
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        logger.info(f"创建模板策略: {strategy}")
        return strategy

    def create_ai_strategy(self, description: str, etf_codes: List[str],
                           initial_capital: int, db: Session) -> Strategy:
        """创建AI生成的策略"""
        code = generate_strategy_code(description)
        if not code:
            raise ValueError("AI策略生成失败，请检查LLM配置或重新描述")

        # 验证代码可编译
        instance = compile_strategy(code)
        if instance is None:
            raise ValueError("生成的策略代码无法编译执行")

        strategy = Strategy(
            name=f"AI策略-{description[:20]}",
            description=description,
            strategy_type="ai_generated",
            code=code,
            etf_codes=etf_codes,
            initial_capital=initial_capital,
        )
        db.add(strategy)
        db.commit()
        db.refresh(strategy)
        logger.info(f"创建AI策略: {strategy}")
        return strategy

    def get_strategy_instance(self, strategy: Strategy) -> BaseStrategy:
        """根据数据库Strategy记录获取可执行的策略实例"""
        if strategy.strategy_type == "template":
            return get_template_strategy(strategy.template_name, strategy.params)
        elif strategy.strategy_type == "ai_generated" and strategy.code:
            instance = compile_strategy(strategy.code)
            if instance is None:
                raise ValueError(f"策略 {strategy.id} 代码编译失败")
            return instance
        else:
            raise ValueError(f"无法创建策略实例: type={strategy.strategy_type}")

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


_service: Optional[StrategyService] = None


def get_strategy_service() -> StrategyService:
    global _service
    if _service is None:
        _service = StrategyService()
    return _service
