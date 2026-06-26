"""Mognads-pollning. Pollar kandidater som befinner sig i tidsfönstret
6/24/48 h efter publicering och uppdaterar deras stats + scores. Lägger
till poll-events i Candidate.poll_history."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Candidate, RunLog
from .youtube import YouTubeClient
from .scoring import (
    score_velocity, score_engagement, score_overperformance,
    combine, pick_weights,
)


def run_maturity(db: Session) -> dict:
    run = RunLog(kind="maturity", status="running", stats={})
    db.add(run); db.commit(); db.refresh(run)
    try:
        result = _do_maturity(db, run)
        run.status = "ok"
        run.finished_at = datetime.now(timezone.utc)
        run.stats = result
        db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"; run.error = str(exc)[:1000]
        run.finished_at = datetime.now(timezone.utc); db.commit()
        raise


def _do_maturity(db: Session, run: RunLog) -> dict:
    hours = settings.maturity_poll_hours_list
    if not hours:
        return {"polled": 0}
    cutoff = max(hours) + 12
    since = datetime.now(timezone.utc) - timedelta(hours=cutoff)
    candidates = db.execute(
        select(Candidate)
        .where(Candidate.first_seen_at >= since)
        .where(Candidate.filtered_out.is_(False))
    ).scalars().all()
    if not candidates:
        return {"polled": 0}

    to_poll = []
    now = datetime.now(timezone.utc)
    for c in candidates:
        # SQLite returnerar DateTime(timezone=True) som naive — tvinga UTC så
        # subtraktionen mot tz-aware `now` inte kastar TypeError.
        pub = c.published_at
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_h = (now - pub).total_seconds() / 3600
        target = _pick_target(age_h, c.poll_history or [], hours)
        if target is not None:
            to_poll.append((c, target))
    if not to_poll:
        return {"polled": 0, "candidates_known": len(candidates)}

    polled = 0
    with YouTubeClient() as yt:
        details = {d["id"]: d for d in yt.videos_details([c.video_id for c, _ in to_poll])}
        run.youtube_quota_used = yt.quota_used

    for c, target in to_poll:
        det = details.get(c.video_id)
        if not det:
            continue
        st = det.get("statistics", {})
        c.views = int(st.get("viewCount") or c.views or 0)
        c.likes = int(st.get("likeCount") or c.likes or 0)
        c.comments = int(st.get("commentCount") or c.comments or 0)
        c.last_polled_at = now
        history = list(c.poll_history or [])
        history.append({
            "at": now.isoformat(), "target_hour": target,
            "views": c.views, "likes": c.likes, "comments": c.comments,
        })
        c.poll_history = history
        # Räkna om billiga delscorer som beror på views/likes/comments
        scores = dict(c.scores or {})
        scores["velocity"] = score_velocity(c.views, c.published_at, c.is_short)
        scores["engagement"] = score_engagement(c.views, c.likes, c.comments)
        scores["overperformance"] = score_overperformance(c.views, c.channel_subscribers)
        c.scores = scores
        c.total_score = combine(scores, pick_weights(c.is_short))
        polled += 1
    db.commit()
    return {"polled": polled, "candidates_known": len(candidates)}


def _pick_target(age_h: float, history: list[dict], targets: list[int]) -> int | None:
    """Returnera den targets[i] som age_h precis passerat och som inte redan
    finns i history."""
    polled_targets = {h.get("target_hour") for h in history}
    for t in sorted(targets):
        if age_h >= t and t not in polled_targets:
            return t
    return None
