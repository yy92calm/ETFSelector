"""FastAPI 应用初始化"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse

from app.config import get_settings
from app.db.database import init_db
from app.routes import etf_routes, strategy_routes, backtest_routes, net_value_routes, auto_strategy_routes, portfolio_routes, config_routes, chat_routes, workbench_routes, task_routes, factor_routes, auth_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动 ...")
    init_db()
    logger.info("数据库初始化成功")

    # 从数据库加载运行时 LLM 配置到 settings 单例
    from app.db.database import SessionLocal
    from app.services.config_service import sync_llm_config_from_db
    _db = SessionLocal()
    try:
        sync_llm_config_from_db(_db)
    finally:
        _db.close()

    from app.tasks.scheduler import get_scheduler
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("定时任务已启动")

    yield

    from app.tasks.scheduler import get_scheduler
    try:
        scheduler = get_scheduler()
        if scheduler.running:
            scheduler.shutdown(wait=False)
    except Exception:
        pass
    logger.info("应用已关闭")


app = FastAPI(
    title=settings.app_name,
    description="ETF量化选择系统 — 行情获取 · 策略回测 · 模拟实盘 · AI自动策略",
    version="0.3.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(etf_routes.router)
app.include_router(strategy_routes.router)
app.include_router(backtest_routes.router)
app.include_router(net_value_routes.router)
app.include_router(auto_strategy_routes.router)
app.include_router(portfolio_routes.router)
app.include_router(config_routes.router)
app.include_router(chat_routes.router)
app.include_router(workbench_routes.router)
app.include_router(task_routes.router)
app.include_router(factor_routes.router)
app.include_router(auth_routes.router)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and not path.startswith("/api/auth/"):
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            from app.routes.auth_routes import verify_token
            token = auth_header[7:]
            if not verify_token(token):
                return JSONResponse(status_code=401, content={"code": 401, "message": "未授权", "data": None})
        else:
            return JSONResponse(status_code=401, content={"code": 401, "message": "未授权", "data": None})
    return await call_next(request)


@app.get("/")
def root():
    # 优先返回工作台页面
    workbench = static_dir / "workbench.html"
    if workbench.exists():
        return FileResponse(str(workbench))
    index = static_dir / "index.html"
    if index.exists():
        return FileResponse(str(index))
    return {"message": settings.app_name, "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok", "env": settings.app_env}
