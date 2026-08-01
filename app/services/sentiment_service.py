"""舆情数据采集服务"""

import logging
import json
import re
from datetime import date, datetime
from typing import List, Dict
from openai import OpenAI
from sqlalchemy.orm import Session

import requests

from app.config import get_settings
from app.models.sentiment import SentimentData
from app.models.etf import ETFBasic

logger = logging.getLogger(__name__)
settings = get_settings()


class SentimentService:
    """舆情数据采集服务 - 直接HTTP采集财经新闻"""
    
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

    POSITIVE_KEYWORDS = [
        "大涨", "利好", "反弹", "突破", "增长", "上涨", "牛市", "看涨", "盈利",
        "涨停", "回暖", "复苏", "走强", "新高", "放量", "超预期", "增持", "回购",
        "分红", "降息", "放水", "刺激", "加仓", "做多", "爆发", "拉升", "领涨",
    ]

    NEGATIVE_KEYWORDS = [
        "大跌", "利空", "暴跌", "破位", "下跌", "熊市", "看跌", "亏损", "跌停",
        "回调", "疲软", "走弱", "新低", "缩量", "减持", "加息", "收紧", "清仓",
        "做空", "违约", "崩盘", "危机", "退市", "抛售", "st", "风险", "利空",
    ]

    def __init__(self):
        self.llm_client = None
        if settings.llm_api_key and settings.llm_api_key.strip():
            self.llm_client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_api_base_url,
            )
    
    def collect_daily_sentiment(self, collect_date: date, db: Session) -> Dict:
        """采集指定日期的舆情数据（含去重）"""
        result = {
            "news_count": 0,
            "sentiment_index": None,
            "errors": []
        }
        
        try:
            news_list = self._fetch_financial_news()
            result["news_count"] = len(news_list)

            # 去重：查询当天已有标题，避免重复入库
            existing_titles = set(
                r[0] for r in db.query(SentimentData.title)
                .filter(SentimentData.data_date == collect_date)
                .all()
                if r[0]
            )
            
            added_count = 0
            for news_item in news_list:
                title = news_item.get("title", "").strip()
                if not title or title in existing_titles:
                    continue
                existing_titles.add(title)

                sentiment_data = SentimentData(
                    data_date=collect_date,
                    source=news_item.get("source", "eastmoney"),
                    data_type="news",
                    title=title,
                    content=news_item.get("content"),
                    publish_time=news_item.get("publish_time"),
                )
                db.add(sentiment_data)
                db.flush()
                added_count += 1
                
                analysis = self._analyze_sentiment(news_item, db)
                if analysis:
                    sentiment_data.sentiment_score = analysis.get("sentiment_score")
                    sentiment_data.sentiment_label = analysis.get("sentiment_label")
                    if analysis.get("related_etfs"):
                        sentiment_data.related_etfs = analysis.get("related_etfs")
                    if analysis.get("key_factors"):
                        sentiment_data.key_factors = analysis.get("key_factors")
            
            db.commit()
            logger.info(f"舆情采集完成: 获取{result['news_count']}条, 新增{added_count}条(去重后)")

            # 回填：对当天已有的未评分舆情进行关键词评分（兼容历史数据）
            unscored = db.query(SentimentData).filter(
                SentimentData.data_date == collect_date,
                SentimentData.sentiment_score.is_(None),
            ).all()
            if unscored:
                logger.info(f"回填 {len(unscored)} 条未评分舆情")
                for item in unscored:
                    analysis = self._analyze_sentiment_by_keywords({
                        "title": item.title or "",
                        "content": item.content or "",
                    })
                    if analysis:
                        item.sentiment_score = analysis.get("sentiment_score")
                        item.sentiment_label = analysis.get("sentiment_label")
                        if analysis.get("key_factors"):
                            item.key_factors = analysis.get("key_factors")
                db.commit()
            
        except Exception as e:
            logger.error(f"舆情采集失败: {e}")
            result["errors"].append(str(e))
            db.rollback()
        
        return result
    
    def _fetch_financial_news(self) -> List[Dict]:
        """获取财经快讯（直接HTTP, 不依赖akshare）"""
        news_list = []
        
        # 1. 东方财富全球快讯 (稳定)
        try:
            resp = requests.get(
                "https://np-weblist.eastmoney.com/comm/web/getFastNewsList",
                params={"client": "web", "biz": "web_724", "fastColumn": "102",
                        "sortEnd": "", "pageSize": "15", "req_trace": "1"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            data = resp.json()
            for item in data.get("data", {}).get("fastNewsList", []):
                news_list.append({
                    "source": "eastmoney",
                    "title": item.get("title", ""),
                    "content": item.get("summary", ""),
                    "publish_time": self._parse_time(item.get("showTime")),
                })
        except Exception as e:
            logger.warning(f"东方财富快讯获取失败: {e}")
        
        # 2. 同花顺资讯
        try:
            resp = requests.get(
                "https://news.10jqka.com.cn/tapp/news/push/stock",
                params={"page": "1", "tag": "", "pagesize": "15"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            data = resp.json()
            for item in data.get("data", {}).get("list", []):
                news_list.append({
                    "source": "ths",
                    "title": item.get("title", ""),
                    "content": item.get("digest", ""),
                    "publish_time": None,
                })
        except Exception as e:
            logger.warning(f"同花顺资讯获取失败: {e}")
        
        # 3. 东方财富热门股（市场热点）
        try:
            resp = requests.post(
                "https://emappdata.eastmoney.com/stockrank/getAllCurrentList",
                json={"appId": "appId01", "globalId": "news_bot",
                       "marketType": "", "pageNo": 1, "pageSize": 10},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            data = resp.json()
            items = data.get("data", [])[:10]
            if items:
                # API字段: sc="SH688825", rk=排名, rc=排名变化
                lines = []
                for i in items:
                    code = i.get('sc', '')
                    rank = i.get('rk', '')
                    rc = i.get('rc', 0)
                    trend = '↑' if rc > 0 else ('↓' if rc < 0 else '→')
                    lines.append(f"{code}(第{rank}名{trend})")
                news_list.append({
                    "source": "eastmoney_hot",
                    "title": "今日热门股排行榜",
                    "content": "市场热门股票: " + ", ".join(lines),
                    "publish_time": datetime.now(),
                })
        except Exception as e:
            logger.warning(f"热门股排行获取失败: {e}")
        
        # 4. 东方财富热门关键词
        try:
            resp = requests.post(
                "https://emappdata.eastmoney.com/stockrank/getHotStockRankList",
                json={"appId": "appId01", "globalId": "news_bot",
                       "srcSecurityCode": "SZ000665"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=15,
            )
            data = resp.json()
            items = data.get("data", [])[:10]
            if items:
                lines = [f"{i.get('conceptName','')}(热度{i.get('hitCount','')})" for i in items]
                news_list.append({
                    "source": "eastmoney_keyword",
                    "title": "今日热门概念关键词",
                    "content": "市场热门概念: " + ", ".join(lines),
                    "publish_time": datetime.now(),
                })
        except Exception as e:
            logger.warning(f"热门关键词获取失败: {e}")
        
        logger.info(f"采集舆情数据: {len(news_list)}条")
        return news_list
    
    def _analyze_sentiment(self, news_item: Dict, db: Session) -> Dict:
        """分析舆情情感（LLM优先，失败时回退到关键词匹配）"""
        if self.llm_client:
            analysis = self._analyze_sentiment_with_llm(news_item, db)
            if analysis and analysis.get("sentiment_score") is not None:
                return analysis

        return self._analyze_sentiment_by_keywords(news_item)

    def _analyze_sentiment_by_keywords(self, news_item: Dict) -> Dict:
        """关键词匹配情感分析（无需LLM）"""
        text = f"{news_item.get('title', '')} {news_item.get('content', '')}"
        text_lower = text.lower()

        positive_count = sum(1 for kw in self.POSITIVE_KEYWORDS if kw in text)
        negative_count = sum(1 for kw in self.NEGATIVE_KEYWORDS if kw in text)

        total = positive_count + negative_count
        if total == 0:
            return {
                "sentiment_score": 0.0,
                "sentiment_label": "neutral",
                "key_factors": [],
            }

        score = round((positive_count - negative_count) / (total + 2) * 2, 2)
        score = max(-1.0, min(1.0, score))

        label = "positive" if score > 0.1 else "negative" if score < -0.1 else "neutral"

        factors = []
        if positive_count > 0:
            factors.extend([kw for kw in self.POSITIVE_KEYWORDS if kw in text_lower][:3])
        if negative_count > 0:
            factors.extend([kw for kw in self.NEGATIVE_KEYWORDS if kw in text_lower][:3])

        return {
            "sentiment_score": score,
            "sentiment_label": label,
            "key_factors": factors,
        }

    def _analyze_sentiment_with_llm(self, news_item: Dict, db: Session) -> Dict:
        """使用LLM分析舆情情感"""
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
    
    def reanalyze_all(self, target_date: date = None, db: Session = None) -> int:
        """批量重算已有null-score的舆情（关键词回退，不依赖LLM/AKShare）"""
        query = db.query(SentimentData).filter(SentimentData.sentiment_score.is_(None))
        if target_date:
            query = query.filter(SentimentData.data_date == target_date)

        items = query.all()
        if not items:
            return 0

        count = 0
        for item in items:
            analysis = self._analyze_sentiment_by_keywords({
                "title": item.title or "",
                "content": item.content or "",
            })
            if analysis and analysis.get("sentiment_score") is not None:
                item.sentiment_score = analysis.get("sentiment_score")
                item.sentiment_label = analysis.get("sentiment_label")
                if analysis.get("key_factors"):
                    item.key_factors = analysis.get("key_factors")
                count += 1

        db.commit()
        logger.info(f"重新分析舆情评分: {count}/{len(items)} 条已更新")
        return count

    def _parse_json_response(self, content: str) -> Dict:
        """解析JSON响应"""
        try:
            match = re.search(r'\{[\s\S]*\}', content)
            if match:
                return json.loads(match.group())
        except:
            pass
        return {}