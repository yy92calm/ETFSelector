"""
FastAPI应用初始化和配置
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import logging
from pathlib import Path

from app.config import get_settings
from app.db.database import init_db
from app.routes import etf_routes

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

settings = get_settings()

# 创建FastAPI应用
app = FastAPI(
    title=settings.app_name,
    description="智能ETF选择系统 - 提供ETF行情分析、虚拟交易和策略回测功能",
    version="0.1.0",
    debug=settings.debug
)

# 配置CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
static_path = Path(__file__).parent.parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
    logger.info(f"静态文件挂载: {static_path}")

# 注册路由
app.include_router(etf_routes.router)


@app.on_event("startup")
async def startup_event():
    """应用启动事件"""
    logger.info("应用启动...")
    try:
        init_db()
        logger.info("数据库初始化成功")
    except Exception as e:
        logger.error(f"数据库初始化失败: {e}")


@app.on_event("shutdown")
async def shutdown_event():
    """应用关闭事件"""
    logger.info("应用关闭...")


@app.get("/")
async def root():
    """根路径 - 返回index.html"""
    static_path = Path(__file__).parent.parent / "static" / "index.html"
    if static_path.exists():
        return FileResponse(str(static_path))
    return {
        "message": "欢迎使用智能ETF选择系统",
        "version": "0.1.0",
        "docs": "/docs",
        "openapi": "/openapi.json"
    }


@app.get("/index.html")
async def index():
    """返回index.html"""
    static_path = Path(__file__).parent.parent / "static" / "index.html"
    if static_path.exists():
        return FileResponse(str(static_path))
    return {"error": "index.html not found"}


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "environment": settings.app_env
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        reload=settings.debug
    )
