"""舆情数据模型"""

from sqlalchemy import Column, String, Integer, Text, Float, Date, DateTime, JSON, Index
from datetime import datetime
from app.db.database import Base


class SentimentData(Base):
    """舆情数据表 - 存储财经新闻和市场情绪"""
    __tablename__ = "sentiment_data"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    data_date = Column(Date, nullable=False, index=True, comment="数据日期")
    
    # 来源信息
    source = Column(String(20), nullable=False, comment="数据源: eastmoney/jin10/shuku")
    data_type = Column(String(20), nullable=False, comment="数据类型: news/flash/sentiment_index")
    
    # 内容
    title = Column(String(200), nullable=True, comment="标题")
    content = Column(Text, nullable=True, comment="内容摘要")
    url = Column(String(500), nullable=True, comment="原文链接")
    publish_time = Column(DateTime, nullable=True, comment="发布时间")
    
    # 情感分析结果（LLM填充）
    sentiment_score = Column(Float, nullable=True, comment="情感分数: -1到1")
    sentiment_label = Column(String(10), nullable=True, comment="情感标签: positive/negative/neutral")
    related_etfs = Column(JSON, nullable=True, comment="相关ETF代码列表")
    key_factors = Column(JSON, nullable=True, comment="关键因素")
    
    # 情绪指数数据（数库科技）
    sentiment_index_value = Column(Float, nullable=True, comment="情绪指数值")
    
    created_at = Column(DateTime, default=datetime.utcnow, comment="入库时间")
    
    # 索引
    __table_args__ = (
        Index('idx_sentiment_date_type', 'data_date', 'data_type'),
    )
    
    def __repr__(self):
        return f"<SentimentData {self.data_date} {self.source}: {self.title[:30] if self.title else 'N/A'}...>"
    
    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "data_date": self.data_date.isoformat() if self.data_date else None,
            "source": self.source,
            "data_type": self.data_type,
            "title": self.title,
            "content": self.content,
            "sentiment_score": self.sentiment_score,
            "sentiment_label": self.sentiment_label,
            "related_etfs": self.related_etfs,
            "key_factors": self.key_factors,
            "publish_time": self.publish_time.isoformat() if self.publish_time else None,
        }