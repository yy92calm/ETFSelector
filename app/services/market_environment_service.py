"""市场环境分析服务"""

import logging
from datetime import date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
import numpy as np

from app.models.etf import ETFQuotation, ETFBasic
from app.models.sentiment import SentimentData
from app.models.portfolio import PortfolioSnapshot

logger = logging.getLogger(__name__)


class MarketEnvironmentService:
    """市场环境分析服务 - 构建市场情绪指数和历史模式匹配"""
    
    MARKET_INDICATORS = {
        "fear_greed_thresholds": {
            "extreme_fear": 25,
            "fear": 45,
            "neutral": 55,
            "greed": 75,
        },
        "similarity_weights": {
            "sentiment": 0.3,
            "trend": 0.3,
            "volatility": 0.2,
            "volume": 0.2,
        },
    }
    
    def build_market_sentiment_index(self, target_date: date, db: Session) -> Dict:
        """构建市场情绪指数（0-100，恐惧-贪婪）"""
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == target_date
        ).all()
        
        if not sentiments:
            return {
                "index": 50,
                "label": "neutral",
                "components": {},
                "message": "无舆情数据，默认中性",
            }
        
        positive_count = sum(1 for s in sentiments if s.sentiment_label == "positive")
        negative_count = sum(1 for s in sentiments if s.sentiment_label == "negative")
        total = len(sentiments)
        
        sentiment_score = (positive_count - negative_count) / total * 100 + 50
        
        avg_sentiment = sum(s.sentiment_score or 0 for s in sentiments) / total
        
        weighted_score = sentiment_score * 0.6 + (avg_sentiment + 50) * 50 * 0.4
        
        weighted_score = max(0, min(100, weighted_score))
        
        label = self._get_sentiment_label(weighted_score)
        
        key_factors = []
        sorted_sentiments = sorted(sentiments, key=lambda x: abs(x.sentiment_score or 0), reverse=True)
        for s in sorted_sentiments[:5]:
            if s.key_factors:
                key_factors.extend(s.key_factors)
        
        return {
            "index": round(weighted_score, 2),
            "label": label,
            "components": {
                "positive_ratio": round(positive_count / total * 100, 2),
                "negative_ratio": round(negative_count / total * 100, 2),
                "avg_sentiment_score": round(avg_sentiment, 3),
                "total_news": total,
            },
            "key_factors": list(set(key_factors))[:10],
            "trend": self._get_sentiment_trend(target_date, db),
        }
    
    def _get_sentiment_label(self, score: float) -> str:
        """获取情绪标签"""
        if score < self.MARKET_INDICATORS["fear_greed_thresholds"]["extreme_fear"]:
            return "extreme_fear"
        elif score < self.MARKET_INDICATORS["fear_greed_thresholds"]["fear"]:
            return "fear"
        elif score < self.MARKET_INDICATORS["fear_greed_thresholds"]["neutral"]:
            return "neutral"
        elif score < self.MARKET_INDICATORS["fear_greed_thresholds"]["greed"]:
            return "greed"
        else:
            return "extreme_greed"
    
    def _get_sentiment_trend(self, target_date: date, db: Session) -> str:
        """获取情绪趋势"""
        sentiments_7d = db.query(SentimentData).filter(
            SentimentData.data_date >= target_date - timedelta(days=7),
            SentimentData.data_date <= target_date,
        ).all()
        
        if len(sentiments_7d) < 3:
            return "insufficient_data"
        
        daily_scores = {}
        for s in sentiments_7d:
            if s.data_date not in daily_scores:
                daily_scores[s.data_date] = []
            daily_scores[s.data_date].append(s.sentiment_score or 0)
        
        avg_scores = []
        for d in sorted(daily_scores.keys()):
            avg_scores.append(sum(daily_scores[d]) / len(daily_scores[d]))
        
        if len(avg_scores) < 2:
            return "stable"
        
        recent_avg = sum(avg_scores[-3:]) / 3
        earlier_avg = sum(avg_scores[:-3]) / len(avg_scores[:-3]) if len(avg_scores) > 3 else avg_scores[0]
        
        change = recent_avg - earlier_avg
        
        if change > 0.1:
            return "improving"
        elif change < -0.1:
            return "deteriorating"
        else:
            return "stable"
    
    def calculate_market_volatility(self, db: Session, days: int = 20) -> Dict:
        """计算市场波动率"""
        etfs = db.query(ETFBasic).limit(10).all()
        etf_codes = [e.etf_code for e in etfs]
        
        all_volatilities = []
        
        for code in etf_codes:
            quotations = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(days).all()
            
            if len(quotations) < days:
                continue
            
            prices = [q.close_price for q in reversed(quotations)]
            returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
            
            if returns:
                volatility = np.std(returns) * np.sqrt(252)
                all_volatilities.append(volatility)
        
        if not all_volatilities:
            return {"volatility": 0, "level": "unknown"}
        
        avg_volatility = np.mean(all_volatilities)
        
        if avg_volatility < 0.1:
            level = "low"
        elif avg_volatility < 0.2:
            level = "moderate"
        elif avg_volatility < 0.3:
            level = "high"
        else:
            level = "extreme"
        
        return {
            "volatility": round(avg_volatility, 4),
            "level": level,
            "percentile": self._calculate_volatility_percentile(avg_volatility, db),
        }
    
    def _calculate_volatility_percentile(self, current_vol: float, db: Session) -> float:
        """计算波动率百分位"""
        etfs = db.query(ETFBasic).limit(5).all()
        
        historical_vols = []
        for etf in etfs:
            quotations = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == etf.etf_code
            ).order_by(ETFQuotation.trade_date.desc()).limit(60).all()
            
            if len(quotations) >= 20:
                for i in range(20, len(quotations)):
                    window = [q.close_price for q in quotations[i:i+20]]
                    returns = [(window[j] - window[j-1]) / window[j-1] for j in range(1, len(window))]
                    if returns:
                        historical_vols.append(np.std(returns) * np.sqrt(252))
        
        if not historical_vols:
            return 50
        
        count_below = sum(1 for v in historical_vols if v < current_vol)
        return round(count_below / len(historical_vols) * 100, 1)
    
    def find_similar_market_environments(
        self, 
        strategy_id: int, 
        target_date: date, 
        db: Session, 
        top_k: int = 5
    ) -> List[Dict]:
        """查找相似的历史市场环境"""
        current_env = self._get_market_environment(target_date, db)
        
        snapshots = db.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.strategy_id == strategy_id,
            PortfolioSnapshot.trade_date < target_date,
        ).order_by(PortfolioSnapshot.trade_date.desc()).limit(90).all()
        
        if not snapshots:
            return []
        
        similar_environments = []
        
        for snapshot in snapshots:
            historical_env = self._get_market_environment(snapshot.trade_date, db)
            
            similarity = self._calculate_environment_similarity(current_env, historical_env)
            
            next_snapshot = db.query(PortfolioSnapshot).filter(
                PortfolioSnapshot.strategy_id == strategy_id,
                PortfolioSnapshot.trade_date > snapshot.trade_date,
            ).order_by(PortfolioSnapshot.trade_date.asc()).first()
            
            future_return = None
            if next_snapshot and snapshot.total_asset > 0:
                future_return = (next_snapshot.total_asset - snapshot.total_asset) / snapshot.total_asset * 100
            
            similar_environments.append({
                "date": snapshot.trade_date.isoformat(),
                "similarity": round(similarity, 3),
                "allocation": snapshot.allocation_config,
                "future_return": round(future_return, 2) if future_return else None,
                "environment": historical_env,
            })
        
        similar_environments.sort(key=lambda x: x["similarity"], reverse=True)
        
        return similar_environments[:top_k]
    
    def _get_market_environment(self, target_date: date, db: Session) -> Dict:
        """获取市场环境特征"""
        sentiment = self.build_market_sentiment_index(target_date, db)
        volatility = self.calculate_market_volatility(db)
        
        etfs = db.query(ETFBasic).limit(5).all()
        trends = []
        
        for etf in etfs:
            quotations = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == etf.etf_code,
                ETFQuotation.trade_date <= target_date,
            ).order_by(ETFQuotation.trade_date.desc()).limit(10).all()
            
            if len(quotations) >= 5:
                prices = [q.close_price for q in reversed(quotations)]
                change_5d = (prices[-1] - prices[-5]) / prices[-5] * 100
                trends.append({
                    "code": etf.etf_code,
                    "trend_5d": round(change_5d, 2),
                })
        
        return {
            "sentiment_index": sentiment["index"],
            "sentiment_label": sentiment["label"],
            "volatility": volatility["volatility"],
            "volatility_level": volatility["level"],
            "etf_trends": trends,
        }
    
    def _calculate_environment_similarity(self, env1: Dict, env2: Dict) -> float:
        """计算市场环境相似度"""
        weights = self.MARKET_INDICATORS["similarity_weights"]
        
        sent_diff = abs(env1.get("sentiment_index", 50) - env2.get("sentiment_index", 50)) / 100
        sentiment_sim = 1 - sent_diff
        
        vol1 = env1.get("volatility", 0.2)
        vol2 = env2.get("volatility", 0.2)
        vol_diff = abs(vol1 - vol2) / max(vol1, vol2, 0.01)
        volatility_sim = 1 - vol_diff
        
        trend_sim = self._calculate_trend_similarity(
            env1.get("etf_trends", []),
            env2.get("etf_trends", [])
        )
        
        volume_sim = 0.5
        
        similarity = (
            sentiment_sim * weights["sentiment"] +
            trend_sim * weights["trend"] +
            volatility_sim * weights["volatility"] +
            volume_sim * weights["volume"]
        )
        
        return similarity
    
    def _calculate_trend_similarity(self, trends1: List[Dict], trends2: List[Dict]) -> float:
        """计算趋势相似度"""
        if not trends1 or not trends2:
            return 0.5
        
        codes1 = {t["code"]: t["trend_5d"] for t in trends1}
        codes2 = {t["code"]: t["trend_5d"] for t in trends2}
        
        common_codes = set(codes1.keys()) & set(codes2.keys())
        
        if not common_codes:
            return 0.5
        
        similarities = []
        for code in common_codes:
            t1 = codes1[code]
            t2 = codes2[code]
            
            if t1 * t2 > 0:
                diff = abs(abs(t1) - abs(t2))
                sim = 1 - min(diff / 10, 1)
            else:
                sim = 0
            
            similarities.append(sim)
        
        return sum(similarities) / len(similarities) if similarities else 0.5
    
    def get_market_regime(self, target_date: date, db: Session) -> Dict:
        """识别市场阶段"""
        sentiment = self.build_market_sentiment_index(target_date, db)
        volatility = self.calculate_market_volatility(db)
        
        regime = "neutral"
        characteristics = []
        
        if sentiment["index"] > 70:
            if volatility["level"] in ["low", "moderate"]:
                regime = "bull_quiet"
                characteristics = ["情绪高涨", "波动较低", "适合持有"]
            else:
                regime = "bull_volatile"
                characteristics = ["情绪高涨", "波动较大", "注意风险"]
        elif sentiment["index"] < 30:
            if volatility["level"] in ["high", "extreme"]:
                regime = "bear_panic"
                characteristics = ["恐慌情绪", "波动剧烈", "谨慎操作"]
            else:
                regime = "bear_quiet"
                characteristics = ["情绪低迷", "波动较低", "可能超跌"]
        else:
            if volatility["level"] == "extreme":
                regime = "crisis"
                characteristics = ["市场动荡", "波动极端", "避险为主"]
            else:
                regime = "neutral"
                characteristics = ["情绪中性", "观望为主"]
        
        return {
            "regime": regime,
            "characteristics": characteristics,
            "confidence": self._calculate_regime_confidence(sentiment, volatility),
            "suggested_action": self._get_regime_action(regime),
        }
    
    def _calculate_regime_confidence(self, sentiment: Dict, volatility: Dict) -> float:
        """计算阶段识别置信度"""
        sent_confidence = 1 - abs(sentiment["index"] - 50) / 50
        
        if volatility["level"] in ["low", "moderate"]:
            vol_confidence = 0.8
        else:
            vol_confidence = 0.6
        
        return round((sent_confidence + vol_confidence) / 2, 2)
    
    def _get_regime_action(self, regime: str) -> str:
        """获取阶段建议行动"""
        actions = {
            "bull_quiet": "维持或适度加仓，关注趋势持续信号",
            "bull_volatile": "谨慎加仓，设置止盈点，防范回调风险",
            "bear_quiet": "轻仓观望，寻找超跌反弹机会",
            "bear_panic": "空仓或避险，等待情绪企稳信号",
            "crisis": "止损离场，转向低风险资产",
            "neutral": "均衡配置，灵活调整仓位",
        }
        return actions.get(regime, "谨慎操作")