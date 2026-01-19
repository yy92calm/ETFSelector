"""
FastAPI应用启动文件
"""

import uvicorn
from app import app
from app.config import get_settings

if __name__ == "__main__":
    settings = get_settings()
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
        log_level=settings.log_level.lower()
    )
