"""Hämta WLA-utfall via YT Analytics och jämför med agentens score.
Klassa varje rad som 'hit', 'miss' eller 'expected'.
Justera score-vikter i små steg när vi har ≥MIN_FEEDBACK_DATAPOINTS."""
from __future__ import annotations
import statistics
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from ..config import settings
from ..models import (
    Candidate, PublishedOutcome, RunLog,
    ScoreWeightsVersion,
)
from .google_oauth import require_credentials


def run_outcomes(db: Session) -> dict:
    run = RunLog(kind="outcomes", status="running", stats={})
    db.add(run); db.commit(); db.refresh(run)
    try:
        result = _do_outcomes(db, run)
        run.status = "ok"; run.finished_at = datetime.now(timezone.utc)
        run.stats = result; db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"; run.error = str(exc)[:1000]
        run.finished_at = datetime.now(timezone.utc); db.commit()
        raise


def _do_outcomes(db: Session, run: RunLog) -> dict:
    if not settings.wla_channel_id:
        return {"skipped": "no wla_channel_id"}
    creds = require_credentials()
    yta = build("youtubeAnalytics", "v2", credentials=creds)

    now = datetime.now(timezone.utc).date()
    rows = db.query(PublishedOutcome).filter(PublishedOutcome.views == 0).all()
    if not rows:
        return {"updated": 0}

    updated = 0
    for o in rows:
        end_date = o.published_at.date() + timedelta(days=o.measurement_window_days)
        if end_date > now:
            continue  # mätperiod inte slut
        try:
            report = yta.reports().query(
                ids=f"channel=={settings.wla_channel_id}",
                startDate=str(o.published_at.date()),
                endDate=str(end_date),
                metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,likes,comments",
                filters=f"video=={o.wla_video_id}",
            ).execute()
        except Exception as exc:  # noqa: BLE001
            run.error = (run.error or "") + f"\nAnalytics err {o.wla_video_id}: {exc}"[:300]
            continue

        data = report.get("rows", [])
        if not data:
            continue
        r = data[0]
        o.views = int(r[0] or 0)
        o.estimated_minutes_watched = int(r[1] or 0)
        o.avg_view_duration_sec = float(r[2] or 0)
        o.subscribers_gained = int(r[3] or 0)
        o.likes = int(r[4] or 0)
        o.comments = int(r[5] or 0)
        o.outcome_class = _classify(db, o)
        o.measured_at = datetime.now(timezone.utc)
        updated += 1

    db.commit()
    _maybe_calibrate(db)
    return {"updated": updated}


def _classify(db: Session, o: PublishedOutcome) -> str:
    """Jämför mot kandidatens ursprungliga totalscore. Använd 50:e + 80:e
    percentilen av samtliga publicerade outcomes som hit/miss-gränser."""
    cand = db.query(Candidate).filter_by(id=o.candidate_id).first() if o.candidate_id else None
    if not cand or cand.total_score is None:
        return "expected"
    # Få ett kanalbaseline för views (median av tidigare outcomes i samma fönster)
    others = db.query(PublishedOutcome).filter(
        PublishedOutcome.measurement_window_days == o.measurement_window_days,
        PublishedOutcome.views > 0,
        PublishedOutcome.id != o.id,
    ).all()
    if not others:
        return "expected"
    median_views = statistics.median([x.views for x in others])
    high_score = cand.total_score >= 75
    low_score = cand.total_score < 55
    above_median = o.views > median_views * 1.5
    below_median = o.views < median_views * 0.5
    if high_score and below_median:
        return "miss"
    if low_score and above_median:
        return "miss"
    if high_score and above_median:
        return "hit"
    if low_score and below_median:
        return "hit"
    return "expected"


def _maybe_calibrate(db: Session) -> None:
    """Heuristisk viktjustering: kolla per delscore vilken som korrelerar
    bäst med 'hit'-klassningen. Justera vikten ±max 5% per körning.
    Kräver MIN_FEEDBACK_DATAPOINTS."""
    outcomes = db.query(PublishedOutcome).filter(PublishedOutcome.outcome_class.is_not(None)).all()
    if len(outcomes) < settings.min_feedback_datapoints:
        return
    # Trivial heuristik v1: räkna hits/miss per delscore. Implementeras
    # vidare i nästa iteration när vi har riktiga datapunkter.
    # Just nu: logga snapshot.
    note = f"{len(outcomes)} datapoints — vikter inte ändrade (heuristik v1 placeholder)"
    # Inga vikt-uppdateringar än, men spara version-snapshot för spårning
    for profile_name in ("shorts", "longform"):
        latest = db.query(ScoreWeightsVersion).filter_by(profile=profile_name).order_by(
            ScoreWeightsVersion.created_at.desc()
        ).first()
        if latest is None:
            weights = settings.weights_shorts if profile_name == "shorts" else settings.weights_longform
            db.add(ScoreWeightsVersion(
                profile=profile_name, weights=weights,
                reason="initial snapshot from env",
                based_on_datapoints=len(outcomes),
            ))
    db.commit()
