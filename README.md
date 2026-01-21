# ETFSelector

ETFSelector 是一个基于 FastAPI 的 ETF 数据获取和分析系统，支持实时行情获取、市场分类管理、定时数据更新等功能。

## 功能特性

- **ETF行情数据获取**：从 Qtrade API 获取实时 ETF 行情数据
- **市场分类管理**：支持上证、深证、全市场 ETF 分类管理
- **批量行情获取**：支持批量获取多个 ETF 的行情数据
- **定时任务调度**：自动在交易时间更新 ETF 行情
- **数据持久化**：使用 SQLite 数据库存储 ETF 基础信息和行情数据
- **全市场ETF列表**：支持从API获取最新的全市场ETF列表
- **定时ETF列表更新**：每天早上9:00自动更新全市场ETF列表

## 技术栈

- **后端框架**：FastAPI
- **数据库**：SQLite + SQLAlchemy ORM
- **HTTP 客户端**：aiohttp
- **任务调度**：APScheduler
- **配置管理**：Pydantic Settings

## 快速开始

### 环境准备

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install fastapi uvicorn sqlalchemy python-dotenv pydantic pydantic-settings requests aiohttp apscheduler

# 设置环境变量
cp .env.example .env
```

### 数据库初始化

```bash
python init_db.py
```

### 启动应用

```bash
python main.py
```

## API 接口

### ETF 数据接口

- `GET /api/etf/list` - 获取 ETF 列表
- `GET /api/etf/latest/{etf_code}` - 获取 ETF 最新行情
- `GET /api/etf/history/{etf_code}` - 获取 ETF 历史行情
- `GET /api/etf/detail/{etf_code}` - 获取 ETF 详细信息
- `POST /api/etf/fetch-quotes` - 批量获取行情数据

### 市场管理接口

- `POST /api/etf/market/{market_type}` - 获取指定市场行情
- `GET /api/etf/market/{market_type}/etfs` - 获取指定市场 ETF 列表
- `POST /api/etf/market/{market_type}/etfs` - 添加 ETF 到指定市场
- `DELETE /api/etf/market/{market_type}/etfs` - 从指定市场移除 ETF
- `GET /api/etf/market/{market_type}/quotes` - 获取指定市场最新行情

### 支持的市场类型

- `shanghai` - 上证市场
- `shenzhen` - 深证市场
- `all` - 上深全市场主流ETF
- `all_etfs` - 全市场所有ETF（包括数据库中存储的所有ETF）

### 调度器管理接口

- `POST /api/etf/scheduler/{action}` - 管理定时任务调度器 (start/stop/status)

## 使用示例

### 获取指定市场ETF列表
```bash
curl -X GET "http://localhost:8000/api/etf/market/shanghai/etfs"
```

### 添加ETF代码到市场
```bash
curl -X POST "http://localhost:8000/api/etf/market/shanghai/etfs" \
  -H "Content-Type: application/json" \
  -d '{"etf_codes": ["sh512690"]}'
```

### 获取指定市场所有ETF行情
```bash
curl -X POST "http://localhost:8000/api/etf/market/shanghai"
```

### 管理定时任务调度器
```bash
# 查看调度器状态
curl -X POST "http://localhost:8000/api/etf/scheduler/status"

# 启动调度器
curl -X POST "http://localhost:8000/api/etf/scheduler/start"

# 停止调度器
curl -X POST "http://localhost:8000/api/etf/scheduler/stop"
```

## 项目结构

```
ETFSelector/
├── app/                          # FastAPI应用核心
│   ├── __init__.py              # 应用初始化
│   ├── config.py                # 配置管理
│   ├── services/
│   │   └── data_service.py      # 数据获取服务
│   ├── models/
│   │   ├── etf_basic.py         # ETF基础信息模型
│   │   └── etf_quotation.py     # ETF行情数据模型
│   ├── routes/
│   │   └── etf_routes.py        # ETF API路由
│   ├── schemas/
│   │   └── etf_schemas.py       # 数据验证schema
│   ├── utils/
│   │   └── api_client.py        # Qtrade API客户端
│   ├── tasks/
│   │   └── scheduler.py         # 定时任务调度器
│   └── db/
│       └── database.py          # 数据库配置
├── static/                       # 前端静态文件
│   ├── index.html               # 主页面
│   ├── css/style.css            # 样式
│   └── js/main.js               # 脚本
├── main.py                       # 启动文件
├── init_db.py                    # 数据库初始化
├── pyproject.toml               # 项目配置
└── .env                         # 环境变量
```

## 主要组件

### ETF 市场管理器 (ETFMarketManager)

- 动态管理 ETF 代码列表
- 支持上证、深证、全市场分类
- 提供按市场类型批量获取行情功能

### ETF 调度器 (ETFScheduler)

- 基于 APScheduler 的定时任务调度器
- 自动在交易时间更新 ETF 行情
- 支持上证、深证和全市场行情定时更新
- 交易时间调度策略（工作日 8:30-15:00）
- 通过 API 控制调度器启动、停止和状态查询

## 许可证

MIT
