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


# Placeholder-endpoints — implementeras i kommande byggsteg
@app.get("/feed/today")
def feed_today(user: str = Depends(require_auth)):
    return {"date": datetime.now(timezone.utc).date().isoformat(), "candidates": []}


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
