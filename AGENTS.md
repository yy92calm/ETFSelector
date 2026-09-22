# AGENTS.md — ETF量化选择系统

## 项目概述

ETF配置组合系统：全市场动量轮动、AI 自主决策 Agent、自动再平衡、回测验证、规则学习与复盘进化。

- **后端**: FastAPI + SQLAlchemy + SQLite
- **前端**: 原生 HTML/CSS/JS（`static/` 目录，无构建工具）
- **数据源**: Ashare（新浪+腾讯双核）为主，efinance 备用（`app/services/Ashare.py` / `data_sources.py`）
- **LLM**: OpenAI 兼容 API，支持多供应商路由（`llm_model_aliases` 前缀匹配，见 `agent_core/provider.py`）
- **MCP**: 外部工具服务器接入（`mcp_servers` 配置，stdio/http）
- **定时任务**: APScheduler（工作日串行管道 + 复盘 + 行情补全 + 舆情）
- **Python**: >= 3.10

## 架构总览

```
main.py                  # uvicorn 入口
app/
├── __init__.py          # FastAPI app 初始化、14个路由注册、鉴权中间件、lifespan
├── config.py            # pydantic-settings 配置（.env）
├── db/database.py       # SQLAlchemy engine/session/init_db（含 ALTER TABLE 兼容迁移）
├── models/              # ORM 模型（见"数据模型"节，14 文件 22 表）
├── schemas/schemas.py   # Pydantic 请求/响应 schema
├── routes/              # API 路由
│   ├── etf_routes / strategy_routes / backtest_routes / net_value_routes
│   ├── auto_strategy_routes / portfolio_routes / config_routes
│   ├── chat_routes      # SSE 流式对话 + 写操作审批 + 中断/换模型
│   ├── workbench_routes # 工作台聚合（总览/活动流/量化摘要/月收益目标）
│   ├── task_routes      # 任务状态/历史、手动触发管道/单阶段、检查点管理
│   ├── factor_routes    # 因子 IC、自适应权重、因子画像、失败模式
│   ├── rules_routes     # 规则训练与查询
│   ├── research_routes  # 行业性价比研究
│   └── auth_routes      # 登录/token 校验
├── agent_core/          # ★ 通用 Agent 运行时（决策中枢）
│   ├── loop.py                     # AgentLoop：ReAct 多轮 Tool Calling（MAX_TOOL_ROUNDS=10）
│   ├── context.py                  # ContextBuilder：每轮注入系统状态快照
│   ├── compaction.py               # 上下文压缩（LLM 摘要旧消息，失败降级 trim）
│   ├── permissions.py              # PermissionEngine：read/write × discuss/interactive/auto
│   ├── approvals.py                # 写操作审批桥（SSE 线程 ↔ /approve 请求线程）
│   ├── provider.py                 # 模型别名 → base_url/key 路由
│   ├── skill_manager.py            # 技能加载（skills/*.md，mtime 热更新）
│   ├── mcp_bridge.py               # MCP 客户端桥（工具改名 mcp__<server>__<tool>）
│   └── memory.py                   # ChatSession/ChatMessage 持久化（保留最近20轮）
├── tools/               # ★ LLM 工具层（约 40 个内置工具）
│   ├── registry.py                 # @tool 装饰器 + 全局注册表 + Schema 自动生成
│   ├── market_tools / strategy_tools / portfolio_tools
│   └── risk_tools / analysis_tools / ops_tools
├── services/            # 业务逻辑层（见下）
├── agents/              # 多 Agent 辩论系统
│   ├── base.py                     # BaseAgent（LLM调用+JSON解析）
│   ├── orchestrator.py             # 市场分析编排（技术+情绪→多空辩论→主管裁决）
│   ├── technical_analyst / sentiment_analyst / bull_researcher / bear_researcher / market_analyst
│   ├── macro_cycle / cross_asset / volatility_regime / theme_discovery /
│   │   drawdown_attribution / rebalance_timing_agent   # 扩展分析师（接入 Orchestrator）
│   ├── risk_agents/                # 三方风控辩论（激进/保守/中性 → 风控主管）
│   └── rotation_debate/            # 轮动辩论（动量派 vs 稳定派 → 轮动裁决官）
├── strategies/          # 配置组合策略引擎
│   ├── base.py                     # AllocationStrategy 基类 + compute_adjustment
│   ├── portfolio_rebalance.py      # 再平衡策略实现
│   └── generator.py                # AI配置生成（LLM）
├── memory/memory_log.py # 决策记忆日志（Markdown文件）
├── tasks/
│   ├── scheduler.py                # 每日十阶段管道 + 复盘 + 行情补全 + 舆情 job
│   ├── task_logger.py              # @log_task_execution 装饰器（TaskExecutionLog 记账）
│   └── prompts/                    # 外置提示词（autonomous_instruction.md、fetch_plan_prompt.md）
└── data/                # ETF列表本地缓存等数据文件
static/                  # 前端页面（workbench.html 为默认首页）
skills/                  # Agent 技能文件（*.md，带 frontmatter）
plans/                   # 迭代设计文档（23 个，历史决策依据）
```

