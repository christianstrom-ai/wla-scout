"""Vinnarprofil från PUBLIK kanaldata (ingen OAuth).

Hämtar WLA:s uploads-playlist, sorterar på viewCount, bygger n-gram-vikter
från top-30 video-titlar/beskrivningar.

Utan Analytics API kan vi inte få faktisk retention/watch time. Vi viktar
istället ngram med views (proxy). Bra nog för v1.
"""
from __future__ import annotations
import re
from collections import Counter
from sqlalchemy.orm import Session

from ..config import settings
from ..models import WinnerProfile
from .youtube import YouTubeClient, parse_iso8601_duration

WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z']{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "you", "are", "this", "that", "but",
    "from", "have", "has", "was", "were", "will", "their", "they", "what",
    "when", "where", "which", "who", "how", "why", "out", "into", "over",
    "more", "your", "our", "wla", "love", "animals", "video", "youtube",
    "subscribe", "channel", "comment", "watch", "follow",
}


def _ngrams(tokens: list[str], n: int) -> list[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _tokenize(text: str) -> list[str]:
    return [t for t in (w.lower() for w in WORD_RE.findall(text or "")) if t not in STOPWORDS]


def build_profile_from_public(db: Session, top_n_videos: int = 30) -> WinnerProfile | None:
    if not settings.wla_channel_id:
        return None

    with YouTubeClient() as yt:
        ch_data = yt._get("channels", {"part": "contentDetails", "id": settings.wla_channel_id}, cost=1)
        items = ch_data.get("items", [])
        if not items:
            return None
        uploads_playlist = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

        video_ids: list[str] = []
        page_token = None
        for _ in range(4):
            params = {"part": "contentDetails", "playlistId": uploads_playlist, "maxResults": 50}
            if page_token:
                params["pageToken"] = page_token
            page = yt._get("playlistItems", params, cost=1)
            for it in page.get("items", []):
                vid = it["contentDetails"].get("videoId")
                if vid:
                    video_ids.append(vid)
            page_token = page.get("nextPageToken")
            if not page_token:
                break

        if not video_ids:
            return None

        details = yt.videos_details(video_ids)

    details.sort(key=lambda v: int(v.get("statistics", {}).get("viewCount", 0) or 0), reverse=True)
    top = details[:top_n_videos]

    counts: Counter[str] = Counter()
    weights: Counter[str] = Counter()
    durations = []

    for item in top:
        sn = item.get("snippet", {})
        st = item.get("statistics", {})
        cd = item.get("contentDetails", {})
        views = int(st.get("viewCount") or 0)
        text = " ".join(filter(None, [sn.get("title"), sn.get("description"), " ".join(sn.get("tags") or [])]))
        toks = _tokenize(text)
        grams = set(toks) | set(_ngrams(toks, 2)) | set(_ngrams(toks, 3))
        for g in grams:
            counts[g] += 1
            weights[g] += float(views)
        dur = parse_iso8601_duration(cd.get("duration"))
        if dur:
            durations.append(dur)

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
        tone_signals={"based_on_top_videos": len(top), "source": "public"},
        based_on_videos=len(top),
    )
    db.add(profile)
    db.commit()
    return profile


build_profile_from_analytics = build_profile_from_public


def get_active_profile(db: Session) -> WinnerProfile | None:
    return db.query(WinnerProfile).order_by(WinnerProfile.created_at.desc()).first()
