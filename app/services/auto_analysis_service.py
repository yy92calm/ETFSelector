"""AI市场分析服务"""

import logging
import json
import re
from datetime import date
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
    """AI市场分析服务 - 综合舆情和净值数据进行分析"""
    
    MARKET_ANALYSIS_PROMPT = """你是专业的ETF市场分析师。

## 今日数据汇总

### 舆情数据：
{sentiment_summary}

### 当前配置ETF净值变化（近5日）：
{nav_changes}

### 当前配置：
{current_allocation}

### ⚠️ 可用ETF列表（仅限从中选择）：
{available_etfs}

### 历史经验参考：
{experience_section}

## 分析要求

请综合以上数据，分析当前市场状态，给出策略建议。

返回JSON格式（不要包含其他文字）：
{{
  "market_sentiment": "bullish/bearish/neutral",
  "sentiment_score": 0.3,
  "confidence_level": "high/medium/low",
  "positive_factors": ["政策利好", "资金流入"],
  "negative_factors": ["估值偏高"],
  "suggested_action": "hold/rebalance",
  "suggested_allocation": {{}},
  "action_reason": "基于舆情和市场数据分析，建议..."
}}

⚠️ 严格约束：
- allocation比例总和必须等于1.0
- **suggested_allocation中的ETF代码必须来自"可用ETF列表"中的代码，使用其他代码将无效**
- 参考历史经验，避免重复踩坑"""

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def analyze_market(self, strategy_id: int, analysis_date: date, db: Session) -> Dict:
        """综合分析市场状态"""
        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            return {"error": "策略不存在"}
        
        current_allocation = strategy.allocation_config or {}
        
        sentiment_summary = self._get_sentiment_summary(analysis_date, db)
        nav_changes = self._get_nav_changes(current_allocation.keys(), db)
        available_etfs = self._get_available_etfs(db)
        experiences = self._get_relevant_experiences(strategy_id, db)
        
        prompt = self.MARKET_ANALYSIS_PROMPT.format(
            sentiment_summary=json.dumps(sentiment_summary, ensure_ascii=False, indent=2),
            nav_changes=json.dumps(nav_changes, ensure_ascii=False, indent=2),
            current_allocation=json.dumps(current_allocation, ensure_ascii=False, indent=2),
            available_etfs=available_etfs,
            experience_section=self._format_experiences(experiences),
        )
        
        analysis_result = self._call_llm(prompt)
        
        if analysis_result and "error" not in analysis_result:
            strategy.last_analysis_result = analysis_result
            strategy.last_auto_analysis_date = analysis_date
            db.commit()
            logger.info(f"策略{strategy_id}分析完成: {analysis_result.get('market_sentiment')}")
        
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
    
    def _get_relevant_experiences(self, strategy_id: int, db: Session) -> List[Experience]:
        """获取相关历史经验"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.expires_date >= date.today(),
        ).order_by(Experience.effectiveness_score.desc()).limit(5).all()
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