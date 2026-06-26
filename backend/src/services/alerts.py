"""Slack/webhook-alerts. Tyst om ALERT_WEBHOOK saknas — gör inget vid dev."""
from __future__ import annotations
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..config import settings
from ..db import SessionLocal
from ..models import Candidate, DailyDelivery, RunLog


def alert(message: str, level: str = "info") -> None:
    if not settings.alert_webhook:
        return
    try:
        httpx.post(settings.alert_webhook, json={"text": f"[WLA Scout {level.upper()}] {message}"}, timeout=5.0)
    except Exception:  # noqa: BLE001
        pass


def _post_blocks(payload: dict) -> bool:
    """Postar Slack blocks-payload. Returnerar True vid 2xx."""
    if not settings.alert_webhook:
        return False
    try:
        r = httpx.post(settings.alert_webhook, json=payload, timeout=8.0)
        return 200 <= r.status_code < 300
    except Exception:  # noqa: BLE001
        return False


def _today_stockholm() -> str:
    """YYYY-MM-DD i Europe/Stockholm — samma som DailyDelivery.date."""
    # zoneinfo finns i 3.9+ men kan saknas på vissa system; importera lokalt
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(settings.tz)).date().isoformat()
    except Exception:
        # Fallback: UTC+2 sommartid, UTC+1 vintertid är ointressant — använd lokal tid
        return datetime.now().date().isoformat()


def gather_daily_stats(db: Session) -> dict:
    """Plocka ut siffrorna som Slack-notisen visar.

    Pool = totalt antal kandidater i DB som inte är filtered_out (aktiv pool).
    Levererade idag = antal candidates i dagens DailyDelivery (idempotent: 0 om scout inte kört).
    """
    today = _today_stockholm()

    pool_size = db.scalar(select(func.count(Candidate.id)).where(Candidate.filtered_out.is_(False)))
    delivered_today_row = db.scalar(select(DailyDelivery).where(DailyDelivery.date == today))
    delivered_today = delivered_today_row.candidate_count if delivered_today_row else 0
    quality_floor = delivered_today_row.quality_floor if delivered_today_row else settings.quality_floor

    # Senaste scout-körningen (oavsett om den hände idag)
    last_scout = db.scalar(
        select(RunLog).where(RunLog.kind == "scout").order_by(RunLog.started_at.desc()).limit(1)
    )
    last_scout_at = last_scout.started_at.astimezone(timezone.utc) if last_scout else None
    last_scout_status = last_scout.status if last_scout else "—"
    last_scout_stats = last_scout.stats if last_scout and last_scout.stats else {}

    # 7-dagars-snitt på leveranser (för kontext)
    week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    week_totals = db.scalars(
        select(DailyDelivery.candidate_count).where(DailyDelivery.date >= week_ago)
    ).all()
    avg_week = round(sum(week_totals) / len(week_totals), 1) if week_totals else 0.0

    return {
        "today": today,
        "pool_size": int(pool_size or 0),
        "delivered_today": int(delivered_today),
        "quality_floor": float(quality_floor),
        "avg_week": avg_week,
        "last_scout_at": last_scout_at,
        "last_scout_status": last_scout_status,
        "last_scout_stats": last_scout_stats,
    }


def daily_status_to_slack() -> bool:
    """Bygger och postar dagens status till Slack. Returnerar True om något skickades."""
    with SessionLocal() as db:
        s = gather_daily_stats(db)

    today = s["today"]
    pool = s["pool_size"]
    delivered = s["delivered_today"]
    floor = s["quality_floor"]
    avg = s["avg_week"]

    # Status-emoji baserat på dagens leverans
    if delivered == 0:
        emoji = ":warning:"
        headline = f"Inga kandidater levererade idag ({today})"
    elif delivered < 5:
        emoji = ":small_orange_diamond:"
        headline = f"{delivered} kandidater levererade idag"
    else:
        emoji = ":white_check_mark:"
        headline = f"{delivered} kandidater levererade idag"

    # Senaste scout-info
    last_at = s["last_scout_at"]
    if last_at:
        try:
            from zoneinfo import ZoneInfo
            last_local = last_at.astimezone(ZoneInfo(settings.tz))
            last_str = last_local.strftime("%Y-%m-%d %H:%M")
        except Exception:
            last_str = last_at.strftime("%Y-%m-%d %H:%M UTC")
    else:
        last_str = "—"

    last_stats = s["last_scout_stats"] or {}
    queries = last_stats.get("queries", "—")
    built = last_stats.get("built", "—")
    filtered = last_stats.get("filtered_out", "—")

    body = (
        f"```\n"
        f"Pool (aktiv)         {pool:>5}\n"
        f"Levererade idag      {delivered:>5}  (golv: {floor:.0f}, snitt 7d: {avg})\n"
        f"Senaste scout-körn.  {last_str}  [{s['last_scout_status']}]\n"
        f"  queries={queries}  byggda={built}  filtrerade={filtered}\n"
        f"```"
    )

    payload = {
        "text": f"{emoji} *WLA Scout dagsstatus* — {headline}\n{body}",
    }
    return _post_blocks(payload)
