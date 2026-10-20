"""API för Storys-fliken (Claude-scouten). Monteras i main.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .auth import require_auth
from .config import settings
from .db import get_db
from . import models
from .services import scout_sync

router = APIRouter()

REJECT_REASONS = ["AI-genererat", "Fel ämne", "Ingen story", "Dålig kvalitet",
                  "Ej originalkälla", "Kontakt saknas", "Redan publicerat"]
APPROVE_REASONS = ["Stark vinkel", "Bra timing", "Originalkälla", "Visuellt starkt",
                   "Återkommande kreatör", "Kontakt enkel"]
HISTORY_DAYS = 60


def _dto(r: models.ScoutItem) -> dict:
    return {
        "id": r.id, "feed": r.feed or "animals", "feed_date": r.feed_date, "platform": r.platform, "url": r.url,
        "creator": r.creator, "creator_url": r.creator_url, "creator_name": r.creator_name,
        "followers": r.followers, "views": r.views, "outlier": r.outlier,
        "duration_sec": r.duration_sec, "headline": r.headline, "summary": r.summary,
        "why": r.why, "angle": r.angle, "contact": r.contact, "checks": r.checks or {},
        "priority": r.priority, "score": r.score,
        "request_subject": r.request_subject, "request_hook": r.request_hook,
        "request_topic": r.request_topic,
        "thumb_url": f"/api/scout/items/{r.id}/thumb" if r.has_thumb else None,
        "status": r.status, "reasons": r.reasons or [], "decision_note": r.decision_note,
        "team_note": r.team_note,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    }


@router.get("/scout/items")
def scout_items(feed: str = "animals", user: str = Depends(require_auth), db: Session = Depends(get_db)):
    if feed not in ("animals", "human"):
        raise HTTPException(400, "feed must be animals/human")
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=HISTORY_DAYS)).isoformat()
    feed_filter = (models.ScoutItem.feed == feed) if feed != "animals" else (
        (models.ScoutItem.feed == "animals") | (models.ScoutItem.feed.is_(None)))
    rows = (db.query(models.ScoutItem)
            .filter(feed_filter)
            .filter((models.ScoutItem.feed_date >= cutoff) | (models.ScoutItem.status.in_(["yes", "snooze"])))
            .order_by(models.ScoutItem.feed_date.desc(), models.ScoutItem.score.desc())
            .all())
    runs = db.query(models.ScoutRun)
    runs = (runs.filter(models.ScoutRun.feed_date.like(f"{feed}:%")) if feed != "animals"
            else runs.filter(~models.ScoutRun.feed_date.like("%:%")))
    last_run = runs.order_by(models.ScoutRun.feed_date.desc()).first()
    return {
        "feed": feed,
        "items": [_dto(r) for r in rows],
        "reject_reasons": REJECT_REASONS,
        "approve_reasons": APPROVE_REASONS,
        "last_run": ({"feed_date": last_run.feed_date.split(":")[-1],
                      "imported_at": last_run.imported_at.isoformat() if last_run.imported_at else None,
                      **{k: v for k, v in (last_run.run or {}).items() if not k.startswith("_")}}
                     if last_run else None),
        "sync_configured": scout_sync.is_configured(),
    }


class ScoutDecision(BaseModel):
    status: str
    reasons: list[str] | None = None
    note: str | None = None


@router.post("/scout/items/{item_id}/decision")
def scout_decision(item_id: str, payload: ScoutDecision,
                   user: str = Depends(require_auth), db: Session = Depends(get_db)):
    if payload.status not in scout_sync.STATUSES:
        raise HTTPException(400, "status must be ny/yes/no/snooze/contacted")
    r = db.get(models.ScoutItem, item_id)
    if not r:
        raise HTTPException(404, "Not found")
    r.status = payload.status
    if payload.status in ("yes", "no"):
        r.reasons = [x for x in (payload.reasons or []) if isinstance(x, str)][:10]
        r.decision_note = (payload.note or "").strip() or None
    elif payload.status == "ny":
        r.reasons, r.decision_note = None, None
    r.decided_at = datetime.now(timezone.utc) if payload.status != "ny" else None
    db.commit()
    return _dto(r)


class ScoutNote(BaseModel):
    team_note: str | None = None


@router.patch("/scout/items/{item_id}/note")
def scout_note(item_id: str, payload: ScoutNote,
               user: str = Depends(require_auth), db: Session = Depends(get_db)):
    r = db.get(models.ScoutItem, item_id)
    if not r:
        raise HTTPException(404, "Not found")
    r.team_note = (payload.team_note or "").strip()[:4000] or None
    db.commit()
    return _dto(r)


@router.get("/scout/items/{item_id}/thumb")
def scout_thumb(item_id: str, user: str = Depends(require_auth), db: Session = Depends(get_db)):
    t = db.get(models.ScoutThumb, item_id)
    if not t:
        raise HTTPException(404, "Not found")
    return Response(content=t.data, media_type=t.content_type,
                    headers={"Cache-Control": "private, max-age=86400"})


PROOF_SYSTEM = """You proofread outreach messages that a video editor at Newsner (a viral news network) sends to private people who filmed a video (an animal rescue or a human-interest moment), asking for an interview and permission to share the video.

