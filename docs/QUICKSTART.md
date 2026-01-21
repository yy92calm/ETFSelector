# 快速开始

## 环境准备

```bash
# 创建虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖
pip install fastapi uvicorn sqlalchemy python-dotenv pydantic pydantic-settings requests aiohttp apscheduler

# 设置环境变量
cp .env.example .env
```

## 数据库初始化

```bash
python init_db.py
```

## 启动应用

```bash
python main.py
```

## 访问应用

- 应用主页: http://localhost:8000
- API 文档: http://localhost:8000/docs
- ReDoc 文档: http://localhost:8000/redoc
- 健康检查: http://localhost:8000/health