## 核心业务流程

### 每日自驱动管道（工作日 20:00，可配）

10 个阶段串行，每阶段独立检查点（`pipeline_checkpoint_service`，支持断点续跑）+ `TaskExecutionLog` 记账；任一阶段失败记录失败经验但不中断整体：

```
阶段1: net_value 净值 → quotes 当日行情 → rebalance 再平衡
阶段2: sentiment 舆情 → policy_flow 政策评估+资金流向
阶段3: market_scan 全市场扫描+因子回填 → market_regime 状态刻画
       → fundamental 基本面性价比 → rotation_review 轮动辩论换仓
阶段4: autonomous — AgentLoop.run_autonomous() LLM 自主决策
       （LLM 失败/未配置 → 降级旧 AutoStrategyExecutor 7阶段管道）
收尾: 规则缓存失效 + mark_completed
```

其他定时 job：周三+周日 21:00 复盘（含提示词自进化）、工作日 18:30±30min jitter 的 LLM 规划行情补全、交易日 10/12/14 点舆情采集。

### AgentLoop 双模式（决策中枢）

```
对话模式 run()              ← /api/chat/stream（SSE），写操作经 PermissionEngine 审批
自主模式 run_autonomous()   ← 每日管道阶段4，无人值守
两者共用：ContextBuilder 状态快照 + tools/ 注册表 + compaction + skills + MCP
```

旧多 Agent 辩论（Orchestrator / RiskDebateOrchestrator）现作为工具 `run_multi_agent_analysis` 和 fallback 管道存在。

### 全市场动量轮动（核心量化引擎）

```
market_scanner_service: 全量ETF 5维指标（动量/趋势/量能/波动/资金流）打分排名
  ↓ 纯量化门槛：持仓分 vs 候选分差距 ≥ 5 才进入 LLM 辩论（省 token）
  ↓ failure_mode_service 过滤反复失败的 banned codes
rotation_debate: MomentumAdvocate vs StabilityAdvocate → RotationJudge
  硬约束：持仓≤5、有进必出、每次最多换2只、两派分歧倾向不换
  ↓
rotation_service.execute_rotation 落地换仓；LLM 不可用时降级纯量化
factor_performance_service: 因子 IC 跟踪 → |IC| 归一化自适应打分权重
```

### 规则学习与回放

```
rule_trainer: 从 auto_strategy_log 提取 regime→allocation 映射
replay_service: 历史交易日重跑"当日指标+LLM裁决"，补足熊市样本
rule_engine: 确定性规则回测引擎（Phase1 硬编码 + Phase2 AI 历史规则）
```

