"""Vinnarprofil: rangordnade n-gram + typisk längd från WLA:s topp-videor.
Körs vid uppstart (om profile saknas) + veckovis."""
from __future__ import annotations
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from googleapiclient.discovery import build
from sqlalchemy.orm import Session

from ..config import settings
from ..models import WinnerProfile
from .google_oauth import require_credentials

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z']{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "you", "are", "this", "that", "but",
    "from", "have", "has", "was", "were", "will", "their", "they", "what",
    "when", "where", "which", "who", "how", "why", "out", "into", "over",
    "more", "your", "our", "wla", "love", "animals", "video", "youtube",
}


def _ngrams(tokens: list[str], n: int) -> list[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _tokenize(text: str) -> list[str]:
    return [t for t in (w.lower() for w in WORD_RE.findall(text or "")) if t not in STOPWORDS]


def build_profile_from_analytics(db: Session, lookback_days: int = 365, top_n_videos: int = 30) -> WinnerProfile | None:
    """Hämtar WLA:s topp-videor från Analytics och bygger vinnarprofil."""
    if not settings.wla_channel_id:
        return None
    creds = require_credentials()
    yt = build("youtube", "v3", credentials=creds)
    yta = build("youtubeAnalytics", "v2", credentials=creds)

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=lookback_days)

    # Top videos per views
    report = yta.reports().query(
        ids=f"channel=={settings.wla_channel_id}",
        startDate=str(start),
        endDate=str(end),
        metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained",
        dimensions="video",
        sort="-views",
        maxResults=top_n_videos,
    ).execute()

    rows = report.get("rows") or []
    if not rows:
        return None
    video_ids = [r[0] for r in rows]

    # Hämta titel/beskrivning/tags via Data API
    out = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]
        det = yt.videos().list(part="snippet,contentDetails", id=",".join(batch)).execute()
        out.extend(det.get("items", []))

    # Vikta n-gram efter views × (avg_view_duration / 60s) som "kvalitet"
    weights_per_video = {}
    for r, vid in zip(rows, video_ids):
        views = r[1] if len(r) > 1 else 0
        avd = r[3] if len(r) > 3 else 60
        weights_per_video[vid] = views * (1 + math.log10(max(1, avd)) - 1)

    counts: Counter[str] = Counter()
    weights: Counter[str] = Counter()
    durations = []
    for item in out:
        vid = item["id"]
        sn = item.get("snippet", {})
        cd = item.get("contentDetails", {})
        text = " ".join(filter(None, [sn.get("title"), sn.get("description"), " ".join(sn.get("tags") or [])]))
        toks = _tokenize(text)
        grams = set(toks) | set(_ngrams(toks, 2)) | set(_ngrams(toks, 3))
        w = float(weights_per_video.get(vid, 0))
        for g in grams:
            counts[g] += 1
            weights[g] += w
        # Längd
        from .youtube import parse_iso8601_duration
        dur = parse_iso8601_duration(cd.get("duration"))
        if dur:
            durations.append(dur)

    # Behåll bara ngram som förekommer i minst 2 videor (filtrera bort one-offs)
    ranked = [
        {"ngram": g, "weight": float(weights[g]), "n": len(g.split())}
        for g in counts
        if counts[g] >= 2
    ]
    ranked.sort(key=lambda x: x["weight"], reverse=True)
    ranked = ranked[:100]

    typical_duration = int(sorted(durations)[len(durations) // 2]) if durations else None

    profile = WinnerProfile(
        keywords=ranked,
        typical_duration_sec=typical_duration,
        tone_signals={"based_on_top_videos": len(out)},
        based_on_videos=len(out),
    )
    db.add(profile)
    db.commit()
    return profile


def get_active_profile(db: Session) -> WinnerProfile | None:
    return db.query(WinnerProfile).order_by(WinnerProfile.created_at.desc()).first()
