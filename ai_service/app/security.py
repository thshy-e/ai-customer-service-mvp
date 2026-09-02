import hashlib
import hmac


def verify_chatwoot_signature(
    raw_body: bytes,
    secret: str,
    signature: str | None,
    timestamp: str | None,
) -> bool:
    # 本地未配置密钥时放行；正式环境配置后必须校验时间戳和 HMAC。
    if not secret:
        return True
    if not signature or not timestamp:
        return False

    signed = timestamp.encode() + b"." + raw_body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(signature, expected)
