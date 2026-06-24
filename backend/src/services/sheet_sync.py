"""Läser WLA Publicerade-sheet och försöker matcha kandidater.

Sheet-kolumner (anpassa när det riktiga arket inspekterats):
  - kandidat_tracking_id  (optional — om ifylld, automatisk match)
  - wla_video_url  ELLER  wla_video_id  (en av dem krävs)
  - publiceringsdatum (YYYY-MM-DD)

Resultat: published_outcomes-rader för 7d/30d efter måt-datum.
"""
from __future__ import annotations
import re
from datetime import datetime, timezone
from googleapiclient.discovery import build
from sqlalchemy.orm import Session
from rapidfuzz import fuzz

from ..config import settings
from ..models import Candidate, PublishedOutcome, SheetSync, RunLog
from .google_oauth import require_credentials

YT_URL_RE = re.compile(r"(?:youtu\.be/|v=|/shorts/|/watch\?v=)([A-Za-z0-9_-]{11})")


def _extract_video_id(value: str) -> str | None:
    if not value:
        return None
    if len(value.strip()) == 11 and re.fullmatch(r"[A-Za-z0-9_-]{11}", value.strip()):
        return value.strip()
    m = YT_URL_RE.search(value)
    return m.group(1) if m else None


def run_sheet_sync(db: Session) -> dict:
    run = RunLog(kind="sheet_sync", status="running", stats={})
    db.add(run); db.commit(); db.refresh(run)
    try:
        result = _do_sync(db, run)
        run.status = "ok"; run.finished_at = datetime.now(timezone.utc)
        run.stats = result; db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"; run.error = str(exc)[:1000]
        run.finished_at = datetime.now(timezone.utc); db.commit()
        raise


def _do_sync(db: Session, run: RunLog) -> dict:
    creds = require_credentials()
    svc = build("sheets", "v4", credentials=creds)
    rng = f"{settings.wla_published_sheet_tab}!A1:Z10000"
    resp = svc.spreadsheets().values().get(
        spreadsheetId=settings.wla_published_sheet_id, range=rng
    ).execute()
    rows = resp.get("values", [])
    if not rows:
        db.add(SheetSync(rows_read=0, rows_matched=0, rows_needs_manual=0))
        db.commit()
        return {"rows_read": 0}

    header = [h.lower().strip() for h in rows[0]]
    col_tracking = _find_col(header, ["kandidat_tracking_id", "tracking_id"])
    col_video    = _find_col(header, ["wla_video_id", "video_id", "wla_video_url", "url"])
    col_date     = _find_col(header, ["publiceringsdatum", "publish_date", "datum"])

    matched = 0
    needs_manual = 0
    for r in rows[1:]:
        if col_video is None or col_video >= len(r):
            continue
        video_id = _extract_video_id(r[col_video])
        if not video_id:
            continue
        pub_str = r[col_date] if col_date is not None and col_date < len(r) else None
        try:
            pub_dt = datetime.fromisoformat(pub_str) if pub_str else None
        except ValueError:
            pub_dt = None
        if not pub_dt:
            continue
        pub_dt = pub_dt.replace(tzinfo=timezone.utc) if pub_dt.tzinfo is None else pub_dt

        candidate = None
        if col_tracking is not None and col_tracking < len(r) and r[col_tracking].strip():
            candidate = db.query(Candidate).filter_by(tracking_id=r[col_tracking].strip()).first()

        if not candidate:
            # Fuzzy match mot kandidatlista (titel-similarity)
            candidate = _fuzzy_match(db, video_id, pub_dt)
            if candidate is None:
                needs_manual += 1
                continue

        # Skapa tomma outcome-rader för 7d och 30d om de saknas; fylls
        # senare av outcomes-jobbet när mätperioden gått.
        for window in (7, 30):
            existing = db.query(PublishedOutcome).filter_by(
                wla_video_id=video_id, measurement_window_days=window
            ).first()
            if existing:
                continue
            db.add(PublishedOutcome(
                candidate_id=candidate.id,
                wla_video_id=video_id,
                published_at=pub_dt,
                measurement_window_days=window,
                views=0,
            ))
        matched += 1

    db.add(SheetSync(rows_read=len(rows) - 1, rows_matched=matched, rows_needs_manual=needs_manual))
    db.commit()
    return {"rows_read": len(rows) - 1, "rows_matched": matched, "rows_needs_manual": needs_manual}


def _find_col(header: list[str], candidates: list[str]) -> int | None:
    for c in candidates:
        if c in header:
            return header.index(c)
    return None


def _fuzzy_match(db: Session, video_id: str, pub_dt: datetime) -> Candidate | None:
    """Kandidater inom 30 dagar före publicering, högst likhet."""
    from datetime import timedelta
    since = pub_dt - timedelta(days=30)
    pool = db.query(Candidate).filter(Candidate.first_seen_at >= since).all()
    if not pool:
        return None
    # Enklast: matcha på video_id om kandidaten faktiskt är samma video
    for c in pool:
        if c.video_id == video_id:
            return c
    # Annars titel-similarity behövs senare manuellt — returnera None
    return None
