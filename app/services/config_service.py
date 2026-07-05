"""系统配置服务 - LLM 配置的读写与运行时同步"""

import logging
from typing import Dict
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

LLM_CONFIG_KEYS = ["llm_api_base_url", "llm_api_key", "llm_model"]


def get_llm_config(db: Session) -> Dict[str, str]:
    """读取当前生效的 LLM 配置（DB 覆盖 .env）"""
    s = get_settings()
    result = {
        "llm_api_base_url": s.llm_api_base_url,
        "llm_api_key": s.llm_api_key,
        "llm_model": s.llm_model,
    }
    configs = db.query(SystemConfig).filter(SystemConfig.key.in_(LLM_CONFIG_KEYS)).all()
    for c in configs:
        if c.value:
            result[c.key] = c.value
    return result


def get_llm_config_masked(db: Session) -> Dict:
    """脱敏版 LLM 配置（供前端展示，API Key 不返回明文）"""
    cfg = get_llm_config(db)
    key = cfg.get("llm_api_key") or ""
    masked = None
    if key.strip():
        masked = "****" + key[-4:] if len(key) > 4 else "****"
    return {
        "llm_api_base_url": cfg.get("llm_api_base_url"),
        "llm_api_key_masked": masked,
        "llm_api_key_configured": bool(key.strip()),
        "llm_model": cfg.get("llm_model"),
    }


def update_llm_config(base_url: str, api_key: str, model: str, db: Session) -> Dict:
    """更新 LLM 配置：写 DB + 同步 settings 单例

    api_key 为空字符串时保留原值（允许只改 base_url/model）。
    """
    s = get_settings()

    updates = {}
    if base_url is not None and base_url.strip():
        updates["llm_api_base_url"] = base_url.strip()
    if api_key and api_key.strip():
        updates["llm_api_key"] = api_key.strip()
    if model is not None and model.strip():
        updates["llm_model"] = model.strip()

    for key, value in updates.items():
        existing = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if existing:
            existing.value = value
        else:
            db.add(SystemConfig(key=key, value=value))
        setattr(s, key, value)

    db.commit()
    logger.info(f"LLM配置已更新: {list(updates.keys())}")
    return get_llm_config_masked(db)


def sync_llm_config_from_db(db: Session):
    """启动时从 DB 加载 LLM 配置到 settings 单例"""
    s = get_settings()
    configs = db.query(SystemConfig).filter(SystemConfig.key.in_(LLM_CONFIG_KEYS)).all()
    for c in configs:
        if c.value:
            setattr(s, c.key, c.value)
    if configs:
        logger.info(f"从数据库加载 {len(configs)} 项 LLM 配置")
