# ETF量化选择系统

ETF配置组合系统 - 智能配置、自动再平衡、回测验证、AI多Agent驱动策略调整、全市场动量轮动

## 项目简介

基于配置比例的ETF组合管理系统，支持AI智能生成策略、自动再平衡、历史回测验证，并内置全市场动量轮动引擎（量化筛选 + 多Agent辩论裁决）。

## 核心功能

### 🎯 策略配置
- **配置组合管理**：自定义ETF配置比例
- **再平衡策略**：支持日/周/月/季/年频率再平衡
- **偏离阈值触发**：当配置偏离超过阈值自动调整
- **AI策略生成**：对话式AI辅助生成配置策略（支持通义千问、Kimi等）
- **实际收益跟踪**：记录持仓起始日，展示自建仓起的实际收益曲线、累计收益、最大回撤

### 🔄 全市场动量轮动（核心引擎）
- **全市场量化扫描**：每个交易日对全量ETF计算5维量化指标（动量/趋势/量能/波动/资金流）
- **轮动决策**：持仓≤5只、有进必出、每次最多换2只、最短持有5天、单只≤40%
- **多Agent轮动辩论**：动量派 vs 稳健派 辩论 → 轮动主管裁决；LLM不可用时降级纯量化
- **IC因子有效性**：跟踪各因子与未来收益的秩相关（IC），基于|IC|归一化自适应调整打分权重
- **失败模式规避**：重复失败的操作沉淀为失败模式库，连续失败≥3次的ETF自动进入规避名单

### 📊 数据服务
- **全市场ETF数据**：覆盖沪深交易所主要ETF
- **净值数据获取**：Ashare（新浪+腾讯双核）为主，efinance 备用
- **历史行情存储**：SQLite本地数据库存储
- **定时任务调度**：每日自驱动串行管道（净值→再平衡→舆情→AI分析），支持检查点断点续跑

### 🔬 回测验证
- **完整回测引擎**：模拟历史持仓与再平衡过程
- **多维度指标**：收益率、最大回撤、夏普比率、胜率
- **可视化展示**：净值曲线、月度收益、时间段收益分析
- **交易记录追踪**：完整记录每次再平衡操作

### 🤖 AI全自动驱动策略
- **多Agent辩论式决策**：技术分析师 + 情绪分析师 → 多空研究员辩论 → 研究主管裁决
- **三方风控辩论**：激进/中性/保守 三个风控Agent辩论，风控主管裁决
- **舆情自动采集**：财经快讯 + LLM情感分析（交易日10:00/12:00/14:00）
- **7阶段全管道执行**：验证→安全限制→风控→AI分析→ETF验证→配置变化检查→交易执行（事务保护）
- **记忆机制**：每周复盘生成结构化经验，经验生命周期管理（90天过期/权重衰减/有效性验证），历史经验注入新决策
- **安全熔断**：频率限制、幅度限制（单次≤10%）、信心阈值、回撤保护

### 💻 前端交互
- **量化工作台**：原生HTML/JavaScript单页应用（行情/任务/舆情/策略多Tab）
- **对话式AI**：多轮对话优化策略配置 + 28个工具（策略CRUD、行情、轮动、风控）
- **回测可视化**：ECharts图表展示回测结果 + 实际持仓收益曲线
- **任务执行日志**：管道每阶段状态、执行历史、统计

## 技术架构

### 后端
- **FastAPI** - 高性能异步Web框架（lifespan 生命周期管理）
- **SQLAlchemy** - ORM数据库操作
- **APScheduler** - 定时任务调度（串行管道，避免并行竞态）
- **Ashare + efinance** - ETF数据获取源（双核容错）
- **OpenAI SDK** - LLM集成（兼容通义千问/Kimi/MiniMax等）
- **AgentLoop** - ReAct自主决策循环（感知→推理→行动，28个工具注册）
- **Loop Engineering** - 检查点断点续跑、失败模式库规避、IC因子自适应权重

### 前端
- **原生HTML/JS** - 无框架轻量实现
- **Fetch API** - 异步数据请求
- **ECharts 5.5** - 图表可视化

### 数据库
- **SQLite** - 本地轻量数据库
- **数据表结构**：
  - `etf_basic` - ETF基础信息
  - `etf_quotation` - 日行情/净值数据
  - `etf_daily_indicator` - 每日量化指标（5维因子 + 综合得分 + 市场排名）
  - `factor_performance` - 因子表现记录（因子值 + 未来5日收益，用于IC计算）
  - `strategy` - 配置策略（含持仓起始日、失败模式签名等扩展字段）
  - `portfolio_snapshot` - 组合快照
  - `trade_record` - 交易记录
  - `holding` - 持仓记录
  - `sentiment_data` - 舆情数据（含LLM情感分析结果）
  - `auto_strategy_log` - 自动策略执行日志
  - `experience` - 策略经验库（含失败模式库字段）
  - `experience_usage_record` - 经验应用记录
  - `pipeline_checkpoint` - 管道执行检查点（断点续跑）
  - `task_execution_log` - 定时任务执行日志

