from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .settings import settings


@dataclass(frozen=True)
class SalesPrincipal:
    email: str
    role: str


def _secret() -> bytes:
    # 会话密钥优先使用专用配置，本地没有配置时才使用开发默认值。
    return (settings.sales_session_secret or settings.chatwoot_webhook_secret or "local-sales-mvp-secret").encode()


def issue_token(email: str, role: str) -> str:
    # MVP 使用带 HMAC 签名的轻量令牌，不额外引入认证服务。
    payload = json.dumps({"email": email, "role": role}, ensure_ascii=False, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signature = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
    return f"{encoded}.{signature}"


def decode_token(token: str) -> SalesPrincipal | None:
    # 先验签再读取内容，任何格式或签名错误都按未登录处理。
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(_secret(), encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
        if payload.get("role") not in {"admin", "sales"}:
            return None
        return SalesPrincipal(email=str(payload["email"]), role=str(payload["role"]))
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def authenticate(email: str, password: str) -> SalesPrincipal | None:
    # 当前只支持配置文件中的管理员和业务员两个演示账号。
    configured = [
        (settings.sales_admin_email, settings.sales_admin_password, "admin"),
        (settings.sales_user_email, settings.sales_user_password, "sales"),
    ]
    for expected_email, expected_password, role in configured:
        if expected_email and hmac.compare_digest(email.casefold(), expected_email.casefold()) and expected_password and hmac.compare_digest(password, expected_password):
            return SalesPrincipal(email=expected_email, role=role)
    return None


def require_sales_principal(request: Request, admin_only: bool = False) -> SalesPrincipal:
    # 路由统一从 Authorization: Bearer 读取销售工作台身份。
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip()
    principal = decode_token(token) if token else None
    if not principal or (admin_only and principal.role != "admin"):
        raise HTTPException(status_code=401 if not principal else 403, detail="销售助手需要登录或管理员权限")
    return principal
