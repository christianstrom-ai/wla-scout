"""Orkestrering av den dagliga scouting-pipelinen.

Flöde:
  1. Bygg sökord från vinnarprofilen (top-N keywords).
  2. Sök via YouTube Data API (begränsa anrop per kvot).
  3. Hämta video- och kanalmetadata.
  4. Filtrera: språk, prenumerantantal, dedup mot DB (30d).
  5. Räkna matematiska delscorer på hela poolen.
  6. Sortera, ta topp-N → LLM story-score (med budget-cap + cache).
  7. Räkna total + viktprofil per format.
  8. Välj kandidater över quality_floor → daily_delivery.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Candidate, DailyDelivery, RunLog, WinnerProfile
from .youtube import YouTubeClient, parse_iso8601_duration, is_shorts, looks_english
from .scoring import (
    score_velocity, score_engagement, score_overperformance,
    score_profile_match, score_reachability, score_originality,
    combine, pick_weights,
)
from .contact import extract_contact
from .llm import score_story
from .winner_profile import get_active_profile


def _build_queries(profile: WinnerProfile | None) -> list[str]:
    if not profile or not profile.keywords:
        return [
            "rescue dog", "stray cat saved", "animal friendship",
            "wildlife encounter", "pet reunion",
        ]
    seeds = []
    for kw in profile.keywords[:25]:
        if kw.get("n", 1) >= 2:  # föredra fraser
            seeds.append(kw["ngram"])
    return seeds[:15] or [k["ngram"] for k in profile.keywords[:10]]


def run_scout(db: Session) -> dict:
    today = datetime.now(timezone.utc).date().isoformat()
    existing = db.query(DailyDelivery).filter_by(date=today).first()
    if existing:
        return {"date": today, "skipped": "already_delivered", "delivery_id": existing.id, "candidate_count": existing.candidate_count}

    run = RunLog(kind="scout", status="running", stats={})
    db.add(run); db.commit(); db.refresh(run)

    try:
        result = _do_scout(db, run, today)
        run.status = "ok"
        run.finished_at = datetime.now(timezone.utc)
        run.stats = result
        db.commit()
        return result
    except Exception as exc:  # noqa: BLE001
        run.status = "failed"
        run.error = str(exc)[:1000]
        run.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise


def _do_scout(db: Session, run: RunLog, today: str) -> dict:
    profile = get_active_profile(db)
    queries = _build_queries(profile)

    pool: dict[str, dict] = {}                # video_id → stub
    published_after = datetime.now(timezone.utc) - timedelta(days=7)
    quota_target = 1500  # spend up to 1500 enheter på search → 15 sökningar
    sub_titles_for_dedup = _recent_titles(db)

    with YouTubeClient() as yt:
        for q in queries:
            if yt.quota_used >= quota_target:
                break
            try:
                items = yt.search_videos(query=q, published_after=published_after)
            except Exception as exc:  # noqa: BLE001
                run.error = (run.error or "") + f"\nsearch err '{q}': {exc}"[:500]
                continue
            for it in items:
                vid = it["id"].get("videoId")
                if vid and vid not in pool:
                    pool[vid] = it
            if len(pool) >= settings.candidate_pool_size:
                break

        run.youtube_quota_used = yt.quota_used

        if not pool:
            return {"queries": len(queries), "pool": 0, "delivered": 0}

        # Hämta full metadata + kanaler
        details = yt.videos_details(pool.keys())
        channels = {c["id"]: c for c in yt.channels_details({d["snippet"]["channelId"] for d in details})}
        run.youtube_quota_used = yt.quota_used

    weights = settings.weights_longform  # default; per-cand i scoringen
    delivered: list[Candidate] = []
    seen_video_ids = set(_existing_video_ids(db))

    candidates_built = 0
    filtered_out_count = 0

    for det in details:
        vid = det["id"]
        if vid in seen_video_ids:
            continue
        sn = det.get("snippet", {})
        st = det.get("statistics", {})
        cd = det.get("contentDetails", {})
        channel_id = sn.get("channelId")
        ch = channels.get(channel_id, {})
        ch_stats = ch.get("statistics", {})
        ch_sn = ch.get("snippet", {})

        subs = int(ch_stats.get("subscriberCount") or 0)
        if not subs or subs > settings.max_subscribers:
            continue

        title = sn.get("title", "")
        description = sn.get("description", "")
        if not (looks_english(title) and looks_english(description)):
            continue

        duration_sec = parse_iso8601_duration(cd.get("duration"))
        short = is_shorts(duration_sec)
        published_at = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))

        views = int(st.get("viewCount") or 0)
        likes = int(st.get("likeCount") or 0)
        comments = int(st.get("commentCount") or 0)

        contact = extract_contact(ch_sn.get("description"))

        scores = {
            "velocity": score_velocity(views, published_at, short),
            "engagement": score_engagement(views, likes, comments),
            "overperformance": score_overperformance(views, subs),
            "profile_match": score_profile_match(
                f"{title}\n{description}", profile.keywords if profile else []
            ),
            "reachability": score_reachability(contact["found"], bool(contact["email"]), bool(contact["instagram"])),
        }
        orig_score, hard_filter, flags = score_originality(
            title=title, description=description,
            channel_title=ch_sn.get("title"),
            channel_description=ch_sn.get("description"),
            similar_existing_titles=sub_titles_for_dedup,
        )
        scores["originality"] = orig_score

        if hard_filter:
            filtered_out_count += 1
            continue  # logga separat om vi vill granska

        cand = Candidate(
            video_id=vid,
            channel_id=channel_id,
            title=title,
            description=description[:5000] if description else None,
            thumbnail_url=sn.get("thumbnails", {}).get("high", {}).get("url"),
            published_at=published_at,
            duration_sec=duration_sec,
            is_short=short,
            language="en",
            channel_title=ch_sn.get("title"),
            channel_subscribers=subs,
            channel_description=ch_sn.get("description"),
            views=views, likes=likes, comments=comments,
            scores=scores,
            source_confidence="low" if flags.get("similar_title_exists") else "high",
            source_flags=flags,
            contact_email=contact["email"],
            contact_instagram=contact["instagram"],
            contact_found=contact["found"],
        )
        candidates_built += 1
        db.add(cand)

    db.flush()

    # Plocka topp-N från det vi byggde + räkna LLM story-score på den delen
    built = db.query(Candidate).filter(Candidate.video_id.in_({d["id"] for d in details})).all()
    built = [c for c in built if c.scores]
    # Preliminär totalscore (utan story)
    for c in built:
        w = pick_weights(c.is_short)
        c.total_score = combine(c.scores, w)
    built.sort(key=lambda c: c.total_score or 0, reverse=True)

    topn = built[: settings.llm_topn]
    for c in topn:
        result = score_story(db, run, {
            "video_id": c.video_id, "title": c.title,
            "description": c.description, "channel_title": c.channel_title,
            "thumbnail_url": c.thumbnail_url,
        })
        if result:
            c.scores = {**c.scores, "story": {"raw": result["score"], "norm": float(result["score"]), "explain": result["reason"]}}
        else:
            c.scores = {**c.scores, "story": {"raw": None, "norm": 50.0, "explain": "LLM avstängd / budget slut"}}
        c.total_score = combine(c.scores, pick_weights(c.is_short))

    db.flush()

    # Välj ut feed-kandidater (över quality_floor, upp till 10)
    candidates_sorted = sorted(built, key=lambda c: c.total_score or 0, reverse=True)
    selected = [c for c in candidates_sorted if (c.total_score or 0) >= settings.quality_floor][:10]

    delivery = DailyDelivery(
        date=today,
        candidate_ids=[c.id for c in selected],
        run_id=run.id,
        candidate_count=len(selected),
        quality_floor=settings.quality_floor,
    )
    db.add(delivery); db.commit()

    return {
        "queries": len(queries),
        "pool": len(pool),
        "built": candidates_built,
        "filtered_out": filtered_out_count,
        "topn_llm": len(topn),
        "delivered": len(selected),
        "delivery_id": delivery.id,
    }


def _recent_titles(db: Session) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.dedup_window_days)
    rows = db.execute(
        select(Candidate.title).where(Candidate.first_seen_at >= cutoff)
    ).all()
    return [r[0] for r in rows if r[0]]


def _existing_video_ids(db: Session) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.dedup_window_days)
    rows = db.execute(
        select(Candidate.video_id).where(Candidate.first_seen_at >= cutoff)
    ).all()
    return [r[0] for r in rows]
