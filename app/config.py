from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    """应用配置"""
    
    # 应用基本配置
    app_name: str = "智能ETF选择系统"
    app_env: str = os.getenv("APP_ENV", "development")
    debug: bool = os.getenv("DEBUG", "True").lower() == "true"
    
    # 数据库配置
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./etf_selector.db")
    
    # Qtrade API配置
    qtrade_api_base_url: str = os.getenv("QTRADE_API_BASE_URL", "http://qt.gtimg.com")
    
    # 日志配置
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    
    class Config:
        env_file = ".env"
        case_sensitive = False


@lru_cache()
def get_settings() -> Settings:
    """获取应用配置单例"""
    return Settings()
