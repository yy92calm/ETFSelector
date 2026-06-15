import logging
from datetime import date
from typing import Dict, List, Optional
from sqlalchemy.orm import Session

from app.agents.technical_analyst import TechnicalAnalystAgent
from app.agents.sentiment_analyst import SentimentAnalystAgent
from app.agents.market_analyst import MarketAnalystAgent
from app.agents.bull_researcher import BullResearcher
from app.agents.bear_researcher import BearResearcher
from app.models.strategy import Strategy

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self):
        self.technical_analyst = TechnicalAnalystAgent()
        self.sentiment_analyst = SentimentAnalystAgent()
        self.market_analyst = MarketAnalystAgent()
        self.bull_researcher = BullResearcher()
        self.bear_researcher = BearResearcher()

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

        combined = {
            "analysis_date": analysis_date.isoformat(),
            "technical_report": technical_report,
            "sentiment_report": sentiment_report,
            "bull_report": bull_report,
            "bear_report": bear_report,
            **final_decision,
        }

        if "error" not in final_decision:
            strategy.last_analysis_result = combined
            strategy.last_auto_analysis_date = analysis_date
            db.commit()
            logger.info(f"[Orchestrator] 策略{strategy_id} 辩论分析完成: {final_decision.get('market_regime')}")

        return combined
