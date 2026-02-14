"""应用配置"""

from pydantic_settings import BaseSettings
from functools import lru_cache
import os


class Settings(BaseSettings):
    app_name: str = "ETF量化选择系统"
    app_env: str = "development"
    debug: bool = True
    log_level: str = "INFO"

    # 数据库
    database_url: str = "sqlite:///./etf_selector.db"

    # LLM API（用于AI策略生成）
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"

    # 定时任务
    scheduler_hour: int = 18
    scheduler_minute: int = 0

    model_config = {"env_file": ".env", "case_sensitive": False}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