Correct spelling, grammar, punctuation and capitalisation, and smooth out awkward or unnatural phrasing so the message reads like fluent, warm, professional English from a native speaker. If any part is written in Swedish or another language, translate it into natural English.

Rules:
- Keep the meaning, every fact, every name, handle and number, and the overall structure, paragraph breaks and sign-off.
- Keep it friendly and personal. Do not make it longer, add new promises, flattery, emojis or claims, or remove the questions.
- Leave placeholders in square brackets as they are.
- Reply with ONLY the corrected message, nothing before or after it."""


class ProofreadPayload(BaseModel):
    text: str


@router.post("/scout/proofread")
def scout_proofread(payload: ProofreadPayload, user: str = Depends(require_auth),
                    db: Session = Depends(get_db)):
    text = (payload.text or "").strip()
    if not text:
        raise HTTPException(400, "Tom text")
    if len(text) > 6000:
        raise HTTPException(400, "Texten är för lång")
    if not settings.anthropic_api_key:
        raise HTTPException(503, "Språkrättning är inte konfigurerad (ANTHROPIC_API_KEY saknas)")
    import anthropic
    from .services.llm import _cost
    model = settings.anthropic_model_escalate
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        resp = client.messages.create(
            model=model, max_tokens=2000, system=PROOF_SYSTEM,
            messages=[{"role": "user", "content": f"Message:\n\"\"\"\n{text}\n\"\"\""}],
        )
    except Exception:  # noqa: BLE001
        raise HTTPException(502, "Språkrättningen misslyckades, försök igen")
    out = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
    out = out.strip('"').strip()
    if out.startswith('"""'):
        out = out[3:]
    if out.endswith('"""'):
        out = out[:-3]
    out = out.strip()
    # Kostnad loggas som en egen körning så att den räknas in i dagsbudgeten.
    now = datetime.now(timezone.utc)
    db.add(models.RunLog(kind="proofread", status="ok", started_at=now, finished_at=now,
                         llm_calls=1, llm_tokens_in=resp.usage.input_tokens,
                         llm_tokens_out=resp.usage.output_tokens,
                         llm_cost_usd=_cost(resp.usage, model)))
    db.commit()
    return {"text": out or text, "changed": bool(out) and out != text}


@router.post("/admin/scout-sync")
def admin_scout_sync(user: str = Depends(require_auth), db: Session = Depends(get_db)):
    """Synka Storys direkt i stället för att vänta på nästa 15-minutersjobb."""
    try:
        return {"ok": True, "result": scout_sync.run_sync(db)}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Synken misslyckades: {exc}")
