"""舆情数据采集服务"""

import logging
import json
import re
from datetime import date, datetime
from typing import List, Dict
from openai import OpenAI
from sqlalchemy.orm import Session

import akshare as ak

from app.config import get_settings
from app.models.sentiment import SentimentData
from app.models.etf import ETFBasic

logger = logging.getLogger(__name__)
settings = get_settings()


class SentimentService:
    """舆情数据采集服务 - 使用AKShare获取财经新闻"""
    
    SENTIMENT_ANALYSIS_PROMPT = """分析以下财经新闻的情感倾向：

标题: {title}
内容: {content}

⚠️ 可用ETF列表（仅限从中选择）：
{available_etfs}

请返回JSON格式（不要包含其他文字）：
{
  "sentiment_score": 0.5,
  "sentiment_label": "positive",
  "related_etfs": [],
  "key_factors": ["政策利好"]
}

字段说明：
- sentiment_score: -1到1的情感分数，正面为正数，负面为负数
- sentiment_label: positive/negative/neutral
- related_etfs: 相关的ETF代码列表，必须来自可用ETF列表
- key_factors: 影响因素关键词列表"""

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def collect_daily_sentiment(self, collect_date: date, db: Session) -> Dict:
        """采集指定日期的舆情数据"""
        result = {
            "news_count": 0,
            "sentiment_index": None,
            "errors": []
        }
        
        try:
            news_list = self._fetch_financial_news()
            result["news_count"] = len(news_list)
            
            for news_item in news_list:
                sentiment_data = SentimentData(
                    data_date=collect_date,
                    source=news_item.get("source", "eastmoney"),
                    data_type="news",
                    title=news_item.get("title"),
                    content=news_item.get("content"),
                    publish_time=news_item.get("publish_time"),
                )
                db.add(sentiment_data)
                db.flush()
                
                if self.llm_client:
                    analysis = self._analyze_sentiment_with_llm(news_item, db)
                    if analysis:
                        sentiment_data.sentiment_score = analysis.get("sentiment_score")
                        sentiment_data.sentiment_label = analysis.get("sentiment_label")
                        sentiment_data.related_etfs = analysis.get("related_etfs")
                        sentiment_data.key_factors = analysis.get("key_factors")
            
            db.commit()
            logger.info(f"舆情采集完成: {result['news_count']}条")
            
        except Exception as e:
            logger.error(f"舆情采集失败: {e}")
            result["errors"].append(str(e))
            db.rollback()
        
        return result
    
    def _fetch_financial_news(self) -> List[Dict]:
        """获取财经快讯（多数据源）"""
        news_list = []
        
        # 1. 东方财富全球快讯
        try:
            df = ak.stock_info_global_em()
            for idx, row in df.head(15).iterrows():
                news_list.append({
                    "source": "eastmoney",
                    "title": row.get("标题", ""),
                    "content": row.get("摘要", "") or row.get("内容", ""),
                    "publish_time": self._parse_time(row.get("发布时间")),
                })
        except Exception as e:
            logger.warning(f"东方财富快讯获取失败: {e}")
        
        # 2. 财联社资讯
        try:
            df = ak.stock_info_global_cls()
            for idx, row in df.head(15).iterrows():
                news_list.append({
                    "source": "cls",
                    "title": row.get("标题", ""),
                    "content": row.get("内容", ""),
                    "publish_time": self._parse_datetime(row.get("发布日期"), row.get("发布时间")),
                })
        except Exception as e:
            logger.warning(f"财联社资讯获取失败: {e}")
        
        # 3. 同花顺资讯
        try:
            df = ak.stock_info_global_ths()
            for idx, row in df.head(15).iterrows():
                news_list.append({
                    "source": "ths",
                    "title": row.get("标题", ""),
                    "content": row.get("内容", ""),
                    "publish_time": self._parse_time(row.get("发布时间")),
                })
        except Exception as e:
            logger.warning(f"同花顺资讯获取失败: {e}")
        
        # 4. 新浪资讯
        try:
            df = ak.stock_info_global_sina()
            for idx, row in df.head(10).iterrows():
                news_list.append({
                    "source": "sina",
                    "title": "",
                    "content": row.get("内容", ""),
                    "publish_time": self._parse_time(row.get("时间")),
                })
        except Exception as e:
            logger.warning(f"新浪资讯获取失败: {e}")
        
        # 5. 金十数据
        try:
            df = ak.js_news(indicator="最新资讯")
            for idx, row in df.head(10).iterrows():
                news_list.append({
                    "source": "jin10",
                    "title": "",
                    "content": row.get("content", ""),
                    "publish_time": self._parse_time(row.get("datetime")),
                })
        except Exception as e:
            logger.warning(f"金十数据快讯获取失败: {e}")
        
        # 6. 东方财富热门股（市场热点）
        try:
            df = ak.stock_hot_rank_em()
            hot_stocks = df.head(10).to_string()
            if hot_stocks:
                news_list.append({
                    "source": "eastmoney_hot",
                    "title": "今日热门股排行榜",
                    "content": f"市场热门股票: {hot_stocks}",
                    "publish_time": datetime.now(),
                })
        except Exception as e:
            logger.warning(f"热门股排行获取失败: {e}")
        
        # 7. 东方财富热门关键词
        try:
            df = ak.stock_hot_keyword_em()
            keywords = df.groupby('概念名称')['热度'].sum().sort_values(ascending=False).head(10)
            keyword_str = keywords.to_string()
            if keyword_str:
                news_list.append({
                    "source": "eastmoney_keyword",
                    "title": "今日热门概念关键词",
                    "content": f"市场热门概念: {keyword_str}",
                    "publish_time": datetime.now(),
                })
        except Exception as e:
            logger.warning(f"热门关键词获取失败: {e}")
        
        logger.info(f"采集舆情数据: {len(news_list)}条 (来源: eastmoney/cls/ths/sina/jin10/热点)")
        return news_list
    
    def _parse_datetime(self, date_str, time_str) -> datetime:
        """解析日期+时间字符串"""
        if not date_str:
            return None
        try:
            date_part = str(date_str)
            time_part = str(time_str) if time_str else "00:00:00"
            return datetime.strptime(f"{date_part} {time_part}", "%Y-%m-%d %H:%M:%S")
        except:
            return None
    
    def _analyze_sentiment_with_llm(self, news_item: Dict, db: Session) -> Dict:
        """使用LLM分析舆情情感"""
        if not self.llm_client:
            return {}
        
        available_etfs = self._get_available_etfs(db)
        
        try:
            prompt = self.SENTIMENT_ANALYSIS_PROMPT.format(
                title=news_item.get("title", "无标题"),
                content=news_item.get("content", "无内容"),
                available_etfs=available_etfs
            )
            
            response = self.llm_client.chat.completions.create(
                model=settings.llm_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=500,
            )
            
            content = response.choices[0].message.content
            return self._parse_json_response(content)
            
        except Exception as e:
            logger.warning(f"LLM情感分析失败: {e}")
            return {}
    
    def get_sentiment_summary(self, target_date: date, db: Session) -> Dict:
        """获取指定日期的舆情汇总"""
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == target_date
        ).all()
        
        if not sentiments:
            return {"total": 0, "positive": 0, "negative": 0, "neutral": 0}
        
        positive = sum(1 for s in sentiments if s.sentiment_label == "positive")
        negative = sum(1 for s in sentiments if s.sentiment_label == "negative")
        neutral = sum(1 for s in sentiments if s.sentiment_label == "neutral")
        
        avg_score = sum(s.sentiment_score or 0 for s in sentiments) / len(sentiments)
        
        return {
            "total": len(sentiments),
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "avg_score": round(avg_score, 2),
            "sentiments": [s.to_dict() for s in sentiments[:10]],
        }
    
    def _parse_time(self, time_str) -> datetime:
        """解析时间字符串"""
        if not time_str:
            return None
        try:
            return datetime.strptime(str(time_str), "%Y-%m-%d %H:%M:%S")
        except:
            return None
    
    def _get_available_etfs(self, db: Session) -> str:
        """从数据库获取所有可用ETF列表"""
        etfs = db.query(ETFBasic).all()
        
        if not etfs:
            return "暂无可用ETF"
        
        lines = [f"{etf.etf_code}: {etf.etf_name}" for etf in etfs]
        return "\n".join(lines)
    
    def _parse_json_response(self, content: str) -> Dict:
        """解析JSON响应"""
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except:
            pass
        return {}