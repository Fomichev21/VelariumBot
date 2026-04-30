from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

from werkzeug.security import check_password_hash, generate_password_hash

from config import settings


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(password: str) -> bool:
    if settings.admin_password_hash:
        return check_password_hash(settings.admin_password_hash, password)
    return hmac.compare_digest(password, settings.admin_password)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def create_jwt(payload: dict[str, Any], expires_in: int = 3600) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    body = dict(payload)
    body["exp"] = int(time.time()) + expires_in

    header_part = _b64encode(json.dumps(header, separators=(",", ":")).encode("utf-8"))
    body_part = _b64encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signing_input = f"{header_part}.{body_part}".encode("utf-8")
    signature = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        signing_input,
        hashlib.sha256,
    ).digest()
    return f"{header_part}.{body_part}.{_b64encode(signature)}"


def decode_jwt(token: str) -> dict[str, Any] | None:
    try:
        header_part, body_part, signature_part = token.split(".")
        signing_input = f"{header_part}.{body_part}".encode("utf-8")
        expected_signature = hmac.new(
            settings.app_secret_key.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(expected_signature, _b64decode(signature_part)):
            return None

        payload = json.loads(_b64decode(body_part).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except Exception:
        return None