## 项目结构

```
ETFSelector/
├── app/
│   ├── __init__.py          # FastAPI应用入口（lifespan + 路由注册）
│   ├── config.py            # 配置管理
│   ├── db/                  # 数据库模块
│   │   └── database.py      # 数据库连接 + 兼容迁移
│   ├── models/              # 数据模型
│   │   ├── etf.py           # ETF模型（含每日指标）
│   │   ├── strategy.py      # 策略模型
│   │   ├── portfolio.py     # 组合/持仓/交易模型
│   │   ├── sentiment.py     # 舆情数据模型
│   │   ├── auto_strategy_log.py # 自动策略日志
│   │   ├── experience.py    # 经验库模型（含失败模式字段）
│   │   ├── factor_performance.py # 因子表现记录
│   │   ├── pipeline_checkpoint.py # 管道检查点
│   │   └── task_log.py      # 任务执行日志
│   ├── routes/              # API路由
│   │   ├── etf_routes.py    # ETF数据接口
│   │   ├── strategy_routes.py # 策略管理接口
│   │   ├── backtest_routes.py # 回测接口
│   │   ├── net_value_routes.py # 净值数据接口
│   │   ├── auto_strategy_routes.py # AI全自动策略+增强功能接口
│   │   ├── portfolio_routes.py # 组合持仓接口
│   │   ├── factor_routes.py # 因子IC/自适应权重/失败模式接口
│   │   ├── workbench_routes.py # 工作台接口
│   │   └── task_routes.py   # 任务日志接口
│   ├── agents/              # 多Agent辩论架构
│   │   ├── base.py          # Agent基类（LLM调用）
│   │   ├── technical_analyst.py # 技术分析师
│   │   ├── sentiment_analyst.py # 情绪分析师
│   │   ├── bull_researcher.py # 多头研究员
│   │   ├── bear_researcher.py # 空头研究员
│   │   ├── market_analyst.py # 研究主管（裁决）
│   │   ├── orchestrator.py  # 辩论编排器
│   │   ├── rotation_debate/ # 轮动辩论
│   │   │   ├── momentum_advocate.py # 动量派
│   │   │   ├── stability_advocate.py # 稳健派
│   │   │   ├── rotation_judge.py # 轮动主管
│   │   │   └── orchestrator.py # 轮动辩论编排
│   │   └── risk_agents/     # 风控辩论
│   │       ├── aggressive_risk.py    # 激进风控
│   │       ├── conservative_risk.py  # 保守风控
│   │       ├── neutral_risk.py       # 中性风控
│   │       ├── risk_manager.py       # 风控主管
│   │       └── risk_debate_orchestrator.py # 风控辩论编排
│   ├── agent_core/          # Agent自主决策
│   │   └── loop.py          # ReAct循环（run/run_autonomous）
│   ├── services/            # 业务服务
│   │   ├── data_service.py  # 数据获取服务
│   │   ├── data_sources.py  # 数据源封装（Ashare/efinance）
│   │   ├── backtest_service.py # 回测引擎
│   │   ├── net_value_service.py # 净值服务
│   │   ├── strategy_service.py # 策略服务
│   │   ├── portfolio_service.py # 持仓再平衡服务
│   │   ├── market_scanner_service.py # 全市场量化扫描
│   │   ├── rotation_service.py # 轮动决策服务
│   │   ├── factor_performance_service.py # 因子IC与自适应权重
│   │   ├── failure_mode_service.py # 失败模式库
│   │   ├── pipeline_checkpoint_service.py # 管道检查点
│   │   ├── sentiment_service.py # 舆情采集服务
│   │   ├── auto_strategy_executor.py # 自动策略执行器（7阶段管道）
│   │   ├── risk_controller.py # 风控控制器
│   │   ├── review_service.py # 复盘分析服务
│   │   ├── experience_manager.py # 经验生命周期管理
│   │   ├── smart_experience_matcher.py # 智能经验匹配
│   │   └── technical_indicator_service.py # 技术指标服务
│   ├── tools/               # LLM工具注册中心（28个工具）
│   │   ├── registry.py      # @tool 装饰器 + schema 生成
│   │   ├── market_tools.py  # 行情工具
│   │   ├── strategy_tools.py # 策略CRUD工具
│   │   ├── portfolio_tools.py # 组合工具
│   │   ├── risk_tools.py    # 风控工具
│   │   └── analysis_tools.py # 分析工具
│   ├── strategies/          # 策略实现
│   │   ├── base.py          # 策略基类
│   │   ├── portfolio_rebalance.py # 再平衡策略
│   │   └── generator.py     # 对话式AI策略生成器
│   ├── schemas/             # Pydantic数据验证
│   ├── tasks/               # 定时任务
│   │   ├── scheduler.py     # 串行管道调度器（含检查点）
│   │   └── task_logger.py   # 任务日志装饰器
│   ├── memory/              # 决策记忆日志
│   └── utils/               # 工具函数（交易日历等）
├── static/                  # 前端静态文件（工作台）
│   ├── workbench.html       # 量化工作台
│   ├── js/                  # 前端逻辑
│   └── css/                 # 样式
├── plans/                   # 技术方案文档
├── etf_selector.db          # SQLite数据库
├── test_agents.py           # Agent系统测试套件
├── main.py                  # 启动脚本
├── pyproject.toml           # 项目配置
├── .env.example             # 环境配置示例
├── .gitignore               # Git忽略配置
└── README.md                # 项目说明
```