## 关键设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| 单例服务 | `get_xxx_service()` | 模块级 `_service` 变量 + 工厂函数 |
| 工具注册 | `tools/registry.py` | `@tool(name, description, risk)` 从类型注解+docstring 生成 Function Schema；`db: Session` 自动注入；未声明 risk 时按 `_WRITE_TOOLS` 名单归类 |
| 权限审批 | `agent_core/permissions.py` + `approvals.py` | discuss 只读 / interactive（默认，写需审批）/ auto 全放行；once/always/deny，超时自动 deny |
| 策略模式 | `strategies/base.py` | `AllocationStrategy` 抽象基类 |
| 管道+检查点 | `tasks/scheduler.py` | 阶段化串行 + 断点续跑 + 失败写经验库 |
| 辩论模式 | `agents/` | 多角色 LLM 对抗 + 主管裁决（市场分析/风控/轮动三套） |
| 经验生命周期 | `experience_manager.py` | 权重衰减、过期清理、有效性验证、失败签名合并计数 |
| 提示词外置 | `tasks/prompts/` | `_load_prompt()` 模板加载，配合 StrategyEvolvedPrompt 自进化 |

## 数据模型核心关系

```
Strategy (1) ──→ (N) PortfolioSnapshot / TradeRecord / Holding / AutoStrategyLog
Strategy (1) ──→ (N) Experience            # 经验库（含 failure_signature 失败模式）
Strategy (1) ──→ (N) StrategyEvolvedPrompt # 提示词自进化版本
ETFBasic     (1) ──→ (N) ETFQuotation      # 日K线行情
ETFBasic     (1) ──→ (N) ETFDailyIndicator # 5维因子+综合分+排名（每日扫描）
ChatSession  (1) ──→ (N) ChatMessage / AIActionLog
独立快照表: MarketRegimeSnapshot / RuleSnapshot / FactorPerformance
            StockFundamental / IndustryScore / PipelineCheckpoint / TaskExecutionLog
```

## 主要服务清单（services/）

| 组 | 服务 |
|----|------|
| 数据 | `data_service` `data_sources` `Ashare` `fundamental_data_service` |
| 策略/组合 | `strategy_service` `portfolio_service` `backtest_service` `net_value_service` |
| AI 管道 | `auto_strategy_executor`（fallback）`auto_analysis_service` `review_service` `pipeline_checkpoint_service` |
| 量化 | `market_scanner_service` `rotation_service` `factor_performance_service` `market_regime_service` `market_environment_service` `technical_indicator_service` |
| 规则 | `rule_engine` `rule_trainer` `replay_service` `value_model_service` |
| 风控/经验 | `risk_controller` `failure_mode_service` `experience_manager` `smart_experience_matcher` |
| 舆情 | `sentiment_service` `policy_impact_service` `capital_flow_service` |
| 其他 | `config_service`（运行时 LLM 配置）`etf_selector_service` |

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

#### Agent / 工具层
- 新增 LLM 工具：在 `app/tools/` 对应模块用 `@tool` 装饰器注册，函数签名必须有类型注解 + docstring（Schema 由此自动生成）；写操作必须归入 `registry._WRITE_TOOLS` 或显式声明 `risk="write"`。
- 工具函数通过 `db: Session` 参数接收 session（执行时自动注入），不自行创建。
- 所有 Agent 继承 `BaseAgent`，通过 `call_llm()` 调用 LLM；`analyze()` 返回 `Dict`，失败时返回 `{"error": "..."}` 而非抛异常。
- LLM 响应解析使用 `_parse_json()`（正则提取 JSON），不假设 LLM 返回格式完美。
- 驱动 LLM 的长提示词外置到文件（`tasks/prompts/` 或 agent 内常量），不硬编码在逻辑中间。
- 技能文件放 `skills/*.md`（frontmatter 控制 model/user invocable），MCP 工具经 `mcp_bridge` 自动注册，不手写重复工具。

#### 策略引擎
- 配置比例 `allocation_config` 总和必须为 1.0（容差 0.01）。
- 买卖以 100 股整数倍取整（`compute_adjustment`）。
- 回测与实盘共用 `compute_adjustment` 和 `PortfolioContext`，修改时两边都要验证。
- 再平衡频率: daily / weekly / monthly / quarterly / yearly / none。
- 轮动硬约束：持仓≤5只、有进必出、每次最多换2只、单只≤40%。

