# AGENTS.md — ETF量化选择系统

## 项目概述

ETF配置组合系统：智能配置、自动再平衡、回测验证、AI多Agent驱动策略调整。

- **后端**: FastAPI + SQLAlchemy + SQLite
- **前端**: 原生 HTML/CSS/JS（`static/` 目录）
- **数据源**: efinance（东方财富行情）
- **LLM**: OpenAI 兼容 API（阿里云 DashScope）
- **定时任务**: APScheduler（工作日串行管道）
- **Python**: >= 3.10

## 架构总览

```
main.py                  # uvicorn 入口
app/
├── __init__.py          # FastAPI app 初始化、路由注册、lifespan
├── config.py            # pydantic-settings 配置（.env）
├── db/database.py       # SQLAlchemy engine/session/init_db
├── models/              # ORM 模型（etf, strategy, portfolio, sentiment, experience, auto_strategy_log, system_config）
├── schemas/schemas.py   # Pydantic 请求/响应 schema
├── routes/              # API 路由（etf, strategy, backtest, net_value, auto_strategy, portfolio, config）
├── services/            # 业务逻辑层
│   ├── data_service.py / data_sources.py   # ETF行情获取（efinance）
│   ├── strategy_service.py                 # 策略CRUD + AI生成
│   ├── portfolio_service.py                # 实盘模拟（再平衡执行）
│   ├── backtest_service.py                 # 回测引擎
│   ├── auto_strategy_executor.py           # AI自驱动全管道
│   ├── risk_controller.py                  # 风控（熔断/回撤/压力测试）
│   ├── sentiment_service.py                # 舆情采集+LLM情感分析
│   ├── experience_manager.py               # 经验生命周期管理
│   ├── smart_experience_matcher.py         # 经验智能匹配
│   ├── review_service.py                   # 周度复盘
│   ├── net_value_service.py                # 净值更新
│   ├── config_service.py                   # 运行时LLM配置
│   └── technical_indicator_service.py      # 技术指标计算
├── agents/              # 多Agent辩论系统
│   ├── base.py                             # BaseAgent（LLM调用+JSON解析）
│   ├── orchestrator.py                     # 分析编排器（3阶段辩论）
│   ├── technical_analyst.py                # 技术分析师
│   ├── sentiment_analyst.py                # 情绪分析师
│   ├── bull_researcher.py / bear_researcher.py  # 多空研究员
│   ├── market_analyst.py                   # 研究主管（最终裁决）
│   └── risk_agents/                        # 三方风控辩论
│       ├── risk_debate_orchestrator.py     # 风控辩论编排
│       ├── aggressive_risk.py / conservative_risk.py / neutral_risk.py
│       └── risk_manager.py                 # 风控主管裁决
├── strategies/          # 配置组合策略引擎
│   ├── base.py                             # AllocationStrategy 基类 + compute_adjustment
│   ├── portfolio_rebalance.py              # 再平衡策略实现
│   └── generator.py                        # AI配置生成（LLM）
├── memory/memory_log.py # 决策记忆日志（Markdown文件）
└── tasks/scheduler.py   # 定时管道（净值→再平衡→舆情→AI分析）
static/                  # 前端页面
```

## 核心业务流程

### 每日自驱动管道（工作日 20:00）

```
净值更新 → 组合再平衡 → 舆情采集 → AI分析管道
                                        ├─ 风险检查（三方风控辩论）
                                        ├─ 多Agent市场分析（技术+情绪→多空辩论→主管裁决）
                                        ├─ ETF代码验证
                                        ├─ 配置变化检查（≤10%）
                                        └─ 交易执行 + 记忆写入
```

### 多Agent辩论分析（Orchestrator）

```
阶段1 数据消化: TechnicalAnalyst + SentimentAnalyst（并行）
阶段2 多空辩论: BullResearcher vs BearResearcher
阶段3 主管裁决: MarketAnalyst → 输出 market_regime / suggested_action / suggested_allocation
```

### 三方风控辩论（RiskDebateOrchestrator）

```
熔断/回撤临界 → 直接拦截（跳过辩论）
否则 → Aggressive vs Conservative vs Neutral → RiskManager 裁决
```

## 关键设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| 单例服务 | `get_xxx_service()` | 模块级 `_service` 变量 + 工厂函数 |
| 策略模式 | `strategies/base.py` | `AllocationStrategy` 抽象基类 |
| 管道模式 | `auto_strategy_executor.py` | 7阶段串行管道，任一阶段失败即终止 |
| 辩论模式 | `agents/orchestrator.py` | 多角色LLM Agent对抗+裁决 |
| 经验生命周期 | `experience_manager.py` | 权重衰减、过期清理、有效性验证 |

## 数据模型核心关系

```
Strategy (1) ──→ (N) PortfolioSnapshot   # 每日资产快照
Strategy (1) ──→ (N) TradeRecord         # 交易记录
Strategy (1) ──→ (N) Holding             # 当前持仓
Strategy (1) ──→ (N) AutoStrategyLog     # 自动策略执行日志
Strategy (1) ──→ (N) Experience          # 经验库
ETFBasic     (1) ──→ (N) ETFQuotation    # 日K线行情
```

