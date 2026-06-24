"""YouTube Data API v3 — sök + metadata + kanalstatistik.

Kvotkostnader vi tracker:
  search.list           = 100
  videos.list           = 1
  channels.list         = 1

Strategi: sök billigt och brett, hämta sen metadata i batch (50 ids åt
gången). En typisk dag = ~10 sökningar (1000 enheter) + 10-15 batches
(~15 enheter) = väl under 10k-taket.
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from ..config import settings

API = "https://www.googleapis.com/youtube/v3"


class QuotaError(RuntimeError):
    pass


class YouTubeClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or settings.youtube_api_key
        if not self.api_key:
            raise RuntimeError("YOUTUBE_API_KEY saknas")
        self._quota_used = 0
        self._http = httpx.Client(timeout=20.0)

    @property
    def quota_used(self) -> int:
        return self._quota_used

    def close(self):
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @retry(
        retry=retry_if_exception_type(httpx.HTTPError),
        wait=wait_exponential(min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _get(self, path: str, params: dict, cost: int) -> dict:
        params = {**params, "key": self.api_key}
        r = self._http.get(f"{API}/{path}", params=params)
        if r.status_code == 403 and "quotaExceeded" in r.text:
            raise QuotaError("YouTube daily quota exceeded")
        r.raise_for_status()
        self._quota_used += cost
        return r.json()

    def search_videos(
        self,
        *,
        query: str,
        published_after: datetime,
        max_results: int = 50,
        region_code: str = "US",
        language: str = "en",
    ) -> list[dict]:
        """search.list — kostar 100 enheter per anrop. Returnerar video-stub.
        Inget annat än video_id, channel_id, snippet är säkert här."""
        params = {
            "part": "snippet",
            "type": "video",
            "q": query,
            "publishedAfter": published_after.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "maxResults": min(max_results, 50),
            "regionCode": region_code,
            "relevanceLanguage": language,
            "order": "viewCount",
            "safeSearch": "moderate",
        }
        data = self._get("search", params, cost=100)
        return data.get("items", [])

    def videos_details(self, video_ids: Iterable[str]) -> list[dict]:
        """videos.list — 1 enhet per anrop, upp till 50 ids."""
        ids = list(video_ids)
        out = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            data = self._get(
                "videos",
                {"part": "snippet,statistics,contentDetails,status", "id": ",".join(batch), "maxResults": 50},
                cost=1,
            )
            out.extend(data.get("items", []))
        return out

    def channels_details(self, channel_ids: Iterable[str]) -> list[dict]:
        ids = list({c for c in channel_ids if c})
        out = []
        for i in range(0, len(ids), 50):
            batch = ids[i:i + 50]
            data = self._get(
                "channels",
                {"part": "snippet,statistics,brandingSettings", "id": ",".join(batch), "maxResults": 50},
                cost=1,
            )
            out.extend(data.get("items", []))
        return out


# ----- Helpers -----

ISO8601_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def parse_iso8601_duration(value: str | None) -> int | None:
    if not value:
        return None
    m = ISO8601_DURATION.fullmatch(value)
    if not m:
        return None
    h, mi, s = (int(x) if x else 0 for x in m.groups())
    return h * 3600 + mi * 60 + s


def is_shorts(duration_sec: int | None, video: dict | None = None) -> bool:
    """Heuristik: < 60 s räknas som Short. YouTube exponerar inte en flagga
    direkt i Data API; #shorts-hashtag eller path-redirect är inte säkra
    signaler, så vi kör på längden."""
    if duration_sec is None:
        return False
    return duration_sec <= 60


def looks_english(text: str | None) -> bool:
    """Lättviktig grov-filter. Detekteras säkrare i steg 2 med fast detect-lib
    om vi behöver det. Räknar tecken som matchar latin + vanlig svensk
    diakritik som icke-engelska."""
    if not text:
        return False
    foreign = sum(1 for ch in text if ch in "åäöÅÄÖéèêñçü")
    return foreign / max(1, len(text)) < 0.02
