"""系统配置路由 - LLM 配置管理"""

import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.schemas.schemas import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["系统配置"])


@router.get("/llm", response_model=APIResponse)
def get_llm_config_api(db: Session = Depends(get_db)):
    """获取 LLM 配置（API Key 脱敏）"""
    from app.services.config_service import get_llm_config_masked
    return APIResponse(data=get_llm_config_masked(db))


@router.put("/llm", response_model=APIResponse)
def update_llm_config_api(req: dict, db: Session = Depends(get_db)):
    """更新 LLM 配置

    Body: {llm_api_base_url, llm_api_key, llm_model}
    api_key 为空时保留原值
    """
    from app.services.config_service import update_llm_config
    result = update_llm_config(
        req.get("llm_api_base_url"),
        req.get("llm_api_key"),
        req.get("llm_model"),
        db,
    )
    return APIResponse(message="LLM配置已更新", data=result)


@router.post("/llm/test", response_model=APIResponse)
def test_llm_connection_api(db: Session = Depends(get_db)):
    """测试 LLM 连接是否可用"""
    from app.services.config_service import get_llm_config
    cfg = get_llm_config(db)

    if not cfg.get("llm_api_key", "").strip():
        return APIResponse(code=400, message="API Key 未配置")

    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=cfg["llm_api_key"],
            base_url=cfg["llm_api_base_url"],
        )
        resp = client.chat.completions.create(
            model=cfg["llm_model"],
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=5,
        )
        reply = resp.choices[0].message.content
        return APIResponse(
            message="连接成功",
            data={"model": cfg["llm_model"], "reply": reply},
        )
    except Exception as e:
        logger.error(f"LLM连接测试失败: {e}")
        return APIResponse(code=400, message=f"连接失败: {e}", data={"error": str(e)})
