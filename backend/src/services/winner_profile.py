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
    # Vanliga eng-stopord
    "the", "and", "for", "with", "you", "are", "this", "that", "but",
    "from", "have", "has", "was", "were", "will", "their", "they", "what",
    "when", "where", "which", "who", "how", "why", "out", "into", "over",
    "more", "your", "our", "all", "also", "just", "than", "then", "them",
    # WLA-brand boilerplate
    "wla", "welovewla", "weloveanimals", "weloveanimalssubscribe",
    "newsner", "digital", "platform", "exclusive", "emojis", "badges",
    "member", "members", "become",
    # Generella CTA/sociala-medier-ord
    "subscribe", "follow", "instagram", "facebook", "tiktok", "twitter",
    "youtube", "channel", "comment", "watch", "share", "like", "join",
    # URL-fragment
    "https", "http", "www", "com", "org", "net", "bit", "url",
    "click", "link", "links",
    # Generiska brus-ord som inte hjälper queries
    "amazing", "incredible", "best", "top", "viral", "trending",
    "moments", "compilation", "every", "always", "never",
    "story", "stories", "video", "videos", "news", "show",
    # Vår egen brand-ord
    "love", "animals", "animal",
}

# Boilerplate-marker: text efter dessa är CTA/länk-skräp som drar
# n-gram-frekvensen helt åt fel håll.
BOILERPLATE_MARKERS = re.compile(
    r"(?i)(\bsubscribe\b|become\s+a?\s*member|follow\s+us|"
    r"check\s+out\s+(our|the)|join\s+(our|the)|"
    r"exclusive\s+(content|emojis|videos|access)|"
    r"https?://|www\.|bit\.ly|patreon|instagram\.com|"
    r"facebook\.com|tiktok\.com|twitter\.com|t\.co/|"
    r"#wla\b|@wla|#weloveanimals)"
)


def _strip_boilerplate(text: str) -> str:
    """Klipp av allt efter första boilerplate-markören."""
    if not text:
        return ""
    m = BOILERPLATE_MARKERS.search(text)
    if m:
        text = text[: m.start()]
    return text.strip()


def _ngrams(tokens: list[str], n: int) -> list[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def _tokenize(text: str) -> list[str]:
    return [
        t for t in (w.lower() for w in WORD_RE.findall(text or ""))
        if t not in STOPWORDS and len(t) >= 3
    ]


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
        title = sn.get("title", "") or ""
        # Strippa boilerplate, ta första 400 tecknen av rensad beskrivning
        description = _strip_boilerplate(sn.get("description", ""))[:400]
        # Vikt titlar 2x: tags och senare delen av beskrivningen är skräp,
        # titlarna fångar det redaktionellt viktiga.
        text = f"{title} {title} {description}"
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