---

## 编程约束

### 1. 先思考再动手

- 明确陈述假设。不确定就问。
- 存在多种理解时，列出选项，不要默默选一个。
- 存在更简单方案时，主动提出。
- 不清楚就停下来，说明困惑点。

### 2. 简洁优先

- 不写超出需求的功能。
- 单次使用的代码不做抽象。
- 不加未被要求的"灵活性"或"可配置性"。
- 200行能用50行解决的，重写。

### 3. 精准修改

- 不"顺手改进"相邻代码、注释、格式。
- 不重构没坏的东西。
- 匹配现有代码风格。
- 发现无关死代码，提一嘴但不删。
- 只清理自己引入的无用 import/变量/函数。

**检验标准**: 每一行变更都能直接追溯到用户需求。

### 4. 目标驱动

将任务转化为可验证目标：
- "加验证" → 写非法输入测试，然后让它通过
- "修bug" → 写复现测试，然后修复
- "重构X" → 确保重构前后测试通过

多步任务先列计划：
```
1. [步骤] → 验证: [检查方式]
2. [步骤] → 验证: [检查方式]
```

### 5. 项目特定约束

#### 数据库
- ORM 模型继承 `app.db.database.Base`，新表必须在 `init_db()` 中导入。
- 字段迁移使用 `init_db()` 内的 `ALTER TABLE` 兼容逻辑，不引入 Alembic。
- 所有 DB 操作通过 `Session` 参数传入，不在 service 内部创建 session（定时任务除外）。
- 事务保护：涉及多表写入时用 try/except + `db.rollback()`。

#### 服务层
- 新增 service 遵循单例模式：模块底部 `_service` + `get_xxx_service()`。
- Service 不直接 import 其他 service 的实例，通过工厂函数获取。
- 耗时操作（LLM调用、网络请求）必须有 try/except 和日志。

#### Agent 系统
- 所有 Agent 继承 `BaseAgent`，通过 `call_llm()` 调用 LLM。
- Agent 的 `analyze()` 方法返回 `Dict`，失败时返回 `{"error": "..."}` 而非抛异常。
- 新增 Agent 需在对应 Orchestrator 中注册。
- LLM 响应解析使用 `_parse_json()`（正则提取 JSON），不假设 LLM 返回格式完美。

#### 策略引擎
- 配置比例 `allocation_config` 总和必须为 1.0（容差 0.01）。
- 买卖以 100 股整数倍取整（`compute_adjustment`）。
- 回测与实盘共用 `compute_adjustment` 和 `PortfolioContext`，修改时两边都要验证。
- 再平衡频率: daily / weekly / monthly / quarterly / yearly / none。

#### 风控
- 熔断/回撤临界时直接拦截，不进入辩论流程。
- 单日最大调整次数由 `strategy.max_daily_adjustments` 控制。
- 单次配置变化上限 10%（`SAFETY_LIMITS["max_allocation_change"]`）。

#### 定时任务
- 所有步骤在单个 job 内串行执行，不并行。
- 每个步骤独立创建/关闭 `SessionLocal()`，不共享 session。
- 新增定时步骤加在 `_job_daily_pipeline()` 对应阶段内。

#### API 路由
- 路由文件在 `app/routes/` 下，使用 `APIRouter`。
- 新路由必须在 `app/__init__.py` 中 `include_router`。
- 响应统一使用 `APIResponse(code, message, data)` 包装。
- 依赖注入使用 `Depends(get_db)`。

#### 前端
- 纯静态文件在 `static/` 下，无构建工具。
- JS 使用原生 ES6+，不引入框架。
- API 调用使用 fetch，基础路径为相对路径。

#### A股配色约定
- 红色 = 正面：多头、涨、正面情绪、盈利、买入信号。
- 绿色 = 负面：空头、跌、负面情绪、亏损、卖出信号。
- 与国际市场（红跌绿涨）相反，遵循A股惯例。
- 前端 CSS 变量、图表配色、报告输出均须遵守此约定。

#### 通用
- 日志使用 `logging.getLogger(__name__)`，不用 print（数据库迁移除外）。
- 中文注释和日志信息。
- 类型注解：函数签名必须有参数和返回值类型。
- 不引入新的重量级依赖（如 Celery、Redis），除非明确要求。
- 配置项通过 `app/config.py` 的 `Settings` 管理，敏感信息走 `.env`。

---

## 运行方式

```bash
# 安装依赖
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env  # 编辑 LLM_API_KEY

# 启动开发服务器
python main.py
# 或
uvicorn app:app --reload --port 8000

# 运行测试
pytest
```

## 目录约定

- 不创建文档文件（除非明确要求）。
- 备份文件（`.bak`, `.backup`）已存在多个，不再新增。
- `.claude/worktrees/` 为历史工作树，不修改。
- `app/memory_logs/` 为运行时生成的决策日志，不提交。