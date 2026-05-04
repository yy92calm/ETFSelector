# ETF量化选择系统

ETF配置组合系统 - 智能配置、自动再平衡、回测验证

## 项目简介

基于配置比例的ETF组合管理系统，支持AI智能生成策略、自动再平衡、历史回测验证。

## 核心功能

### 🎯 策略配置
- **配置组合管理**：自定义ETF配置比例
- **再平衡策略**：支持日/周/月/季/年频率再平衡
- **偏离阈值触发**：当配置偏离超过阈值自动调整
- **AI策略生成**：对话式AI辅助生成配置策略（支持通义千问、Kimi等）

### 📊 数据服务
- **全市场ETF数据**：覆盖沪深交易所主要ETF
- **净值数据获取**：证监会官方净值数据源（每日更新）
- **历史行情存储**：SQLite本地数据库存储
- **定时任务调度**：每日自动更新净值数据

### 🔬 回测验证
- **完整回测引擎**：模拟历史持仓与再平衡过程
- **多维度指标**：收益率、最大回撤、夏普比率、胜率
- **可视化展示**：净值曲线、月度收益、时间段收益分析
- **交易记录追踪**：完整记录每次再平衡操作

### 💻 前端交互
- **单页面应用**：原生HTML/JavaScript实现
- **实时数据展示**：ETF净值概览、策略列表
- **对话式AI**：多轮对话优化策略配置
- **回测可视化**：图表展示回测结果

## 技术架构

### 后端
- **FastAPI** - 高性能异步Web框架
- **SQLAlchemy** - ORM数据库操作
- **APScheduler** - 定时任务调度
- **akshare/baostock** - 数据获取源
- **OpenAI SDK** - LLM集成

### 前端
- **原生HTML/JS** - 无框架轻量实现
- **Fetch API** - 异步数据请求
- **Chart.js** - 图表可视化

### 数据库
- **SQLite** - 本地轻量数据库
- **数据表结构**：
  - `etf_basic` - ETF基础信息（322只）
  - `etf_quotation` - 日行情/净值数据（5475条）
  - `strategy` - 配置策略（2个）
  - `portfolio_snapshot` - 组合快照
  - `trade_record` - 交易记录
  - `holding` - 持仓记录

## 项目结构

```
ETFSelector/
├── app/
│   ├── __init__.py          # FastAPI应用入口
│   ├── config.py            # 配置管理
│   ├── db/                  # 数据库模块
│   │   └── database.py      # 数据库连接
│   ├── models/              # 数据模型
│   │   ├── etf.py           # ETF模型
│   │   ├── strategy.py      # 策略模型
│   │   └ portfolio.py       # 组合模型
│   ├── routes/              # API路由
│   │   ├── etf_routes.py    # ETF数据接口
│   │   ├── strategy_routes.py # 策略管理接口
│   │   ├── backtest_routes.py # 回测接口
│   │   └ net_value_routes.py # 净值数据接口
│   ├── services/            # 业务服务
│   │   ├── data_service.py  # 数据获取服务
│   │   ├── backtest_service.py # 回测引擎
│   │   ├── net_value_service.py # 净值服务
│   │   ├── strategy_service.py # 策略服务
│   │   ├── data_sources.py  # 数据源封装
│   │   └ csrc_data_source.py # 证监会数据源
│   ├── strategies/          # 策略实现
│   │   ├── base.py          # 策略基类
│   │   ├── portfolio_rebalance.py # 再平衡策略
│   │   ├── generator.py     # AI策略生成器
│   │   └ templates/         # 策略模板
│   ├── schemas/             # 数据验证
│   ├── tasks/               # 定时任务
│   │   └ scheduler.py       # 任务调度器
│   └ utils/                 # 工具函数
├── static/                  # 前端静态文件
│   ├── index.html           # 主页面
│   ├── js/
│   │   └ app.js             # 前端逻辑
│   ├── css/
│   │   └ style.css          # 样式文件
├── etf_selector.db          # SQLite数据库
├── main.py                  # 启动脚本
├── pyproject.toml           # 项目配置
├── .env                     # 环境配置（已忽略）
├── .gitignore               # Git忽略配置
└── README.md                # 项目说明
```

## 安装与运行

### 1. 安装依赖

```bash
pip install -e .
```

### 2. 配置环境变量

创建 `.env` 文件：

```env
# 应用配置
APP_ENV=development
DEBUG=True
LOG_LEVEL=INFO

# 数据库
DATABASE_URL=sqlite:///./etf_selector.db

# LLM API配置（用于AI策略生成）
LLM_API_BASE_URL=https://coding.dashscope.aliyuncs.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=qwen3.6-plus

# 定时任务配置
SCHEDULER_HOUR=18
SCHEDULER_MINUTE=0
```

### 3. 启动服务

```bash
python main.py
```

访问：
- 主页面：http://localhost:8000
- API文档：http://localhost:8000/docs
- 健康检查：http://localhost:8000/health

## 核心API

### ETF数据
- `GET /api/etf/list` - 获取ETF列表
- `GET /api/etf/quotes/{code}` - 获取历史行情
- `GET /api/etf/net-value/overview` - 净值概览
- `POST /api/etf/net-value/sync` - 同步净值数据

### 策略管理
- `GET /api/strategies` - 获取策略列表
- `POST /api/strategies` - 创建策略
- `PUT /api/strategies/{id}` - 更新策略
- `DELETE /api/strategies/{id}` - 删除策略
- `POST /api/strategies/generate` - AI生成策略

### 回测验证
- `POST /api/backtest/run` - 执行回测
- `GET /api/backtest/result/{id}` - 获取回测结果

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

- ✅ ETF数据获取与存储
- ✅ 配置策略管理
- ✅ 再平衡逻辑实现
- ✅ 回测引擎开发
- ✅ AI策略生成（对话式）
- ✅ 前端界面开发
- ✅ 定时任务调度
- 🚧 实盘模拟（待重构）

## 更新记录

### v0.3.0 (2026-05)
- ✅ 优化再平衡时间触发逻辑
- ✅ 支持日/周/月/季/年频率
- ✅ 修复月末再平衡重复触发问题
- ✅ 改进净值数据缺失处理

### v0.2.0 (2026-04)
- ✅ 重构为配置组合系统
- ✅ AI策略生成（对话式交互）
- ✅ 证监会净值数据源集成
- ✅ 回测引擎优化

### v0.1.0 (2026-02)
- ✅ 初始版本
- ✅ ETF数据获取
- ✅ 简单回测功能

## 技术栈

- Python >= 3.10
- FastAPI >= 0.100.0
- SQLAlchemy >= 2.0.0
- akshare >= 1.14.0
- OpenAI SDK >= 1.0.0

## 许可证

MIT License

## 联系方式

项目维护：ETFSelector Team