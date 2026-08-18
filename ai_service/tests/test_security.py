import hashlib
import hmac

from app.security import verify_chatwoot_signature


def test_signature_is_verified():
    body = b'{"event":"message_created"}'
    timestamp = "1723890000"
    secret = "test-secret"
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert verify_chatwoot_signature(body, secret, f"sha256={digest}", timestamp)


def test_invalid_signature_is_rejected():
    assert not verify_chatwoot_signature(b"{}", "secret", "sha256=invalid", "123")


def test_empty_secret_allows_local_development():
    assert verify_chatwoot_signature(b"{}", "", None, None)

