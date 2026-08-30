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

    # 交易成本
    commission_rate: float = 0.005  # 单笔买卖金额费率 0.5%
    commission_min: float = 10.0    # 单笔最低手续费（元）

    # LLM API（用于AI策略生成）
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    # 模型名前缀 → base_url 路由表（JSON，最长前缀匹配；key 是模型名前缀）
    # 示例: {"qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    #        "deepseek": "https://api.deepseek.com/v1"}
    llm_model_aliases: str = ""

    # AI 对话
    chat_approval_timeout: int = 120  # 写操作审批等待超时秒数

    # 上下文压缩
    context_window_tokens: int = 128000
    compaction_threshold: float = 0.8
    compaction_min_tokens: int = 20000

    # 数据源容错（efinance 防封）
    data_source_retries: int = 3  # 单次拉取失败重试次数
    data_source_retry_base: float = 1.5  # 退避基数（秒）
    circuit_break_failures: int = 5  # 连续失败触发熔断次数
    circuit_break_seconds: int = 600  # 熔断时长（秒）
    # ETF列表本地缓存（相对路径基于项目根目录，部署时可改为绝对路径）
    etf_list_cache_path: str = "app/data/etf_list_cache.json"
    # 定时任务数据源是否允许降级到 efinance：
    #   False（默认）= Ashare 失败即放弃（开发环境防封策略）
    #   True = Ashare 失败时降级到 efinance（部署环境网络受限时使用）
    scheduled_task_allow_fallback: bool = False

    # 多Agent辩论
    debate_max_data_lag_days: int = 3  # 辩论前允许的数据最大滞后自然日，超过则自动同步

    # 定时任务（每个工作日20:00更新净值数据）
    scheduler_hour: int = 20
    scheduler_minute: int = 0

    # MCP servers 配置（JSON字符串，示例见 .env.example）
    mcp_servers: str = ""

    model_config = {"env_file": ".env", "case_sensitive": False}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
