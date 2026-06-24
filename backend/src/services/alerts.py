"""Slack/webhook-alerts. Tyst om ALERT_WEBHOOK saknas — gör inget vid dev."""
from __future__ import annotations
import httpx
from ..config import settings


def alert(message: str, level: str = "info") -> None:
    if not settings.alert_webhook:
        return
    try:
        httpx.post(settings.alert_webhook, json={"text": f"[WLA Scout {level.upper()}] {message}"}, timeout=5.0)
    except Exception:  # noqa: BLE001
        pass