## 安装与运行

### 1. 安装依赖

```bash
# 安装运行时依赖
pip install -e .

# 如需运行测试，安装 dev 依赖
pip install -e ".[dev]"
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入 LLM API Key：

```env
# 应用配置
APP_ENV=development
DEBUG=True
LOG_LEVEL=INFO

# 数据库
DATABASE_URL=sqlite:///./etf_selector.db

# LLM API配置（用于AI策略生成，兼容OpenAI协议）
LLM_API_BASE_URL=https://coding.dashscope.aliyuncs.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=qwen3.6-plus

# 定时任务配置（每个工作日执行自驱动管道：净值更新→再平衡→舆情→AI分析）
SCHEDULER_HOUR=20
SCHEDULER_MINUTE=0
```

### 3. 启动服务

```bash
python main.py
# 或
uvicorn app:app --reload --port 8000
```

访问：
- 工作台：http://localhost:8000
- API文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

### 4. 运行测试

```bash
python -m pytest
```

测试覆盖：模块导入、JSON解析、Agent prompt格式化、Orchestrator辩论流程、风控辩论、记忆日志、执行器集成。

## 核心API

### ETF数据
- `GET /api/etf/list` - 获取ETF列表
- `GET /api/etf/history/{code}` - 获取历史行情
- `GET /api/etf/overview` - 行情概览
- `GET /api/net-value/overview` - 净值概览
- `POST /api/net-value/batch-update` - 批量同步净值
- `POST /api/net-value/update-single/{code}` - 单只同步
- `GET /api/net-value/history/{code}` - 历史净值

### 策略管理
- `GET /api/strategy/list` - 获取策略列表
- `POST /api/strategy/create` - 创建自定义策略
- `POST /api/strategy/ai-chat` - 对话式AI生成策略
- `GET /api/strategy/{id}` - 策略详情
- `PUT /api/strategy/{id}` - 更新策略
- `PUT /api/strategy/{id}/status` - 更新状态
- `DELETE /api/strategy/{id}` - 删除策略

### 回测验证
- `POST /api/backtest/run` - 执行回测

### 组合持仓
- `GET /api/portfolio/{strategy_id}/history` - 每日资产快照
- `GET /api/portfolio/{strategy_id}/holdings` - 当前持仓
- `GET /api/portfolio/{strategy_id}/trades` - 交易记录
- `POST /api/portfolio/{strategy_id}/catch-up` - 补跑策略

### 轮动与因子（前缀 `/api/factors`）
- `GET /ic-history` - 各因子每日IC历史
- `GET /adaptive-weights` - 当前自适应权重（IC归一化）
- `POST /backfill` - 因子未来收益回填
- `GET /failure-modes` - 活跃失败模式与规避名单

### AI全自动策略
- `POST /api/auto-strategy/create` - 创建自动策略
- `GET /api/auto-strategy/list` - 自动策略列表
- `GET /api/auto-strategy/status` - 策略状态
- `GET /api/auto-strategy/logs` - 执行日志
- `GET /api/auto-strategy/sentiments` - 舆情数据
- `GET /api/auto-strategy/experiences` - 经验库
- `POST /api/auto-strategy/trigger-collect` - 触发舆情采集
- `POST /api/auto-strategy/trigger-analyze` - 触发市场分析
- `POST /api/auto-strategy/trigger-adjust` - 触发全管道调整
- `POST /api/auto-strategy/trigger-review` - 触发复盘
- `POST /api/auto-strategy/trigger-daily-pipeline` - 一键每日管道
- `POST /api/auto-strategy/pause` / `resume` - 暂停/恢复

### 增强功能（前缀 `/api/auto-strategy/enhanced`）
- `GET /technical-indicators` - 技术指标分析
- `GET /market-sentiment-index` - 市场情绪指数
- `GET /market-regime` - 市场阶段识别
- `GET /risk-dashboard` - 风险仪表盘
- `GET /circuit-breaker-check` - 熔断检查
- `GET /stress-test` - 压力测试
- `POST /smart-experience-match` - 智能经验匹配
- `POST /detect-anomalies` - 异常检测
- `POST /suggest-parameter-adjustments` - 参数调整建议

### 工作台与任务
- `GET /api/workbench/overview` - 工作台概览
- `GET /api/workbench/quant-summary` - 量化扫描摘要
- `GET /api/workbench/market-indicators` - 市场指标排行
- `GET /api/tasks/history` - 任务执行历史
- `GET /api/tasks/stats` - 任务统计

## 再平衡频率说明

系统支持5种再平衡频率：

| 频率 | 说明 | 触发时机 |
|------|------|---------|
| `daily` | 每日再平衡 | 每个交易日（除首日） |
| `weekly` | 每周再平衡 | 每周最后一个交易日（周五） |
| `monthly` | 每月再平衡 | 每月最后一个交易日 |
| `quarterly` | 每季度再平衡 | 每季度末最后一个交易日（3/6/9/12月） |
| `yearly` | 每年再平衡 | 每年末最后一个交易日（12月） |
| `none` | 禁用时间触发 | 仅使用偏离阈值触发 |

**偏离阈值触发**：当配置偏离超过设定阈值（默认5%）时自动触发再平衡。

## 示例策略

### 股债平衡策略
```json
{
  "name": "股债平衡组合",
  "allocation_config": {
    "159915": 0.3,  // 创业板ETF 30%
    "510300": 0.3,  // 沪深300ETF 30%
    "511010": 0.4   // 国债ETF 40%
  },
  "rebalance_freq": "quarterly",
  "rebalance_threshold": 0.05
}
```

### 核心-卫星策略
```json
{
  "name": "核心卫星配置",
  "allocation_config": {
    "510300": 0.6,  // 核心：沪深300ETF 60%
    "159915": 0.2,  // 卫星：创业板ETF 20%
    "518880": 0.2   // 卫星：黄金ETF 20%
  },
  "rebalance_freq": "monthly",
  "rebalance_threshold": 0.08
}
```

## 开发状态

- ✅ ETF数据获取与存储（Ashare双核 + efinance备用）
- ✅ 配置策略管理
- ✅ 再平衡逻辑实现（5种频率 + 偏离阈值）
- ✅ 回测引擎
- ✅ 对话式AI策略生成
- ✅ 前端工作台（行情/任务/舆情/策略Tab）
- ✅ 定时任务调度（串行管道 + 检查点断点续跑）
- ✅ AI全自动驱动策略（多Agent辩论 + 风控辩论 + 记忆机制）
- ✅ 全市场动量轮动（量化扫描 + 轮动辩论 + 失败规避）
- ✅ IC因子有效性跟踪与自适应权重
- ✅ 增强功能（技术指标 / 市场环境 / 风险仪表盘 / 异常检测）
- ✅ Agent系统测试套件（29项）

## 更新记录

### v0.4.0 (2026-08)
- ✅ 全市场动量轮动引擎（量化筛选 + 多Agent轮动辩论 + 纯量化降级）
- ✅ Loop Engineering：管道检查点断点续跑
- ✅ 失败模式库（重复失败操作自动规避，FSA式）
- ✅ IC因子有效性跟踪（Spearman秩相关）与自适应打分权重
- ✅ 量化工作台（行情/任务/舆情/策略多Tab + 实际持仓收益曲线）
- ✅ 任务执行日志系统
- ✅ 交易日历与T-1数据展示逻辑
- ✅ 策略持仓起始日与实际收益跟踪

### v0.3.0 (2026-05)
- ✅ 多Agent辩论式决策架构（技术→情绪→多空辩论→主管裁决）
- ✅ 三方风控辩论（激进/中性/保守）
- ✅ 7阶段全管道执行（事务保护）
- ✅ 记忆机制（经验生成/生命周期/应用追踪）
- ✅ 数据源全面切换 efinance
- ✅ 增强功能模块（技术指标、市场环境、风险仪表盘）

### v0.2.0 (2026-04)
- ✅ 重构为配置组合系统
- ✅ AI策略生成（对话式交互）
- ✅ 回测引擎优化

### v0.1.0 (2026-02)
- ✅ 初始版本
- ✅ ETF数据获取
- ✅ 简单回测功能

## 技术栈

- Python >= 3.10
- FastAPI >= 0.100.0
- SQLAlchemy >= 2.0.0
- APScheduler
- numpy / pandas
- Ashare / efinance / akshare
- OpenAI SDK >= 1.0.0

## 许可证

MIT License

## 联系方式

项目维护：ETFSelector Team