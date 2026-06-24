"""FastAPI entrypoint. Health, login, dashboard-API."""
from __future__ import annotations
from datetime import datetime, timezone
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
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


@app.get("/feed/today")
def feed_today(user: str = Depends(require_auth), db: Session = Depends(get_db)):
    today = datetime.now(timezone.utc).date().isoformat()
    delivery = db.query(models.DailyDelivery).filter_by(date=today).first()
    if not delivery or not delivery.candidate_ids:
        return {"date": today, "candidates": []}
    cands = db.query(models.Candidate).filter(models.Candidate.id.in_(delivery.candidate_ids)).all()
    ordered = sorted(cands, key=lambda c: delivery.candidate_ids.index(c.id))
    return {"date": today, "delivery_id": delivery.id, "quality_floor": delivery.quality_floor,
            "candidates": [_candidate_dto(c) for c in ordered]}


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


@app.post("/admin/trigger-scout")
def trigger_scout(user: str = Depends(require_auth)):
    """Manuell scouting-körning från statuspanelen."""
    from .services.scout import run_scout
    from .db import SessionLocal
    with SessionLocal() as db:
        try:
            res = run_scout(db)
            return {"ok": True, "result": res}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(500, str(exc))


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
