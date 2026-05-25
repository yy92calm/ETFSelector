"""AI市场分析服务"""

import logging
import json
import re
from datetime import date, timedelta
from typing import Dict, List
from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.strategy import Strategy
from app.models.sentiment import SentimentData
from app.models.etf import ETFQuotation, ETFBasic
from app.models.experience import Experience

logger = logging.getLogger(__name__)
settings = get_settings()


class AutoAnalysisService:
    """AI市场分析服务 - 综合舆情、技术指标、历史模式进行分析"""
    
    MARKET_ANALYSIS_PROMPT = """你是专业的ETF市场分析师。

## 今日数据汇总

### 市场情绪指数：
{sentiment_index}

### 舆情关键信息：
{sentiment_summary}

### 技术指标分析：
{technical_indicators}

### 当前配置ETF表现：
{nav_changes}

### 当前配置：
{current_allocation}

### 历史相似环境案例：
{similar_environments}

### 历史经验参考：
{experience_section}

### ⚠️ 可用ETF列表（仅限从中选择）：
{available_etfs}

## 分析要求

请综合以上多维度数据，进行深度分析：

1. **市场环境识别**：判断当前处于何种市场阶段
2. **技术面分析**：结合MA、MACD、RSI等技术指标
3. **历史模式参考**：借鉴相似环境下的历史表现
4. **经验应用**：应用历史经验避免踩坑

返回JSON格式（不要包含其他文字）：
{{
  "market_regime": "bull_quiet/bull_volatile/bear_quiet/bear_panic/crisis/neutral",
  "market_sentiment": "bullish/bearish/neutral",
  "sentiment_score": 0.3,
  "confidence_level": "high/medium/low",
  "technical_signals": {{
    "overall_trend": "strong_bullish/bullish/bearish/strong_bearish/neutral",
    "key_indicators": ["MA5突破MA10", "RSI超买"],
    "strength": 0.8
  }},
  "historical_pattern": {{
    "similar_case_count": 3,
    "avg_future_return": 2.5,
    "success_rate": 0.67
  }},
  "positive_factors": ["政策利好", "技术突破"],
  "negative_factors": ["估值偏高", "资金流出"],
  "suggested_action": "hold/rebalance",
  "suggested_allocation": {{}},
  "action_reason": "基于多维度分析...",
  "risk_alert": {{
    "level": "low/medium/high",
    "factors": ["波动率上升", "情绪过热"]
  }}
}}

⚠️ 严格约束：
- allocation比例总和必须等于1.0
- **suggested_allocation中的ETF代码必须来自"可用ETF列表"中的代码**
- 参考相似历史环境的表现数据
- 结合技术指标信号做出决策
- 优先应用失败经验避免踩坑"""

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def analyze_market(self, strategy_id: int, analysis_date: date, db: Session) -> Dict:
        """综合分析市场状态 - 多维度增强版"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}
        
        current_allocation = strategy.allocation_config or {}
        
        sentiment_index = self._build_sentiment_index(analysis_date, db)
        sentiment_summary = self._get_sentiment_summary(analysis_date, db)
        technical_indicators = self._get_technical_indicators(current_allocation.keys(), db)
        nav_changes = self._get_nav_changes(current_allocation.keys(), db)
        similar_environments = self._find_similar_environments(strategy_id, analysis_date, db)
        experiences = self._get_relevant_experiences(strategy_id, analysis_date, db)
        available_etfs = self._get_available_etfs(db)
        
        prompt = self.MARKET_ANALYSIS_PROMPT.format(
            sentiment_index=json.dumps(sentiment_index, ensure_ascii=False, indent=2),
            sentiment_summary=json.dumps(sentiment_summary, ensure_ascii=False, indent=2),
            technical_indicators=json.dumps(technical_indicators, ensure_ascii=False, indent=2),
            nav_changes=json.dumps(nav_changes, ensure_ascii=False, indent=2),
            current_allocation=json.dumps(current_allocation, ensure_ascii=False, indent=2),
            similar_environments=json.dumps(similar_environments, ensure_ascii=False, indent=2),
            experience_section=self._format_experiences(experiences),
            available_etfs=available_etfs,
        )
        
        analysis_result = self._call_llm(prompt)
        
        if analysis_result and "error" not in analysis_result:
            analysis_result["analysis_date"] = analysis_date.isoformat()
            analysis_result["similar_environments_used"] = len(similar_environments)
            analysis_result["technical_indicators_available"] = len(technical_indicators)
            
            strategy.last_analysis_result = analysis_result
            strategy.last_auto_analysis_date = analysis_date
            db.commit()
            logger.info(f"策略{strategy_id}增强分析完成: {analysis_result.get('market_regime')}")
        
        return analysis_result
    
    def _get_sentiment_summary(self, target_date: date, db: Session) -> Dict:
        """获取舆情汇总"""
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == target_date,
            SentimentData.sentiment_label.isnot(None)
        ).all()
        
        if not sentiments:
            return {"total": 0, "message": "无舆情数据"}
        
        positive = sum(1 for s in sentiments if s.sentiment_label == "positive")
        negative = sum(1 for s in sentiments if s.sentiment_label == "negative")
        avg_score = sum(s.sentiment_score or 0 for s in sentiments) / len(sentiments)
        
        key_news = [
            {"title": s.title, "score": s.sentiment_score, "factors": s.key_factors}
            for s in sorted(sentiments, key=lambda x: abs(x.sentiment_score or 0), reverse=True)[:5]
        ]
        
        return {
            "total": len(sentiments),
            "positive_count": positive,
            "negative_count": negative,
            "avg_sentiment_score": round(avg_score, 2),
            "key_news": key_news,
        }
    
    def _get_nav_changes(self, etf_codes: List[str], db: Session) -> Dict:
        """获取指定ETF的净值变化"""
        result = {}
        for code in etf_codes:
            quotations = db.query(ETFQuotation).filter(
                ETFQuotation.etf_code == code
            ).order_by(ETFQuotation.trade_date.desc()).limit(5).all()
            
            if quotations:
                latest = quotations[0]
                change_5d = None
                if len(quotations) >= 5:
                    change_5d = (latest.close_price - quotations[4].close_price) / quotations[4].close_price * 100
                result[code] = {
                    "latest_nav": latest.close_price,
                    "change_5d_pct": round(change_5d, 2) if change_5d else None,
                }
        
        return result
    
    def _get_available_etfs(self, db: Session) -> str:
        """从数据库获取所有可用ETF列表，格式化为prompt字符串"""
        etfs = db.query(ETFBasic).all()
        
        if not etfs:
            return "暂无可用ETF"
        
        lines = [f"{etf.etf_code}: {etf.etf_name}" for etf in etfs]
        return "\n".join(lines)
    
    def _build_sentiment_index(self, target_date: date, db: Session) -> Dict:
        """构建市场情绪指数"""
        from app.services.market_environment_service import MarketEnvironmentService
        env_svc = MarketEnvironmentService()
        return env_svc.build_market_sentiment_index(target_date, db)
    
    def _get_technical_indicators(self, etf_codes: List[str], db: Session) -> Dict:
        """获取技术指标分析"""
        from app.services.technical_indicator_service import TechnicalIndicatorService
        tech_svc = TechnicalIndicatorService()
        
        indicators = tech_svc.batch_calculate_indicators(etf_codes, db)
        
        summary = {
            "overall_trend": "neutral",
            "bullish_count": 0,
            "bearish_count": 0,
            "key_signals": [],
        }
        
        for code, ind in indicators.items():
            if "error" in ind:
                continue
            
            trend = ind.get("trend_signal", {})
            if trend.get("trend") in ["strong_bullish", "bullish"]:
                summary["bullish_count"] += 1
            elif trend.get("trend") in ["strong_bearish", "bearish"]:
                summary["bearish_count"] += 1
            
            rsi = ind.get("rsi", {})
            if rsi.get("signal") == "overbought":
                summary["key_signals"].append(f"{code}: RSI超买({rsi['value']})")
            elif rsi.get("signal") == "oversold":
                summary["key_signals"].append(f"{code}: RSI超卖({rsi['value']})")
            
            macd = ind.get("macd", {})
            if macd.get("trend") == "bullish":
                summary["key_signals"].append(f"{code}: MACD金叉")
            elif macd.get("trend") == "bearish":
                summary["key_signals"].append(f"{code}: MACD死叉")
        
        total = summary["bullish_count"] + summary["bearish_count"]
        if total > 0:
            if summary["bullish_count"] > summary["bearish_count"]:
                summary["overall_trend"] = "bullish"
            elif summary["bearish_count"] > summary["bullish_count"]:
                summary["overall_trend"] = "bearish"
        
        return summary
    
    def _find_similar_environments(self, strategy_id: int, target_date: date, db: Session) -> List[Dict]:
        """查找相似的历史市场环境"""
        from app.services.market_environment_service import MarketEnvironmentService
        env_svc = MarketEnvironmentService()
        
        similar = env_svc.find_similar_market_environments(strategy_id, target_date, db, top_k=5)
        
        summary = []
        for env in similar[:3]:
            summary.append({
                "date": env["date"],
                "similarity": env["similarity"],
                "future_return": env.get("future_return"),
                "allocation": env.get("allocation"),
            })
        
        return summary
    
    def _get_relevant_experiences(self, strategy_id: int, target_date: date, db: Session) -> List[Experience]:
        """获取相关历史经验 - 增强版"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.expires_date >= target_date,
        ).order_by(
            Experience.effectiveness_score.desc(),
            Experience.application_count.desc()
        ).limit(8).all()
        
        return experiences
    
    def _format_experiences(self, experiences: List[Experience]) -> str:
        """格式化经验文本"""
        if not experiences:
            return "暂无历史经验"
        
        sections = []
        for exp in experiences:
            prefix = "【成功经验】" if exp.experience_type == "success" else "【失败教训】" if exp.experience_type == "failure" else "【洞察】"
            sections.append(f"{prefix} {exp.title}: {exp.key_insight or exp.description[:100]}")
        
        return "\n".join(sections)
    
    def _call_llm(self, prompt: str) -> Dict:
        """调用LLM"""
        if not self.llm_client:
            return {"error": "LLM客户端未配置"}
        
        try:
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.5,
                max_tokens=1000,
            )
            
            content = response.choices[0].message.content
            return self._parse_json_response(content)
            
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            return {"error": str(e)}
    
    def _parse_json_response(self, content: str) -> Dict:
        """解析JSON响应"""
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except Exception as e:
            logger.warning(f"JSON解析失败: {e}")
        return {"error": "无法解析响应"}