#### 风控
- 熔断/回撤临界时直接拦截，不进入辩论流程。
- 单日最大调整次数由 `strategy.max_daily_adjustments` 控制。
- 单次配置变化上限 10%（`SAFETY_LIMITS["max_allocation_change"]`）。

#### 定时任务
- 所有步骤在单个 job 内串行执行，不并行。
- 每个步骤独立创建/关闭 `SessionLocal()`，不共享 session。
- 新增管道阶段：加入 `_stages` 列表 + `_run_stage()` 调用，自动获得检查点续跑和 TaskExecutionLog 记账。
- 数据源拉取遵守容错配置（重试、熔断、`scheduled_task_allow_fallback` 控制是否降级 efinance）。

#### API 路由
- 路由文件在 `app/routes/` 下，使用 `APIRouter`。
- 新路由必须在 `app/__init__.py` 中 `include_router`。
- 响应统一使用 `APIResponse(code, message, data)` 包装。
- 依赖注入使用 `Depends(get_db)`。
- `/api/*`（除 `/api/auth/`）全部经过 Bearer token 鉴权中间件，新增路由无需单独处理鉴权。

#### 前端
- 纯静态文件在 `static/` 下，无构建工具。
- JS 使用原生 ES6+，不引入框架。
- API 调用使用 fetch，基础路径为相对路径，token 由 `js/auth.js` 统一注入。
- 默认首页是 `workbench.html`（工作台单页，多视图 Tab + AI 对话侧边栏）；`index.html` 为旧版主界面。

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
- 大型迭代先写 `plans/` 设计文档再动手（历史惯例，23 篇）。

---

## 运行方式

```bash
# 安装依赖
pip install -e ".[dev]"

# 配置环境变量
cp .env.example .env  # 编辑 LLM_API_KEY、鉴权 token、MCP servers

# 启动开发服务器
python main.py
# 或
uvicorn app:app --reload --port 8000

# 运行测试
pytest

# 历史回放（一次性 CLI）
python -m app.services.replay_service
```

## 目录约定

- 不创建文档文件（除非明确要求）；`plans/` 为已批准的设计文档区。
- 备份文件（`.bak`, `.backup`）已存在多个，不再新增。
- `.claude/worktrees/` 为历史工作树，不修改。
- `app/memory_logs/` 为运行时生成的决策日志，不提交。
- `server.log`、`etf_selector.db` 为运行时产物，不作为代码理解依据。

## 协作约定

- **提交节奏**：改完可直接 commit + push（用户已确认）。
- **部署节奏**：上线到服务器（停服/pull/重启）**必须先问用户**，得到确认后再动。

### 服务器部署要点（历史踩坑）

- 线上路径 `/home/ubuntu/workspace/ETFSelector`，SSH `ssh -i ~/.ssh/yang.pem ubuntu@43.133.82.137`（无 systemd/tmux，进程为 `venv/bin/python main.py`，端口 12958）。
- 启动方式：`cd ~/workspace/ETFSelector && nohup setsid venv/bin/python main.py >> server.log 2>&1 < /dev/null &`
- 线上库 `etf_selector.db` 已取消 git 跟踪（`.gitignore` 忽略 `*.db`），git 不会再动它；若又遇到仍被跟踪的情况，先 `git rm --cached etf_selector.db` 再 pull，**切勿让 git 删掉线上库**。
- 部署顺序：停服 → pull → 核验 DB（大小/md5/integrity_check）→ 启服 → 健康检查 + 新接口验证。
- `.env` 的 `DEBUG=True` 开启 uvicorn reload，而 `server.log` 在监听目录内会自我触发 `change detected`（历史累计数百万条、日志 368MB）；生产建议 `DEBUG=False` 或日志重定向到项目目录外。
