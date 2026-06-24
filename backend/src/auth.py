"""Delad inloggning v1. Lösen jämförs konstant-tid mot env. Session via JWT
i HTTP-only-cookie. Alla skrivande endpoints kräver `Depends(require_auth)`."""
from __future__ import annotations
import hmac
import secrets
from datetime import datetime, timedelta, timezone
import jwt
from fastapi import Cookie, HTTPException, status
from .config import settings


SESSION_COOKIE = "wla_session"
SESSION_TTL_HOURS = 24 * 7


def check_password(password: str) -> bool:
    expected = settings.dashboard_auth_password or ""
    if not expected:
        return False
    return hmac.compare_digest(password.encode(), expected.encode())


def issue_session_token() -> str:
    secret = settings.dashboard_auth_secret or secrets.token_urlsafe(32)
    payload = {
        "sub": "redaktion",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=SESSION_TTL_HOURS),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def require_auth(wla_session: str | None = Cookie(default=None)) -> str:
    if not wla_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Login required")
    try:
        payload = jwt.decode(wla_session, settings.dashboard_auth_secret, algorithms=["HS256"])
        return payload.get("sub", "redaktion")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid session")
