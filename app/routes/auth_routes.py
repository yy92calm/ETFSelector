"""认证授权 API"""

import hashlib
import hmac
import time
import logging
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.schemas.schemas import APIResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["认证"])

ACCESS_PASSWORD = "test@123"
TOKEN_SECRET = "etf-selector-secret-key-2024"
TOKEN_EXPIRE_SECONDS = 86400 * 7


def generate_token() -> str:
    timestamp = str(int(time.time()))
    payload = f"{timestamp}:{ACCESS_PASSWORD}"
    signature = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{timestamp}:{signature}"


def verify_token(token: str) -> bool:
    try:
        parts = token.split(":")
        if len(parts) != 2:
            return False
        timestamp, signature = parts
        if int(timestamp) < time.time() - TOKEN_EXPIRE_SECONDS:
            return False
        payload = f"{timestamp}:{ACCESS_PASSWORD}"
        expected = hmac.new(TOKEN_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


class LoginRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=100)


@router.post("/login", response_model=APIResponse)
def login(request: LoginRequest):
    if request.password != ACCESS_PASSWORD:
        return APIResponse(code=401, message="密码错误", data=None)
    token = generate_token()
    return APIResponse(data={"token": token})


@router.get("/check", response_model=APIResponse)
def check_auth(request: Request):
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        if verify_token(token):
            return APIResponse(data={"valid": True})
    return APIResponse(code=401, message="未授权", data={"valid": False})
