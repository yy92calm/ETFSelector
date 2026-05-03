"""FastAPI 应用初始化"""

import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db.database import init_db
from app.routes import etf_routes, strategy_routes, backtest_routes, net_value_routes  # noqa: F401
# portfolio_routes 暂时禁用（实盘模拟服务待重构）
# from app.routes import portfolio_routes  # noqa: F401

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    description="ETF量化选择系统 — 行情获取 · 策略回测 · 模拟实盘",
    version="0.2.0",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# 注册路由
app.include_router(etf_routes.router)
app.include_router(strategy_routes.router)
app.include_router(backtest_routes.router)
app.include_router(net_value_routes.router)  # 净值数据路由
# app.include_router(portfolio_routes.router)  # 暂时禁用


@app.on_event("startup")
def startup():
    logger.info("应用启动 ...")
    init_db()
    logger.info("数据库初始化成功")

    from app.tasks.scheduler import get_scheduler
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info(f"定时任务已启动 (每日 {settings.scheduler_hour}:{settings.scheduler_minute:02d})")


@app.on_event("shutdown")
def shutdown():
    from app.tasks.scheduler import get_scheduler
    try:
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass
    logger.info("应用已关闭")


@app.get("/")
def root():
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": settings.app_name, "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env}
