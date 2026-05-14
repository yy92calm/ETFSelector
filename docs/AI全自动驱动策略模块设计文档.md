# AI全自动驱动策略模块设计文档

> 版本: 1.0
> 日期: 2026-05-10
> 项目: ETFSelector

---

## 一、需求概述

### 1.1 业务目标

新增一个 **AI全自动驱动的ETF策略模块**，实现：
- 📰 自动获取舆情数据（财经新闻、市场情绪指数）
- 🧠 AI自动分析市场状态（舆情+净值综合研判）
- 🔄 动态构建/调整策略配置（无需人工对话）
- 💰 ETF持仓管理，以当日收盘净值成交
- ⏰ 每日20:00自动计算策略调整后的持仓
- 🖥️ 前端新增tab展示全流程状态

### 1.2 与现有系统对比

| 维度 | 现有AI策略 | 新模块 |
|---|---|---|
| **交互方式** | 对话式（用户输入→AI响应） | 全自动无人值守 |
| **触发机制** | 用户点击 | 定时任务驱动 |
| **数据来源** | 用户描述需求 | 舆情自动获取 |
| **策略类型** | static allocation | dynamic allocation |
| **执行频率** | 手动触发 | 每日固定时间 |

---

## 二、现有系统分析

### 2.1 架构概览

```
┌─────────────────────────────────────────────────┐
│                   FastAPI 后端                   │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │etf_routes│ │strategy  │ │backtest  │        │
│  │          │ │_routes   │ │_routes   │        │
│  └──────────┘ └──────────┘ └──────────┘        │
└─────────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────┐
│              SQLAlchemy + SQLite                 │
│  Strategy | ETFQuotation | PortfolioSnapshot    │
│  TradeRecord | Holding                          │
└─────────────────────────────────────────────────┘
```

### 2.2 现有AI策略生成流程

**核心类**: `ETFAllocationAgent` (`app/strategies/generator.py`)

```python
class ETFAllocationAgent:
    SYSTEM_PROMPT = """你是一个专业的ETF配置助手...
    输出格式JSON: {reply, allocation, confidence}"""
    
    def chat_and_generate(self, user_message, chat_history, 
                          current_allocation, model, db):
        # 1. 获取可用ETF列表
        etf_list = self._get_all_etfs(db)
        
        # 2. 构建prompt
        prompt = self._build_prompt(...)
        
        # 3. 调用LLM
        response = self.client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        
        # 4. 解析响应
        return self._parse_llm_response(response.content)
```

**复用点**:
- LLM调用模式（OpenAI SDK）
- SYSTEM_PROMPT 结构设计
- JSON响应解析方式
- ETF列表获取逻辑

### 2.3 现有定时任务

**文件**: `app/tasks/scheduler.py`

| 时间 | 任务 | 说明 |
|---|---|---|
| 18:00/20:00 | `_job_update_net_values` | 更新ETF净值数据 |
| 18:05/20:05 | `_job_run_strategies` | 执行活跃策略 |

```python
_scheduler.add_job(
    _job_update_net_values,
    trigger=CronTrigger(day_of_week='mon-fri', hour=20, minute=0),
)
_scheduler.add_job(
    _job_run_strategies,
    trigger=CronTrigger(day_of_week='mon-fri', hour=20, minute=5),
)
```

### 2.4 现有持仓管理

**文件**: `app/services/portfolio_service.py`

```python
class PortfolioService:
    def run_all_active_strategies(self, db):
        """执行所有活跃策略"""
        strategies = db.query(Strategy).filter(Strategy.status == 'active')
        for strategy in strategies:
            self._execute_strategy(strategy, db)
    
    def _execute_strategy(self, strategy, db):
        # 1. 获取当前净值
        # 2. 计算目标持仓
        # 3. 执行调仓
        # 4. 记录交易
        # 5. 保存快照
```

---

## 三、优化架构设计

### 3.1 设计原则

| 原则 | 说明 |
|---|---|
| **最小化侵入** | 复用现有Strategy表，仅新增必要字段 |
| **复用现有模式** | LLM调用沿用ETFAllocationAgent设计 |
| **分层编排** | 定时任务按数据依赖顺序编排 |
| **安全熔断** | 多层限制防止过度交易 |

