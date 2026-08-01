import logging
from datetime import date
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.agents.technical_analyst import TechnicalAnalystAgent
from app.agents.sentiment_analyst import SentimentAnalystAgent
from app.agents.market_analyst import MarketAnalystAgent
from app.agents.bull_researcher import BullResearcher
from app.agents.bear_researcher import BearResearcher
from app.agents.macro_cycle_agent import MacroCycleAgent
from app.agents.cross_asset_agent import CrossAssetAgent
from app.agents.volatility_regime_agent import VolatilityRegimeAgent
from app.agents.theme_discovery_agent import ThemeDiscoveryAgent
from app.agents.drawdown_attribution_agent import DrawdownAttributionAgent
from app.agents.rebalance_timing_agent import RebalanceTimingAgent
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self):
        self.technical_analyst = TechnicalAnalystAgent()
        self.sentiment_analyst = SentimentAnalystAgent()
        self.market_analyst = MarketAnalystAgent()
        self.bull_researcher = BullResearcher()
        self.bear_researcher = BearResearcher()
        self.macro_cycle = MacroCycleAgent()
        self.cross_asset = CrossAssetAgent()
        self.volatility_regime = VolatilityRegimeAgent()
        self.theme_discovery = ThemeDiscoveryAgent()
        self.drawdown_attribution = DrawdownAttributionAgent()
        self.rebalance_timing = RebalanceTimingAgent()

    def analyze(self, strategy_id: int, analysis_date: date, db: Session) -> Dict:
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}

        etf_codes = list((strategy.allocation_config or {}).keys())

        logger.info(f"[Orchestrator] 策略{strategy_id} 开始多Agent辩论式分析")

        # 阶段1: 数据消化
        technical_report = self.technical_analyst.analyze(etf_codes, db)
        if "error" in technical_report:
            logger.warning(f"[Orchestrator] 技术分析师失败: {technical_report.get('error')}")

        sentiment_report = self.sentiment_analyst.analyze(analysis_date, db)
        if "error" in sentiment_report:
            logger.warning(f"[Orchestrator] 情绪分析师失败: {sentiment_report.get('error')}")

        # 阶段1.5: 宏观+跨资产+波动率（增强分析层）
        macro_report = self.macro_cycle.analyze(etf_codes, db)
        if "error" in macro_report:
            logger.warning(f"[Orchestrator] 宏观周期分析失败: {macro_report.get('error')}")

        cross_asset_report = self.cross_asset.analyze(etf_codes, db)
        if "error" in cross_asset_report:
            logger.warning(f"[Orchestrator] 跨资产分析失败: {cross_asset_report.get('error')}")

        vol_report = self.volatility_regime.analyze(etf_codes, db)
        if "error" in vol_report:
            logger.warning(f"[Orchestrator] 波动率体制分析失败: {vol_report.get('error')}")

        # 阶段2: 多空辩论
        bull_report = self.bull_researcher.analyze(technical_report, sentiment_report)
        if "error" in bull_report:
            logger.warning(f"[Orchestrator] 多头研究员失败: {bull_report.get('error')}")

        bear_report = self.bear_researcher.analyze(technical_report, sentiment_report)
        if "error" in bear_report:
            logger.warning(f"[Orchestrator] 空头研究员失败: {bear_report.get('error')}")

        # 阶段3: 研究主管裁决
        final_decision = self.market_analyst.analyze(
            strategy_id=strategy_id,
            analysis_date=analysis_date,
            technical_report=technical_report,
            sentiment_report=sentiment_report,
            db=db,
            bull_report=bull_report,
            bear_report=bear_report,
        )

        # 阶段4: 辅助决策（主题发现 + 再平衡时机）
        theme_report = self.theme_discovery.analyze(etf_codes, db)
        if "error" in theme_report:
            logger.warning(f"[Orchestrator] 主题发现失败: {theme_report.get('error')}")

        rebalance_report = self.rebalance_timing.analyze(strategy_id, db)
        if "error" in rebalance_report:
            logger.warning(f"[Orchestrator] 再平衡时机判断失败: {rebalance_report.get('error')}")

        combined = {
            "analysis_date": analysis_date.isoformat(),
            "technical_report": technical_report,
            "sentiment_report": sentiment_report,
            "macro_report": macro_report,
            "cross_asset_report": cross_asset_report,
            "volatility_report": vol_report,
            "bull_report": bull_report,
            "bear_report": bear_report,
            "theme_report": theme_report,
            "rebalance_timing": rebalance_report,
            **final_decision,
        }

        if "error" not in final_decision:
            strategy.last_analysis_result = combined
            strategy.last_auto_analysis_date = analysis_date
            db.commit()
            logger.info(f"[Orchestrator] 策略{strategy_id} 辩论分析完成: {final_decision.get('market_regime')}")

        return combined

    def analyze_drawdown(self, strategy_id: int, db: Session) -> Dict:
        return self.drawdown_attribution.analyze(strategy_id, db)
