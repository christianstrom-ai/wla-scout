"""FastAPI entrypoint. Health, login, dashboard-API."""
from __future__ import annotations
import csv
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import SESSION_COOKIE, check_password, issue_session_token, require_auth
from .config import settings
from .db import get_db, engine, Base
from . import models  # noqa: F401 — registrera tabeller
from .scheduler import setup_scheduler

# Kill-switch i minne: när False → scout-jobbet skippar LLM-anrop.
KILL_SWITCH = {"llm_enabled": True, "scheduler_enabled": True}

app = FastAPI(title="WLA Scout API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def strip_api_prefix(request, call_next):
    # Frontend kallar /api/* (Vite dev-proxy gör samma rewrite). I prod
    # serverar samma container både frontend och API; rewrite här istället.
    path = request.url.path
    if path.startswith("/api/"):
        request.scope["path"] = path[4:] or "/"
        raw = request.scope.get("raw_path")
        if raw:
            request.scope["raw_path"] = raw[4:] or b"/"
    return await call_next(request)


@app.on_event("startup")
def _ensure_tables():
    # SQLite quick-start. Prod kör Alembic-migrationer.
    if settings.database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    if KILL_SWITCH["scheduler_enabled"]:
        setup_scheduler()


@app.get("/health")
def health(db: Session = Depends(get_db)):
    last_scout = db.execute(
        select(models.RunLog).where(models.RunLog.kind == "scout").order_by(models.RunLog.started_at.desc()).limit(1)
    ).scalar_one_or_none()
    return {
        "status": "ok",
        "now": datetime.now(timezone.utc).isoformat(),
        "last_scout": {
            "started_at": last_scout.started_at.isoformat() if last_scout else None,
            "status": last_scout.status if last_scout else None,
        } if last_scout else None,
        "config": {
            "tz": settings.tz,
            "max_subscribers": settings.max_subscribers,
            "quality_floor": settings.quality_floor,
            "daily_llm_budget_usd": settings.daily_llm_budget_usd,
        },
    }


class LoginPayload(BaseModel):
    password: str


@app.post("/login")
def login(payload: LoginPayload, response: Response):
    if not check_password(payload.password):
        raise HTTPException(401, "Invalid password")
    token = issue_session_token()
    response.set_cookie(
        key=SESSION_COOKIE, value=token,
        httponly=True, secure=True, samesite="lax",
        max_age=60 * 60 * 24 * 7,
    )
    return {"ok": True}


@app.post("/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/me")
def me(user: str = Depends(require_auth)):
    return {"user": user}


def _candidate_dto(c: models.Candidate) -> dict:
    return {
        "id": c.id, "tracking_id": c.tracking_id,
        "video_id": c.video_id, "channel_id": c.channel_id,
        "title": c.title, "description": c.description,
        "thumbnail_url": c.thumbnail_url,
        "published_at": c.published_at.isoformat() if c.published_at else None,
        "duration_sec": c.duration_sec,
        "is_short": c.is_short,
        "channel_title": c.channel_title,
        "channel_subscribers": c.channel_subscribers,
        "views": c.views, "likes": c.likes, "comments": c.comments,
        "scores": c.scores or {},
        "total_score": c.total_score,
        "source_confidence": c.source_confidence,
        "source_flags": c.source_flags,
        "contact": {
            "email": c.contact_email,
            "instagram": c.contact_instagram,
            "other": c.contact_other,
            "found": c.contact_found,
        },
        "suggested_angle": c.suggested_angle,
        "url": f"https://youtube.com/watch?v={c.video_id}",
        "decision": _latest_decision(c),
    }


def _latest_decision(c: models.Candidate) -> dict | None:
    if not c.decisions:
        return None
    latest = max(c.decisions, key=lambda d: d.decided_at)
    return {"verdict": latest.verdict, "decided_at": latest.decided_at.isoformat()}


def _delivery_payload(date: str, delivery: models.DailyDelivery | None, db: Session) -> dict:
    # Aktiv pool = kandidater som inte filtrerats bort, oavsett datum.
    pool_size = db.query(models.Candidate).filter(models.Candidate.filtered_out.is_(False)).count()
    if not delivery or not delivery.candidate_ids:
        return {"date": date, "candidates": [], "quality_floor": settings.quality_floor,
                "pool_size": pool_size}
    cands = db.query(models.Candidate).filter(models.Candidate.id.in_(delivery.candidate_ids)).all()
    ordered = sorted(cands, key=lambda c: delivery.candidate_ids.index(c.id))
    return {"date": date, "delivery_id": delivery.id, "quality_floor": delivery.quality_floor,
            "pool_size": pool_size,
            "candidates": [_candidate_dto(c) for c in ordered]}


@app.get("/feed/today")
def feed_today(user: str = Depends(require_auth), db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc).date().isoformat()
    delivery = db.query(models.DailyDelivery).filter_by(date=today).first()
    return _delivery_payload(today, delivery, db)


@app.get("/feed/by-date/{date}")
def feed_by_date(date: str, user: str = Depends(require_auth), db: Session = Depends(get_db)):
    delivery = db.query(models.DailyDelivery).filter_by(date=date).first()
    return _delivery_payload(date, delivery, db)


@app.get("/feed/dates")
def feed_dates(user: str = Depends(require_auth), db: Session = Depends(get_db)):
    """Lista alla leveranser senaste 30 dagarna med beslutsräknare."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=30)).isoformat()
    deliveries = (
        db.query(models.DailyDelivery)
        .filter(models.DailyDelivery.date >= cutoff)
        .order_by(models.DailyDelivery.date.desc())
        .all()
    )
    out = []
    for d in deliveries:
        cands = db.query(models.Candidate).filter(models.Candidate.id.in_(d.candidate_ids or [])).all()
        counts = {"yes": 0, "no": 0, "snooze": 0, "pending": 0}
        for c in cands:
            if not c.decisions:
                counts["pending"] += 1
                continue
            latest = max(c.decisions, key=lambda x: x.decided_at)
            counts[latest.verdict] = counts.get(latest.verdict, 0) + 1
        out.append({
            "date": d.date,
            "candidate_count": d.candidate_count,
            **counts,
        })
    return {"dates": out}


@app.get("/candidate/{cand_id}")
def candidate_detail(cand_id: str, user: str = Depends(require_auth), db: Session = Depends(get_db)):
    c = db.query(models.Candidate).filter_by(id=cand_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    return _candidate_dto(c)


class DecisionPayload(BaseModel):
    verdict: str  # 'yes' | 'no' | 'snooze'
    note: str | None = None


@app.post("/candidate/{cand_id}/decision")
def candidate_decision(
    cand_id: str, payload: DecisionPayload,
    user: str = Depends(require_auth), db: Session = Depends(get_db),
):
    if payload.verdict not in ("yes", "no", "snooze"):
        raise HTTPException(400, "verdict must be yes/no/snooze")
    c = db.query(models.Candidate).filter_by(id=cand_id).first()
    if not c:
        raise HTTPException(404, "Not found")
    d = models.Decision(candidate_id=cand_id, verdict=payload.verdict,
                        decided_by=user, note=payload.note)
    db.add(d); db.commit()
    return {"ok": True, "decision_id": d.id}


@app.post("/admin/kill-switch")
def kill_switch(payload: dict, user: str = Depends(require_auth)):
    if "llm_enabled" in payload:
        KILL_SWITCH["llm_enabled"] = bool(payload["llm_enabled"])
    if "scheduler_enabled" in payload:
        KILL_SWITCH["scheduler_enabled"] = bool(payload["scheduler_enabled"])
    return {"ok": True, "state": KILL_SWITCH}


@app.get("/admin/kill-switch")
def kill_switch_state(user: str = Depends(require_auth)):
    return KILL_SWITCH


@app.get("/export/decisions.csv")
def export_decisions(
    days: int = 7,
    user: str = Depends(require_auth),
    db: Session = Depends(get_db),
):
    """CSV-export av kandidater + beslut för de senaste `days` dagarna."""
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=max(1, min(days, 90)))).isoformat()
    deliveries = (
        db.query(models.DailyDelivery)
        .filter(models.DailyDelivery.date >= cutoff)
        .order_by(models.DailyDelivery.date.asc())
        .all()
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "date", "rank", "video_id", "url", "channel_title", "subscribers",
        "is_short", "views", "likes", "comments", "total_score",
        "velocity", "engagement", "overperformance", "profile_match",
        "story", "reachability", "originality", "ai_generated",
        "verdict", "decided_at", "note", "title",
    ])
    for d in deliveries:
        ids = d.candidate_ids or []
        cands = db.query(models.Candidate).filter(models.Candidate.id.in_(ids)).all()
        by_id = {c.id: c for c in cands}
        for rank, cid in enumerate(ids, start=1):
            c = by_id.get(cid)
            if not c:
                continue
            decision = max(c.decisions, key=lambda x: x.decided_at) if c.decisions else None
            s = c.scores or {}
            def sub(k): return (s.get(k) or {}).get("norm") if isinstance(s.get(k), dict) else None
            w.writerow([
                d.date, rank, c.video_id, f"https://youtube.com/watch?v={c.video_id}",
                c.channel_title or "", c.channel_subscribers or 0,
                "short" if c.is_short else "long", c.views or 0, c.likes or 0, c.comments or 0,
                c.total_score or 0,
                sub("velocity"), sub("engagement"), sub("overperformance"),
                sub("profile_match"), sub("story"), sub("reachability"), sub("originality"),
                "yes" if s.get("ai_generated") else "no",
                decision.verdict if decision else "",
                decision.decided_at.isoformat() if decision else "",
                (decision.note or "") if decision else "",
                c.title or "",
            ])
    buf.seek(0)
    today = datetime.now(timezone.utc).date().isoformat()
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="wla-decisions-{today}.csv"'},
    )


@app.post("/admin/build-profile")
def trigger_build_profile(user: str = Depends(require_auth)):
    """Bygg vinnarprofilen från publika uploads i WLA-kanalen.
    Utan profil får scout bara 18 base-queries."""
    from .services.winner_profile import build_profile_from_public
    from .db import SessionLocal
    with SessionLocal() as db:
        try:
            profile = build_profile_from_public(db)
            if profile is None:
                return {"ok": False, "reason": "build_returned_none"}
            return {
                "ok": True,
                "profile_id": profile.id,
                "keyword_count": len(profile.keywords or []),
                "top_keywords": [k["ngram"] for k in (profile.keywords or [])[:20]],
            }
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc))


@app.post("/admin/trigger-scout")
def trigger_scout(force: bool = False, user: str = Depends(require_auth)):
    """Manuell scouting-körning. force=true raderar dagens delivery först
    (för att testa/dubbelköra) — använd försiktigt."""
    from .services.scout import run_scout
    from .db import SessionLocal
    with SessionLocal() as db:
        if force:
            today = datetime.now(timezone.utc).date().isoformat()
            db.query(models.DailyDelivery).filter_by(date=today).delete()
            db.commit()
        try:
            res = run_scout(db)
            return {"ok": True, "result": res}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc))


@app.post("/admin/send-daily-status")
def admin_send_daily_status(user: str = Depends(require_auth)):
    """Triggar Slack daily-status-notisen manuellt + rapporterar om webhook
    är konfigurerad. Användbart för att verifiera setup på prod."""
    from .services.alerts import daily_status_to_slack
    from .config import settings as cfg
    webhook_set = bool(cfg.alert_webhook)
    ok = daily_status_to_slack() if webhook_set else False
    return {
        "webhook_configured": webhook_set,
        "send_ok": ok,
        "hint": (None if webhook_set else
                 "Sätt ALERT_WEBHOOK i Render Environment-fliken så fyrar 06:30-jobbet."),
    }


@app.post("/admin/import-delivery")
def import_delivery(payload: dict, user: str = Depends(require_auth)):
    """En-gångs-import av en lokalt körd batch till prod. Skapar Candidates
    som saknas (dedup på video_id) och lägger till en DailyDelivery för datumet
    om den inte redan finns. För dagar när scoutkvoten är slut i prod men
    lokalt fick igenom en batch.

    Body:
      { "date": "YYYY-MM-DD", "quality_floor": float,
        "candidates": [ {<Candidate-fält som JSON>}... ],
        "candidate_ids_order": [str, ...] }
    """
    from .db import SessionLocal

    date = payload.get("date")
    quality_floor = float(payload.get("quality_floor", 55.0))
    candidates = payload.get("candidates", [])
    order = payload.get("candidate_ids_order", [])
    if not (date and candidates and order):
        raise HTTPException(400, "date, candidates och candidate_ids_order krävs")

    DT_FIELDS = {"published_at", "first_seen_at", "last_polled_at"}
    inserted_new = 0
    reused_existing = 0
    final_ids: list[str] = []

    with SessionLocal() as db:
        for src in candidates:
            video_id = src.get("video_id")
            if not video_id:
                continue
            existing = db.query(models.Candidate).filter_by(video_id=video_id).first()
            if existing:
                reused_existing += 1
                final_ids.append(existing.id)
                continue
            # Bygg ny Candidate. Parsa ISO-datetimes där det behövs.
            data = {k: v for k, v in src.items() if hasattr(models.Candidate, k)}
            for f in DT_FIELDS:
                if data.get(f):
                    try:
                        data[f] = datetime.fromisoformat(data[f])
                    except Exception:  # noqa: BLE001
                        data.pop(f, None)
            c = models.Candidate(**data)
            db.add(c)
            db.flush()
            inserted_new += 1
            final_ids.append(c.id)

        # Behåll ordningen från payload där det går
        id_set = set(final_ids)
        ordered_existing_ids = [
            (next((i for i in final_ids if i), None) if False else None) for _ in order
        ]
        # Enklare: ta candidates-listans ordning som final ordning
        delivery = db.query(models.DailyDelivery).filter_by(date=date).first()
        if delivery:
            # Uppdatera om dagens leverans var tom
            if not delivery.candidate_ids:
                delivery.candidate_ids = final_ids
                delivery.candidate_count = len(final_ids)
                delivery.quality_floor = quality_floor
                action = "updated_empty"
            else:
                action = "kept_existing"
        else:
            delivery = models.DailyDelivery(
                date=date, candidate_ids=final_ids,
                candidate_count=len(final_ids), quality_floor=quality_floor,
            )
            db.add(delivery)
            action = "created"

        db.commit()
        return {
            "ok": True,
            "date": date,
            "inserted_candidates": inserted_new,
            "reused_existing_candidates": reused_existing,
            "delivery_ids_count": len(final_ids),
            "delivery_action": action,
        }


@app.get("/status")
def status_panel(user: str = Depends(require_auth), db: Session = Depends(get_db)):
    runs = db.execute(
        select(models.RunLog).order_by(models.RunLog.started_at.desc()).limit(20)
    ).scalars().all()
    return {
        "runs": [
            {
                "id": r.id, "kind": r.kind, "status": r.status,
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "stats": r.stats, "error": r.error,
                "youtube_quota_used": r.youtube_quota_used,
                "llm_cost_usd": r.llm_cost_usd,
            }
            for r in runs
        ],
    }


# ---- Frontend static (SPA) ----
# Bundle:n läggs i /app/frontend i Docker-imagen. Lokalt finns ../frontend/dist.
_FRONTEND_DIR = Path(os.environ.get("FRONTEND_DIST", Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"))

if _FRONTEND_DIR.exists():
    app.mount("/assets", StaticFiles(directory=_FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # API-rutter har redan matchat; allt annat → index.html (React Router tar över)
        index = _FRONTEND_DIR / "index.html"
        if index.exists():
            return FileResponse(index)
        raise HTTPException(404, "Frontend not built")