### 3.2 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    定时任务调度层                             │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │  19:30   │ │  19:45   │ │  20:00   │ │  20:05   │        │
│  │舆情采集  │ │ AI分析   │ │策略调整  │ │ 持仓计算 │        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                       服务层                                  │
│  ┌────────────────┐ ┌────────────────┐ ┌─────────────────┐  │
│  │SentimentService│ │AutoAnalysisSvc │ │AutoStrategyExec │  │
│  │  舆情采集      │ │  市场分析      │ │   策略执行      │  │
│  │  (新增)        │ │  (新增)        │ │   (新增)        │  │
│  └────────────────┘ └────────────────┘ └─────────────────┘  │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │         复用: ETFAllocationAgent (LLM调用模式)           ││
│  │         复用: PortfolioService (持仓计算)               ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                       数据层                                  │
│  ┌─────────────┐ ┌──────────────────┐                       │
│  │SentimentData│ │AutoStrategyLog   │  (新增2表)            │
│  │  舆情数据   │ │  执行日志        │                       │
│  └─────────────┘ └──────────────────┘                       │
│                                                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │              复用: Strategy (增加字段)                    ││
│  │              复用: ETFQuotation, PortfolioSnapshot       ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                       API层                                   │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  /api/auto-strategy/*                                    ││
│  │  - /status      策略状态                                  ││
│  │  - /logs        执行日志                                  ││
│  │  - /sentiments  舆情数据                                  ││
│  │  - /trigger     手动触发                                  ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                       前端层                                  │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  新增Tab: "AI驱动策略"                                    ││
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐                 ││
│  │  │舆情监控  │ │策略状态  │ │执行日志  │                 ││
│  │  └──────────┘ └──────────┘ └──────────┘                 ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

---

## 四、数据层设计（最小化）

### 4.1 Strategy表扩展字段

```python
# app/models/strategy.py (扩展)
class Strategy(Base):
    # 现有字段...
    
    # 新增字段（自动策略专用）
    strategy_source = Column(String(20), default='manual')  # manual/auto_generated
    auto_strategy_status = Column(String(20))               # running/paused/stopped
    last_auto_analysis_date = Column(Date)
    auto_adjustment_count = Column(Integer, default=0)
    max_daily_adjustments = Column(Integer, default=1)      # 每日最大调整次数
    
    # AI分析结果缓存
    last_analysis_result = Column(JSON)                     # 最近的AI分析结果
```

### 4.2 新增SentimentData表

```python
# app/models/sentiment.py (新增)
class SentimentData(Base):
    """舆情数据表"""
    __tablename__ = "sentiment_data"
    
    id = Column(Integer, primary_key=True)
    data_date = Column(Date, index=True)                    # 数据日期
    
    # 来源信息
    source = Column(String(20))                             # akshare/数库科技
    data_type = Column(String(20))                          # news/sentiment_index/flash
    
    # 内容
    title = Column(String(200))                             # 标题
    content = Column(Text)                                  # 内容摘要
    publish_time = Column(DateTime)                         # 发布时间
    
    # 情感分析结果（LLM填充）
    sentiment_score = Column(Float)                         # -1到1
    sentiment_label = Column(String(10))                    # positive/negative/neutral
    related_etfs = Column(JSON)                             # 相关ETF代码列表
    key_factors = Column(JSON)                              # 关键因素
    
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 4.3 新增AutoStrategyLog表

```python
# app/models/auto_strategy_log.py (新增)
class AutoStrategyLog(Base):
    """自动策略执行日志"""
    __tablename__ = "auto_strategy_log"
    
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey('strategy.id'))
    log_date = Column(Date, index=True)
    
    # 执行状态
    status = Column(String(20))                             # success/failed/skipped
    action_type = Column(String(20))                        # analyze/adjust/hold
    
    # 执行详情
    sentiment_summary = Column(JSON)                        # 当日舆情汇总
    analysis_result = Column(JSON)                          # AI分析结果
    adjustment_decision = Column(JSON)                      # 调整决策
    
    # 安全检查
    safety_check_passed = Column(Boolean)
    safety_reason = Column(String(200))                     # 未通过原因
    
    # 执行结果
    old_allocation = Column(JSON)
    new_allocation = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 4.4 数据表关系

```
Strategy (扩展)
    │
    ├─── AutoStrategyLog (新增，一对一关系)
    │         记录每次自动策略执行
    │
    └─── PortfolioSnapshot (复用)
              记录每日持仓快照

SentimentData (新增)
    │
    └─── 被AutoAnalysisService引用
              作为AI分析输入数据
```

---

## 五、服务层设计

### 5.1 SentimentService（舆情采集）

```python
# app/services/sentiment_service.py (新增)
"""
舆情数据采集服务
使用AKShare获取财经新闻和市场情绪指数
"""

import akshare as ak
from typing import List
from sqlalchemy.orm import Session
from app.models.sentiment import SentimentData
from app.strategies.generator import ETFAllocationAgent

class SentimentService:
    """舆情数据采集服务"""
    
    # AKShare舆情接口配置
    SENTIMENT_CONFIG = {
        "news_sources": [
            ("stock_info_global_em", "东方财富财经快讯"),
            ("js_news", "金十数据实时快讯"),
        ],
        "sentiment_index": [
            ("index_news_sentiment_scope", "数库科技A股情绪指数"),
        ],
        "collect_time": "19:30",
        "max_items": 20,
    }
    
    def collect_daily_sentiment(self, db: Session) -> dict:
        """采集每日舆情数据"""
        results = {
            "news_count": 0,
            "sentiment_index": None,
        }
        
        # 1. 采集财经快讯
        news_data = self._fetch_financial_news()
        results["news_count"] = len(news_data)
        
        # 2. 获取情绪指数
        sentiment_df = ak.index_news_sentiment_scope()
        results["sentiment_index"] = sentiment_df.iloc[-1]  # 最新数据
        
        # 3. 存储到数据库
        for item in news_data:
            self._save_sentiment_data(item, db)
        
        return results
    
    def _fetch_financial_news(self) -> List[dict]:
        """获取财经快讯"""
        try:
            # 东方财富财经快讯
            df = ak.stock_info_global_em()
            news_list = []
            for idx, row in df.head(20).iterrows():
                news_list.append({
                    "source": "eastmoney",
                    "data_type": "news",
                    "title": row.get("标题", ""),
                    "content": row.get("摘要", ""),
                    "publish_time": row.get("发布时间", None),
                })
            return news_list
        except Exception as e:
            logger.error(f"获取财经快讯失败: {e}")
            return []
    
    def analyze_sentiment_with_llm(self, sentiment: SentimentData, 
                                    db: Session) -> dict:
        """使用LLM分析舆情情感"""
        agent = ETFAllocationAgent()
        
        prompt = f"""分析以下财经新闻的情感倾向：

标题: {sentiment.title}
内容: {sentiment.content}

请返回JSON格式：
{
  "sentiment_score": 0.5,  // -1到1，正面为正数
  "sentiment_label": "positive",  // positive/negative/neutral
  "related_etfs": ["510300"],  // 相关ETF代码
  "key_factors": ["政策利好", "经济复苏"]  // 关键因素
}"""
        
        # 复用ETFAllocationAgent的LLM调用模式
        result = agent._call_llm(prompt)
        return result
```

### 5.2 AutoAnalysisService（市场分析）

```python
# app/services/auto_analysis_service.py (新增)
"""
AI市场分析服务
综合舆情数据和净值变化，生成市场研判和策略建议
"""

from datetime import date
from sqlalchemy.orm import Session
from app.models.sentiment import SentimentData
from app.models.etf import ETFQuotation
from app.strategies.generator import ETFAllocationAgent

class AutoAnalysisService:
    """AI市场分析服务"""
    
    ANALYSIS_PROMPT = """你是专业的ETF市场分析师。

今日数据汇总：
1. 舆情数据：{sentiment_summary}
2. 情绪指数：{sentiment_index}
3. ETF净值变化：{nav_changes}
4. 当前配置：{current_allocation}

请综合分析市场状态，返回JSON格式：
{
  "market_sentiment": "bullish/bearish/neutral",
  "sentiment_score": 0.3,
  "confidence_level": "high/medium/low",
  "positive_factors": ["政策利好"],
  "negative_factors": ["估值偏高"],
  "suggested_action": "hold/rebalance",
  "suggested_allocation": {"510300": 0.4, "510500": 0.3},
  "action_reason": "基于舆情和市场数据，建议..."
}"""
    
    def analyze_market(self, strategy_id: int, analysis_date: date, 
                       db: Session) -> dict:
        """综合分析市场状态"""
        
        # 1. 获取当日舆情数据
        sentiment_data = self._get_today_sentiments(analysis_date, db)
        
        # 2. 获取ETF净值变化
        nav_changes = self._get_nav_changes(db)
        
        # 3. 获取当前策略配置
        strategy = db.query(Strategy).get(strategy_id)
        current_allocation = strategy.allocation_config
        
        # 4. 调用LLM分析（复用ETFAllocationAgent模式）
        agent = ETFAllocationAgent()
        prompt = self._build_analysis_prompt(
            sentiment_data, nav_changes, current_allocation
        )
        
        analysis_result = agent._call_llm(prompt)
        
        # 5. 存储分析结果到策略表
        strategy.last_analysis_result = analysis_result
        strategy.last_auto_analysis_date = analysis_date
        db.commit()
        
        return analysis_result
```

### 5.3 AutoStrategyExecutor（策略执行）

```python
# app/services/auto_strategy_executor.py (新增)
"""
自动策略执行器
基于AI分析结果执行策略调整，包含安全检查机制
"""

from sqlalchemy.orm import Session
from app.models.strategy import Strategy
from app.models.auto_strategy_log import AutoStrategyLog
from app.services.portfolio_service import PortfolioService

class AutoStrategyExecutor:
    """自动策略执行器"""
    
    # 安全限制配置
    SAFETY_LIMITS = {
        "max_daily_adjustments": 1,         # 每日最大调整次数
        "max_allocation_change": 0.10,      # 单次最大配置变化10%
        "min_confidence_level": "medium",   # 最低信心等级
        "continuous_same_direction": 3,     # 连续同向调整限制
    }
    
    def execute_auto_strategy(self, strategy_id: int, db: Session) -> dict:
        """执行自动策略"""
        strategy = db.query(Strategy).get(strategy_id)
        
        # 1. 安全检查
        safety_result = self._check_safety_limits(strategy)
        if not safety_result["passed"]:
            self._log_execution(strategy_id, "skipped", safety_result, db)
            return {"status": "skipped", "reason": safety_result["reason"]}
        
        # 2. 获取AI分析结果
        analysis = strategy.last_analysis_result
        
        # 3. 决策是否调整
        if analysis["suggested_action"] == "hold":
            self._log_execution(strategy_id, "hold", analysis, db)
            return {"status": "hold", "reason": "AI建议维持当前配置"}
        
        # 4. 执行调整（复用PortfolioService）
        portfolio_svc = PortfolioService()
        old_allocation = strategy.allocation_config.copy()
        
        # 计算调整幅度
        adjustment = self._calculate_adjustment(
            old_allocation, 
            analysis["suggested_allocation"]
        )
        
        # 应用调整
        strategy.allocation_config = adjustment["new_allocation"]
        strategy.auto_adjustment_count += 1
        db.commit()
        
        # 5. 记录日志
        self._log_execution(
            strategy_id, 
            "adjust", 
            {
                "analysis": analysis,
                "old_allocation": old_allocation,
                "new_allocation": adjustment["new_allocation"],
            },
            db
        )
        
        return {"status": "adjusted", "adjustment": adjustment}
    
    def _check_safety_limits(self, strategy: Strategy) -> dict:
        """安全检查"""
        # 检查每日调整次数
        if strategy.auto_adjustment_count >= strategy.max_daily_adjustments:
            return {"passed": False, "reason": "超过每日最大调整次数"}
        
        # 检查信心等级
        analysis = strategy.last_analysis_result
        if analysis.get("confidence_level") == "low":
            return {"passed": False, "reason": "AI信心等级过低"}
        
        # 检查配置变化幅度
        old = strategy.allocation_config
        new = analysis.get("suggested_allocation", {})
        max_change = self._calculate_max_change(old, new)
        if max_change > self.SAFETY_LIMITS["max_allocation_change"]:
            return {"passed": False, "reason": f"配置变化幅度过大({max_change:.2%})"}
        
        return {"passed": True}
```

---

## 六、定时任务编排

### 6.1 时间编排设计

**编排原则**: 按数据依赖顺序，净值更新→舆情采集→AI分析→策略调整→持仓计算

| 时间 | 任务 | 说明 | 依赖 |
|---|---|---|---|
| **18:00** | `_job_update_net_values` | 更新ETF净值（现有） | 无 |
| **18:05** | `_job_run_strategies` | 执行现有策略（现有） | 净值数据 |
| **19:30** | `_job_collect_sentiments` | 舆情采集+情感分析（新增） | 无 |
| **19:45** | `_job_analyze_market` | AI市场分析（新增） | 舆情+净值 |
| **20:00** | `_job_adjust_auto_strategy` | 自动策略调整（新增） | AI分析结果 |
| **20:05** | `_job_calculate_holdings` | 持仓计算（新增） | 策略调整 |

### 6.2 Scheduler扩展实现

```python
# app/tasks/scheduler.py (扩展)

def _job_collect_sentiments():
    """19:30 - 舆情采集任务"""
    from app.services.sentiment_service import SentimentService
    db = SessionLocal()
    try:
        svc = SentimentService()
        result = svc.collect_daily_sentiment(db)
        
        # 对新采集的舆情进行情感分析
        sentiments = db.query(SentimentData).filter(
            SentimentData.data_date == date.today(),
            SentimentData.sentiment_score == None
        ).all()
        for s in sentiments:
            svc.analyze_sentiment_with_llm(s, db)
        
        logger.info(f"舆情采集完成: {result['news_count']}条")
    finally:
        db.close()

def _job_analyze_market():
    """19:45 - AI市场分析任务"""
    from app.services.auto_analysis_service import AutoAnalysisService
    db = SessionLocal()
    try:
        svc = AutoAnalysisService()
        # 获取所有运行中的自动策略
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()
        for strategy in auto_strategies:
            result = svc.analyze_market(strategy.id, date.today(), db)
            logger.info(f"策略{strategy.id}分析完成: {result['market_sentiment']}")
    finally:
        db.close()

def _job_adjust_auto_strategy():
    """20:00 - 自动策略调整任务"""
    from app.services.auto_strategy_executor import AutoStrategyExecutor
    db = SessionLocal()
    try:
        svc = AutoStrategyExecutor()
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()
        for strategy in auto_strategies:
            result = svc.execute_auto_strategy(strategy.id, db)
            logger.info(f"策略{strategy.id}执行: {result['status']}")
    finally:
        db.close()

# 注册新任务到scheduler
def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
        
        # 现有任务...
        
        # 新增: 19:30 舆情采集
        _scheduler.add_job(
            _job_collect_sentiments,
            trigger=CronTrigger(day_of_week='mon-fri', hour=19, minute=30),
            id="collect_sentiments",
            replace_existing=True,
        )
        
        # 新增: 19:45 AI分析
        _scheduler.add_job(
            _job_analyze_market,
            trigger=CronTrigger(day_of_week='mon-fri', hour=19, minute=45),
            id="analyze_market",
            replace_existing=True,
        )
        
        # 新增: 20:00 自动策略调整
        _scheduler.add_job(
            _job_adjust_auto_strategy,
            trigger=CronTrigger(day_of_week='mon-fri', hour=20, minute=0),
            id="adjust_auto_strategy",
            replace_existing=True,
        )
        
    return _scheduler
```

---

## 七、API层设计

### 7.1 新增路由组

```python
# app/routes/auto_strategy_routes.py (新增)
"""AI全自动策略路由"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.db.database import get_db
from app.schemas.schemas import APIResponse

router = APIRouter(prefix="/api/auto-strategy", tags=["AI全自动策略"])

@router.get("/status", response_model=APIResponse)
def get_auto_strategy_status(strategy_id: int, db: Session = Depends(get_db)):
    """获取自动策略状态"""
    strategy = db.query(Strategy).get(strategy_id)
    return APIResponse(data={
        "strategy_id": strategy_id,
        "status": strategy.auto_strategy_status,
        "last_analysis": strategy.last_auto_analysis_date,
        "adjustment_count": strategy.auto_adjustment_count,
        "current_allocation": strategy.allocation_config,
        "last_analysis_result": strategy.last_analysis_result,
    })

@router.get("/logs", response_model=APIResponse)
def get_execution_logs(strategy_id: int, days: int = 7, 
                       db: Session = Depends(get_db)):
    """获取执行日志"""
    logs = db.query(AutoStrategyLog).filter(
        AutoStrategyLog.strategy_id == strategy_id,
        AutoStrategyLog.log_date >= date.today() - timedelta(days=days)
    ).order_by(AutoStrategyLog.log_date.desc()).all()
    
    return APIResponse(data={
        "logs": [{
            "date": log.log_date,
            "status": log.status,
            "action": log.action_type,
            "sentiment": log.sentiment_summary,
            "analysis": log.analysis_result,
            "decision": log.adjustment_decision,
        } for log in logs]
    })

@router.get("/sentiments", response_model=APIResponse)
def get_sentiment_data(days: int = 1, db: Session = Depends(get_db)):
    """获取舆情数据"""
    sentiments = db.query(SentimentData).filter(
        SentimentData.data_date >= date.today() - timedelta(days=days)
    ).order_by(SentimentData.publish_time.desc()).all()
    
    return APIResponse(data={
        "sentiments": [{
            "id": s.id,
            "title": s.title,
            "content": s.content,
            "source": s.source,
            "sentiment_score": s.sentiment_score,
            "sentiment_label": s.sentiment_label,
            "related_etfs": s.related_etfs,
        } for s in sentiments]
    })

@router.post("/trigger-collect", response_model=APIResponse)
def trigger_sentiment_collect(db: Session = Depends(get_db)):
    """手动触发舆情采集"""
    from app.services.sentiment_service import SentimentService
    svc = SentimentService()
    result = svc.collect_daily_sentiment(db)
    return APIResponse(message="舆情采集触发成功", data=result)

@router.post("/trigger-analyze", response_model=APIResponse)
def trigger_market_analyze(strategy_id: int, db: Session = Depends(get_db)):
    """手动触发市场分析"""
    from app.services.auto_analysis_service import AutoAnalysisService
    svc = AutoAnalysisService()
    result = svc.analyze_market(strategy_id, date.today(), db)
    return APIResponse(message="市场分析触发成功", data=result)

@router.post("/create", response_model=APIResponse)
def create_auto_strategy(req: AutoStrategyCreate, db: Session = Depends(get_db)):
    """创建自动策略"""
    strategy = Strategy(
        name=req.name,
        strategy_source='auto_generated',
        auto_strategy_status='running',
        allocation_config=req.initial_allocation,
        max_daily_adjustments=req.max_daily_adjustments or 1,
        initial_capital=req.initial_capital,
    )
    db.add(strategy)
    db.commit()
    return APIResponse(message="自动策略创建成功", data={"strategy_id": strategy.id})
```

### 7.2 路由注册

```python
# app/__init__.py (扩展)
from app.routes.auto_strategy_routes import router as auto_strategy_router

app.include_router(auto_strategy_router)
```

---

## 八、前端集成设计

### 8.1 新增Tab页

```html
<!-- static/index.html (扩展) -->
<nav class="navbar">
    <h1>ETF量化选择系统</h1>
    <div class="nav-links">
        <a href="#" data-tab="market">行情看板</a>
        <a href="#" data-tab="strategy">策略管理</a>
        <a href="#" data-tab="backtest">策略回测</a>
        <a href="#" data-tab="auto-strategy">AI驱动策略</a>  <!-- 新增 -->
    </div>
</nav>

<!-- AI驱动策略 Tab内容 -->
<div id="tab-auto-strategy" class="tab-content">
    <!-- 舆情监控面板 -->
    <div class="panel">
        <h3>舆情监控</h3>
        <div id="sentiment-list" class="news-list">
            <!-- 动态加载舆情数据 -->
        </div>
        <div id="sentiment-summary" class="stats-mini">
            <!-- 情感分布统计 -->
        </div>
    </div>
    
    <!-- 策略状态面板 -->
    <div class="panel">
        <h3>策略状态</h3>
        <div id="strategy-status">
            <!-- 当前策略配置、执行次数、状态 -->
        </div>
    </div>
    
    <!-- 执行日志面板 -->
    <div class="panel">
        <h3>执行日志</h3>
        <div id="execution-logs">
            <!-- 最近7天执行记录 -->
        </div>
    </div>
    
    <!-- 操作按钮 -->
    <div class="toolbar">
        <button onclick="triggerCollect()">手动采集舆情</button>
        <button onclick="triggerAnalyze()">触发市场分析</button>
        <button onclick="pauseStrategy()">暂停策略</button>
    </div>
</div>
```

### 8.2 前端JS扩展

```javascript
// static/js/app.js (扩展)

// Tab切换
document.querySelectorAll('.nav-links a').forEach(a => {
    a.addEventListener('click', (e) => {
        const tab = a.dataset.tab;
        if (tab === 'auto-strategy') {
            loadAutoStrategyPage();
        }
        // ...
    });
});

async function loadAutoStrategyPage() {
    await Promise.all([
        loadSentiments(),
        loadStrategyStatus(),
        loadExecutionLogs(),
    ]);
}

async function loadSentiments() {
    const data = await api('/api/auto-strategy/sentiments?days=1');
    renderSentimentList(data.sentiments);
    renderSentimentSummary(data.sentiments);
}

function renderSentimentList(sentiments) {
    const container = document.getElementById('sentiment-list');
    container.innerHTML = sentiments.map(s => `
        <div class="news-item ${s.sentiment_label}">
            <span class="time">${formatTime(s.publish_time)}</span>
            <span class="title">${s.title}</span>
            <span class="score">${s.sentiment_score?.toFixed(2) || '-'}</span>
        </div>
    `).join('');
}

function renderSentimentSummary(sentiments) {
    const positive = sentiments.filter(s => s.sentiment_label === 'positive').length;
    const negative = sentiments.filter(s => s.sentiment_label === 'negative').length;
    const neutral = sentiments.length - positive - negative;
    
    document.getElementById('sentiment-summary').innerHTML = `
        <div class="stat">正面: ${positive} (${(positive/sentiments.length*100).toFixed(0)}%)</div>
        <div class="stat">负面: ${negative} (${(negative/sentiments.length*100).toFixed(0)}%)</div>
        <div class="stat">中性: ${neutral}</div>
    `;
}

async function triggerCollect() {
    await api('/api/auto-strategy/trigger-collect', { method: 'POST' });
    toast('舆情采集已触发', 'success');
    loadSentiments();
}

async function triggerAnalyze() {
    const strategyId = getCurrentStrategyId();
    await api(`/api/auto-strategy/trigger-analyze?strategy_id=${strategyId}`, { method: 'POST' });
    toast('市场分析已触发', 'success');
    loadStrategyStatus();
}
```

---

## 九、安全机制设计

### 9.1 多层安全防护

| 层级 | 机制 | 配置 |
|---|---|---|
| **频率限制** | 每日最大调整次数 | 默认1次 |
| **幅度限制** | 单次最大配置变化 | 默认10% |
| **信心限制** | AI信心等级阈值 | confidence="low"时暂停 |
| **熔断机制** | 连续同向调整限制 | 连续3次后暂停 |

### 9.2 熔断触发条件

```python
SAFETY_TRIGGERS = {
    "confidence_low": True,              # AI信心度低时暂停
    "large_adjustment": True,            # 配置变化超过阈值时暂停
    "continuous_same_direction": 3,      # 连续同向调整次数限制
    "sentiment_conflict": True,          # 正负面因素严重冲突时暂停
}
```

### 9.3 安全检查流程

```
AI分析结果 → 安全检查器 → 决策
                    │
                    ├── 通过 → 执行调整
                    │
                    └── 未通过 → 记录日志 + 暂停执行 + 通知用户
```

---

## 十、舆情数据源方案

### 10.1 AKShare接口推荐

| 接口 | 数据源 | 用途 |
|---|---|---|
| `stock_info_global_em()` | 东方财富财经快讯 | 全局市场舆情 |
| `js_news(indicator='最新资讯')` | 金十数据 | 实时快讯 |
| `index_news_sentiment_scope()` | 数库科技 | A股情绪指数 |

### 10.2 舆情采集流程

```
19:30 触发 → AKShare接口 → 数据清洗 → 存入SentimentData表
                                          │
                                          ↓
                                    LLM情感分析 → 填充sentiment_score/label
```

### 10.3 LLM情感分析Prompt模板

```python
SENTIMENT_ANALYSIS_PROMPT = """分析以下财经新闻的情感倾向：

标题: {title}
内容: {content}

请返回JSON格式：
{
  "sentiment_score": 0.5,  // -1到1，正面为正数
  "sentiment_label": "positive",  // positive/negative/neutral
  "related_etfs": ["510300"],  // 相关ETF代码（可选）
  "key_factors": ["政策利好"]  // 关键因素（可选）
}"""
```

---

## 十一、实施计划

### 11.1 工时估算

| 阶段 | 工作内容 | 预估时间 |
|---|---|---|
| **Phase 1** | 数据层（3新表+Strategy扩展） | 2小时 |
| **Phase 2** | 服务层（6新服务） | 5小时 |
| **Phase 3** | 定时任务+API | 2小时 |
| **Phase 4** | 前端Tab集成 | 3小时 |
| **Phase 5** | 记忆机制（复盘+经验管理） | 3小时 |
| **Phase 6** | 测试验证 | 2小时 |
| **总计** | - | **约17小时（2-3天）** |

### 11.2 实施步骤

```
Day 1 上午: 数据层
├── 创建 sentiment.py 模型
├── 创建 auto_strategy_log.py 模型
├── 创建 experience.py 模型（记忆机制）
├── 扩展 Strategy 模型字段
└── 数据库迁移测试

Day 1 下午: 核心服务层
├── 实现 SentimentService
├── 实现 AutoAnalysisService
├── 实现 AutoStrategyExecutor

Day 2 上午: 记忆机制服务层
├── 实现 ReviewService（复盘分析）
├── 实现 ExperienceManager（生命周期）
├── 实现 ExperienceValidator（安全验证）
├── 实现 ExperienceTracker（效果追踪）

Day 2 下午: 定时任务+API
├── 扩展 scheduler.py（新增复盘任务）
├── 创建 auto_strategy_routes.py
└── 注册路由

Day 3 上午: 前端集成
├── 修改 index.html（新增Tab页）
├── 扩展 app.js（Tab逻辑）
├── 新增经验展示模块

Day 3 下午: 测试验证
├── 功能单元测试
├── 复盘流程验证
├── 端到端验证
└── 文档完善
```

---

## 十二、文件清单

### 12.1 新增文件

| 文件路径 | 说明 |
|---|---|---|
| `app/models/sentiment.py` | 舆情数据模型 |
| `app/models/auto_strategy_log.py` | 自动策略日志模型 |
| `app/models/experience.py` | 策略经验模型 |
| `app/models/experience_usage_record.py` | 经验应用记录模型 |
| `app/services/sentiment_service.py` | 舆情采集服务 |
| `app/services/auto_analysis_service.py` | 市场分析服务 |
| `app/services/auto_strategy_executor.py` | 策略执行器 |
| `app/services/review_service.py` | 复盘分析服务 |
| `app/services/experience_manager.py` | 经验生命周期管理 |
| `app/services/experience_validator.py` | 经验验证服务 |
| `app/services/experience_tracker.py` | 经验效果追踪 |
| `app/routes/auto_strategy_routes.py` | API路由 |

### 12.2 修改文件

| 文件路径 | 修改内容 |
|---|---|
| `app/models/strategy.py` | 新增字段 |
| `app/tasks/scheduler.py` | 新增定时任务（复盘） |
| `app/__init__.py` | 注册新路由 |
| `static/index.html` | 新增Tab页 |
| `static/js/app.js` | 新增Tab逻辑 |

---

## 十三、记忆机制设计（复盘优化）

### 13.1 设计目标

| 目标 | 说明 |
|---|---|
| **复盘优化** | 对历史执行结果进行回顾分析 |
| **经验生成** | AI自动总结成功/失败经验 |
| **避免踩坑** | 历史经验指导未来决策 |
| **最小存储** | 轻量级结构，避免存储原始数据 |

### 13.2 Experience数据表

```python
# app/models/experience.py (新增)
class Experience(Base):
    """策略经验表 - 存储AI总结的结构化经验"""
    __tablename__ = "experience"
    
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey('strategy.id'), index=True)
    
    # 经验分类
    experience_type = Column(String(20), nullable=False)  # success/failure/insight
    scenario_tags = Column(JSON)                         # ["高通胀", "政策收紧", "牛市初期"]
    
    # 经验内容
    title = Column(String(100))                          # "高通胀环境下债券ETF配置过高的风险"
    description = Column(Text)                           # 详细描述
    market_condition = Column(JSON)                      # 市场环境描述
    action_taken = Column(JSON)                          # 采取的行动
    result = Column(String(20))                          # positive/negative/neutral
    
    # 有效性
    effectiveness_score = Column(Float, default=0.0)     # 0-10分，验证后更新
    application_count = Column(Integer, default=0)       # 应用次数
    success_rate = Column(Float)                         # 应用成功率
    
    # 来源
    source_log_id = Column(Integer)                      # 来源日志ID
    generated_date = Column(Date)                        # 生成日期
    
    # 生命周期
    is_validated = Column(Boolean, default=False)        # 是否验证
    is_active = Column(Boolean, default=True)            # 是否激活
    expires_date = Column(Date)                          # 过期日期（默认90天）
    weight = Column(Float, default=1.0)                  # 权重（衰减）
    
    created_at = Column(DateTime, default=datetime.utcnow)
```

### 13.3 复盘触发机制

```python
# app/services/review_service.py (新增)
class ReviewService:
    """复盘分析服务"""
    
    REVIEW_TRIGGERS = {
        "periodic": {
            "daily": False,          # 每日复盘（太频繁）
            "weekly": True,          # 每周复盘 ✅
            "monthly": True,         # 每月深度复盘 ✅
        },
        "event_based": {
            "consecutive_failures": 3,  # 连续3次失败触发紧急复盘
            "large_loss": -0.05,        # 单日损失超5%触发紧急复盘
            "safety_triggered": True,   # 安全机制触发后复盘
        }
    }
    
    def trigger_review(self, strategy_id: int, review_type: str, db: Session):
        """触发复盘分析"""
        # 1. 收集分析周期内的数据
        period_data = self._collect_period_data(strategy_id, review_type, db)
        
        # 2. 调用LLM生成经验
        experiences = self._generate_experiences_with_llm(period_data, db)
        
        # 3. 存储经验到数据库
        for exp in experiences:
            self._save_experience(strategy_id, exp, db)
        
        # 4. 清理过期经验
        self._cleanup_expired_experiences(strategy_id, db)
        
        return {"experiences_generated": len(experiences)}
```

### 13.4 经验生成Prompt设计

```python
EXPERIENCE_GENERATION_PROMPT = """你是专业的投资复盘分析师。

请基于以下历史数据，总结可复用的投资经验：

## 历史执行数据
- 执行日期范围: {date_range}
- 总执行次数: {total_count}
- 成功次数: {success_count}
- 失败次数: {failure_count}
- 平均收益率: {avg_return}
- 最大损失: {max_loss}

## 典型案例（最近3次失败）
{failure_cases}

## 典型案例（最近3次成功）
{success_cases}

## 舆情环境特征
{sentiment_patterns}

请生成3-5条结构化经验，每条经验包含：
1. experience_type: success/failure/insight
2. scenario_tags: 适用场景标签列表
3. title: 简明标题（不超过50字）
4. description: 详细描述（100-200字）
5. market_condition: 市场环境关键指标
6. action_taken: 采取/应采取的行动
7. result: positive/negative/neutral
8. key_insight: 核心洞察（一句话）

输出JSON数组格式：
[
  {
    "experience_type": "failure",
    "scenario_tags": ["高通胀", "政策收紧"],
    "title": "高通胀环境下债券ETF配置过高的风险",
    "description": "在高通胀、政策收紧的环境下，债券ETF配置超过30%会导致...",
    ...
  }
]

注意：
1. 经验应具有通用性和可复用性
2. 避免过于具体的细节
3. 总结规律而非单次事件
4. 区分成功经验和失败教训"""
```

### 13.5 经验应用机制

```python
# app/services/auto_analysis_service.py (扩展)
class AutoAnalysisService:
    
    def analyze_market(self, strategy_id: int, analysis_date: date, db: Session):
        """综合分析市场状态（注入经验）"""
        
        # 1. 获取相关历史经验
        relevant_experiences = self._get_relevant_experiences(
            strategy_id, 
            current_market_condition,  # 当前市场环境
            db
        )
        
        # 2. 构建带经验的Prompt
        prompt = self._build_prompt_with_experiences(
            sentiment_data, 
            nav_changes, 
            current_allocation,
            relevant_experiences  # 注入经验
        )
        
        # 3. 调用LLM分析
        analysis_result = self._call_llm(prompt)
        
        # 4. 记录使用的经验
        self._record_experience_usage(relevant_experiences, analysis_result, db)
        
        return analysis_result
    
    def _get_relevant_experiences(self, strategy_id: int, 
                                   market_condition: dict, 
                                   db: Session) -> List[Experience]:
        """获取相关的历史经验"""
        # 按场景标签匹配
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True,
            Experience.is_validated == True,
            Experience.expires_date >= date.today(),
        ).all()
        
        # 计算相关性分数
        scored_experiences = []
        for exp in experiences:
            relevance = self._calculate_relevance(exp, market_condition)
            weighted_score = relevance * exp.weight * (exp.effectiveness_score / 10)
            scored_experiences.append((exp, weighted_score))
        
        # 返回最相关的3-5条
        sorted_experiences = sorted(scored_experiences, key=lambda x: x[1], reverse=True)
        return [exp for exp, score in sorted_experiences[:5]]
    
    def _build_prompt_with_experiences(self, sentiments, nav_changes, 
                                        allocation, experiences) -> str:
        """构建带经验的Prompt"""
        experience_section = ""
        if experiences:
            experience_section = """
## 历史经验参考（请参考以下经验进行决策）

### 成功经验：
{success_experiences}

### 失败教训（请避免）：
{failure_experiences}

### 关键洞察：
{insights}

请参考以上经验，结合当前市场数据做出决策。特别注意避免失败教训中提到的情况。
"""
        
        return self.ANALYSIS_PROMPT.format(
            sentiment_summary=sentiments,
            nav_changes=nav_changes,
            current_allocation=allocation,
            experience_section=experience_section
        )
```

### 13.6 经验生命周期管理

```python
# app/services/experience_manager.py (新增)
class ExperienceManager:
    """经验生命周期管理"""
    
    LIFECYCLE_CONFIG = {
        "expire_days": 90,              # 90天后过期
        "weight_decay_rate": 0.1,       # 每月权重衰减10%
        "min_weight": 0.3,              # 最小权重阈值
        "validation_threshold": 3,      # 应用3次后验证
        "effectiveness_threshold": 6.0, # 评分低于6分标记待审核
    }
    
    def update_experience_lifecycle(self, strategy_id: int, db: Session):
        """更新经验生命周期"""
        experiences = db.query(Experience).filter(
            Experience.strategy_id == strategy_id,
            Experience.is_active == True
        ).all()
        
        for exp in experiences:
            # 1. 权重衰减（每月）
            months_since_creation = (date.today() - exp.generated_date).days / 30
            exp.weight = max(
                self.LIFECYCLE_CONFIG["min_weight"],
                1.0 - months_since_creation * self.LIFECYCLE_CONFIG["weight_decay_rate"]
            )
            
            # 2. 过期检查
            if date.today() > exp.expires_date:
                exp.is_active = False
            
            # 3. 有效性验证
            if exp.application_count >= self.LIFECYCLE_CONFIG["validation_threshold"]:
                self._validate_experience(exp, db)
            
            # 4. 低效经验标记
            if exp.effectiveness_score < self.LIFECYCLE_CONFIG["effectiveness_threshold"]:
                exp.is_validated = False  # 需人工审核
        
        db.commit()
    
    def _validate_experience(self, exp: Experience, db: Session):
        """验证经验有效性"""
        # 获取该经验的应用记录
        usage_records = self._get_experience_usage_records(exp.id, db)
        
        if len(usage_records) >= 3:
            # 计算成功率
            success_count = sum(1 for r in usage_records if r.result == "positive")
            exp.success_rate = success_count / len(usage_records)
            
            # 更新效果评分
            exp.effectiveness_score = min(10, exp.success_rate * 10)
            exp.is_validated = True
```

### 13.7 安全机制

```python
# app/services/experience_validator.py (新增)
class ExperienceValidator:
    """经验验证和安全机制"""
    
    SAFETY_RULES = {
        "max_experiences_per_strategy": 50,   # 每策略最大50条经验
        "auto_approve_threshold": 7.0,        # 评分≥7自动激活
        "manual_review_threshold": 5.0,       # 评分5-7需人工审核
        "auto_reject_threshold": 3.0,         # 评分<3自动拒绝
        "conflict_detection": True,           # 检测矛盾经验
    }
    
    def validate_new_experience(self, experience: dict, db: Session) -> dict:
        """验证新经验"""
        # 1. 内容安全检查
        if self._has_dangerous_advice(experience):
            return {"valid": False, "reason": "包含危险建议"}
        
        # 2. 与现有经验冲突检查
        conflicts = self._check_conflicts(experience, db)
        if conflicts:
            return {"valid": False, "reason": f"与现有经验冲突: {conflicts}"}
        
        # 3. 初始评分
        initial_score = self._calculate_initial_score(experience)
        
        # 4. 决定状态
        if initial_score >= self.SAFETY_RULES["auto_approve_threshold"]:
            return {"valid": True, "status": "active", "score": initial_score}
        elif initial_score >= self.SAFETY_RULES["manual_review_threshold"]:
            return {"valid": True, "status": "pending_review", "score": initial_score}
        else:
            return {"valid": False, "status": "rejected", "score": initial_score}
    
    def _check_conflicts(self, new_exp: dict, db: Session) -> List[str]:
        """检查与现有经验的冲突"""
        existing_exps = db.query(Experience).filter(
            Experience.strategy_id == new_exp["strategy_id"],
            Experience.is_active == True
        ).all()
        
        conflicts = []
        for exp in existing_exps:
            # 检查场景标签重叠但建议相反
            if self._has_overlapping_tags(new_exp, exp) and \
               self._has_opposite_actions(new_exp, exp):
                conflicts.append(exp.title)
        
        return conflicts
```

### 13.8 定时任务扩展

```python
# app/tasks/scheduler.py (扩展)

def _job_weekly_review():
    """每周复盘任务 - 每周日21:00"""
    from app.services.review_service import ReviewService
    db = SessionLocal()
    try:
        svc = ReviewService()
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()
        for strategy in auto_strategies:
            result = svc.trigger_review(strategy.id, 'weekly', db)
            logger.info(f"策略{strategy.id}每周复盘完成: {result}")
    finally:
        db.close()

def _job_monthly_deep_review():
    """每月深度复盘 - 每月最后一天22:00"""
    from app.services.review_service import ReviewService
    db = SessionLocal()
    try:
        svc = ReviewService()
        auto_strategies = db.query(Strategy).filter(
            Strategy.strategy_source == 'auto_generated',
            Strategy.auto_strategy_status == 'running'
        ).all()
        for strategy in auto_strategies:
            result = svc.trigger_review(strategy.id, 'monthly', db)
            logger.info(f"策略{strategy.id}月度深度复盘完成: {result}")
        
        # 更新经验生命周期
        from app.services.experience_manager import ExperienceManager
        exp_mgr = ExperienceManager()
        for strategy in auto_strategies:
            exp_mgr.update_experience_lifecycle(strategy.id, db)
    finally:
        db.close()

# 注册复盘任务
_scheduler.add_job(
    _job_weekly_review,
    trigger=CronTrigger(day_of_week='sun', hour=21, minute=0),
    id="weekly_review",
)
_scheduler.add_job(
    _job_monthly_deep_review,
    trigger=CronTrigger(day='last', hour=22, minute=0),
    id="monthly_deep_review",
)
```

### 13.9 记忆机制架构图

```
┌─────────────────────────────────────────────────────────────┐
│                     复盘触发层                                │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────────────┐    │
│  │ 每周日21:00 │ │ 每月最后一天│ │ 事件触发(连续失败)   │    │
│  │   每周复盘  │ │ 22:00深度   │ │    紧急复盘         │    │
│  └─────────────┘ └─────────────┘ └─────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                     复盘分析层                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  ReviewService                                           ││
│  │  1. 收集历史数据 (AutoStrategyLog + PortfolioSnapshot)   ││
│  │  2. LLM经验生成 (EXPERIENCE_GENERATION_PROMPT)          ││
│  │  3. 经验验证 (ExperienceValidator)                       ││
│  │  4. 存储到Experience表                                   ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                     经验存储层                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  Experience表                                            ││
│  │  - experience_type: success/failure/insight             ││
│  │  - scenario_tags: 适用场景                               ││
│  │  - effectiveness_score: 效果评分                         ││
│  │  - weight: 权重(衰减)                                    ││
│  │  - expires_date: 过期日期                                ││
│  └─────────────────────────────────────────────────────────┐│
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                     经验应用层                                │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  AutoAnalysisService (决策时注入)                        ││
│  │  1. 匹配相关经验 (场景标签 + 权重)                        ││
│  │  2. 注入到Prompt (成功经验 + 失败教训)                    ││
│  │  3. 记录使用情况 (应用次数 + 结果)                        ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                   生命周期管理层                              │
│  ┌─────────────────────────────────────────────────────────┐│
│  │  ExperienceManager                                       ││
│  │  - 权重衰减: 每月10%                                      ││
│  │  - 过期清理: 90天                                         ││
│  │  - 有效性验证: 应用3次后                                  ││
│  │  - 低效标记: 评分<6需审核                                 ││
│  └─────────────────────────────────────────────────────────┘│
└─────────────────────────────────────────────────────────────┘
```

### 13.10 经验应用效果追踪

```python
# app/services/experience_tracker.py (新增)
class ExperienceTracker:
    """经验应用效果追踪"""
    
    def record_experience_usage(self, experience_id: int, 
                                 decision_result: dict, 
                                 db: Session):
        """记录经验使用情况"""
        exp = db.query(Experience).get(experience_id)
        
        # 更新应用次数
        exp.application_count += 1
        
        # 记录应用结果
        usage_record = ExperienceUsageRecord(
            experience_id=experience_id,
            usage_date=date.today(),
            market_condition=decision_result["market_condition"],
            decision_made=decision_result["decision"],
            result=decision_result["result"],  # positive/negative/neutral
            return_pct=decision_result["return_pct"],
        )
        db.add(usage_record)
        db.commit()
    
    def calculate_experience_effectiveness(self, experience_id: int, 
                                            db: Session) -> float:
        """计算经验有效性分数"""
        records = db.query(ExperienceUsageRecord).filter(
            ExperienceUsageRecord.experience_id == experience_id
        ).all()
        
        if len(records) < 3:
            return 0.0  # 数据不足
        
        # 加权计算
        positive_weight = sum(r.return_pct for r in records if r.result == "positive")
        negative_weight = sum(abs(r.return_pct) for r in records if r.result == "negative")
        
        effectiveness = (positive_weight - negative_weight) / len(records) * 10
        return max(0, min(10, effectiveness))
```

---

## 十四、附录

### A. 数据表SQL定义

```sql
-- 舆情数据表
CREATE TABLE sentiment_data (
    id INTEGER PRIMARY KEY,
    data_date DATE,
    source VARCHAR(20),
    data_type VARCHAR(20),
    title VARCHAR(200),
    content TEXT,
    publish_time DATETIME,
    sentiment_score FLOAT,
    sentiment_label VARCHAR(10),
    related_etfs JSON,
    key_factors JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_sentiment_date ON sentiment_data(data_date);

-- 自动策略日志表
CREATE TABLE auto_strategy_log (
    id INTEGER PRIMARY KEY,
    strategy_id INTEGER,
    log_date DATE,
    status VARCHAR(20),
    action_type VARCHAR(20),
    sentiment_summary JSON,
    analysis_result JSON,
    adjustment_decision JSON,
    safety_check_passed BOOLEAN,
    safety_reason VARCHAR(200),
    old_allocation JSON,
    new_allocation JSON,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_auto_log_date ON auto_strategy_log(log_date);
CREATE INDEX idx_auto_log_strategy ON auto_strategy_log(strategy_id);

-- Strategy表扩展字段
ALTER TABLE strategy ADD COLUMN strategy_source VARCHAR(20) DEFAULT 'manual';
ALTER TABLE strategy ADD COLUMN auto_strategy_status VARCHAR(20);
ALTER TABLE strategy ADD COLUMN last_auto_analysis_date DATE;
ALTER TABLE strategy ADD COLUMN auto_adjustment_count INTEGER DEFAULT 0;
ALTER TABLE strategy ADD COLUMN max_daily_adjustments INTEGER DEFAULT 1;
ALTER TABLE strategy ADD COLUMN last_analysis_result JSON;
```

### B. API接口清单

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/auto-strategy/status` | GET | 获取策略状态 |
| `/api/auto-strategy/logs` | GET | 获取执行日志 |
| `/api/auto-strategy/sentiments` | GET | 获取舆情数据 |
| `/api/auto-strategy/trigger-collect` | POST | 手动触发舆情采集 |
| `/api/auto-strategy/trigger-analyze` | POST | 手动触发市场分析 |
| `/api/auto-strategy/create` | POST | 创建自动策略 |

---

**设计文档完成** 📋