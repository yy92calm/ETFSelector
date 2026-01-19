# 快速开始指南

## 环境要求

- Python 3.10+
- macOS (或其他 Unix-like 系统)
- uv 包管理器（用于虚拟环境管理）

## 安装步骤

### 1. 创建虚拟环境（如果还没有）

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install --upgrade pip setuptools wheel
pip install fastapi uvicorn sqlalchemy python-dotenv pydantic pydantic-settings requests aiohttp
```

或使用 pyproject.toml：

```bash
pip install -e .
```

### 3. 配置环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 文件（可选，默认值已可用）：

```
DATABASE_URL=sqlite:///./etf_selector.db
QTRADE_API_BASE_URL=http://qt.gtimg.com
LOG_LEVEL=INFO
APP_ENV=development
DEBUG=True
```

### 4. 初始化数据库（可选）

```bash
source .venv/bin/activate
python init_db.py
```

## 运行应用

### 启动服务器

```bash
source .venv/bin/activate
python main.py
```

应用将运行在 `http://localhost:8000`

### 查看 API 文档

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## API 端点

### ETF 行情相关

- `GET /api/etf/list` - 获取所有 ETF 列表
- `GET /api/etf/latest/{etf_code}` - 获取 ETF 最新行情
- `GET /api/etf/history/{etf_code}?start_date=2024-01-01&end_date=2024-12-31` - 获取历史行情
- `GET /api/etf/detail/{etf_code}` - 获取 ETF 详细信息
- `POST /api/etf/fetch-quotes` - 从 Qtrade 获取行情数据

### 系统健康检查

- `GET /` - 获取应用信息
- `GET /health` - 健康检查

## 测试 API

### 获取行情数据

```bash
curl -X POST http://localhost:8000/api/etf/fetch-quotes \
  -H "Content-Type: application/json" \
  -d '{"etf_codes": ["sh510050", "sh510300", "sh510500"]}'
```

### 获取 ETF 详情

```bash
curl http://localhost:8000/api/etf/detail/sh510050
```

### 获取 ETF 列表

```bash
curl http://localhost:8000/api/etf/list
```

## 项目结构

```
ETFSelector/
├── app/                          # FastAPI应用
│   ├── __init__.py              # 应用初始化
│   ├── config.py                # 配置管理
│   ├── services/                # 业务服务
│   │   └── data_service.py      # 数据获取服务
│   ├── models/                  # 数据库模型
│   │   ├── etf_basic.py         # ETF基础信息
│   │   └── etf_quotation.py     # ETF行情数据
│   ├── routes/                  # API路由
│   │   └── etf_routes.py        # ETF相关接口
│   ├── schemas/                 # Pydantic数据模型
│   │   └── etf_schemas.py       # ETF相关schema
│   ├── utils/                   # 工具模块
│   │   └── api_client.py        # Qtrade API客户端
│   └── db/                      # 数据访问层
│       └── database.py          # 数据库配置
│
├── static/                       # 前端静态文件
│   ├── index.html               # 主页
│   ├── css/                     # 样式文件
│   │   └── style.css
│   └── js/                      # 脚本文件
│       └── main.js
│
├── tests/                        # 测试文件
├── migrations/                   # 数据库迁移
├── docs/                         # 文档
├── main.py                       # 应用启动入口
├── init_db.py                    # 数据库初始化脚本
├── pyproject.toml               # 项目配置
├── .env                         # 环境变量
└── README.md                    # 项目简介
```

## 主要功能

✅ **ETF 行情获取** - 集成 Qtrade API 获取实时行情数据

✅ **数据存储** - SQLite 数据库存储 ETF 基础信息和行情历史

✅ **RESTful API** - 完整的 API 接口支持查询和数据获取

✅ **Web 前端** - 简单的 HTML/CSS/JS 前端界面（开发中）

## 开发注意事项

### 模拟数据

目前系统包含模拟数据功能。如果 Qtrade API 无法获取数据，系统会自动使用预设的模拟数据：

- `sh510050` - 华夏上证50ETF (¥2.45, +0.82%)
- `sh510300` - 华夏沪深300ETF (¥3.12, -0.32%)
- `sh510500` - 华夏中证500ETF (¥5.23, +1.15%)

### 日志

应用日志输出到控制台，日志级别由 `LOG_LEVEL` 环境变量控制（默认 INFO）。

### 数据库

SQLite 数据库文件位置：`./etf_selector.db`

## 常见问题

### 端口被占用

如果 8000 端口已被占用，编辑 `main.py` 修改端口号：

```python
uvicorn.run(
    "app:app",
    host="0.0.0.0",
    port=8001,  # 改为其他端口
    ...
)
```

### 获取行情失败

- 检查网络连接
- 确保 Qtrade API URL 正确
- 系统会自动降级到模拟数据

### 数据库错误

删除 `etf_selector.db` 并重新启动应用即可重新初始化数据库。

## 下一步

- [ ] 完成虚拟交易模块
- [ ] 实现技术指标计算
- [ ] 支持策略回测
- [ ] 增强前端界面
- [ ] 添加用户认证
- [ ] 性能优化和部署

## 许可证

MIT
