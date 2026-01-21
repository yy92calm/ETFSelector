"""
FastAPI应用启动文件
"""

import uvicorn
import asyncio
from app import app
from app.config import get_settings
from app.tasks.scheduler import get_etf_scheduler

if __name__ == "__main__":
    settings = get_settings()
    
    # 启动ETF数据更新调度器
    scheduler = get_etf_scheduler()
    scheduler.start()
    
    try:
        uvicorn.run(
            "app:app",
            host="0.0.0.0",
            port=8000,
            reload=settings.debug,
            log_level=settings.log_level.lower()
        )
    except KeyboardInterrupt:
        # 关闭调度器
        scheduler.shutdown()
        print("ETF Selector已关闭")
