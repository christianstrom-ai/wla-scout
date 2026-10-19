"""Synk mellan appen och datarepot som Claude-scouten skriver till.

Datarepots layout (skrivs av morgonkörningen, läses här):
  feed/YYYY-MM-DD.json   {"date", "run": {...}, "items": [ {...}, ... ]}
  thumbs/<item_id>.jpg   bildruta ur klippet (valfri, pekas ut av item["thumb"])

Skrivs av appen (läses av morgonkörningen):
  decisions.json         {"exported_at", "items": {<id>: {status, reasons, ...}}}

Källan är antingen GitHub (scout_data_repo + scout_github_token) eller,
för lokal utveckling, en vanlig mapp (scout_data_dir).
"""
from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy.orm import Session

from ..config import settings
from ..models import ScoutItem, ScoutRun, ScoutThumb

log = logging.getLogger("scout_sync")

# Fält som morgonkörningen äger. Uppdateras vid omimport; redaktionens
# beslut (status, reasons, anteckningar) rörs aldrig av importen.
CONTENT_FIELDS = [
    "feed_date", "platform", "url", "creator", "creator_url", "creator_name",
    "followers", "views", "outlier", "duration_sec", "headline", "summary",
    "why", "angle", "contact", "checks", "priority", "score",
    "request_subject", "request_hook", "request_topic",
]
INT_FIELDS = {"followers", "views", "duration_sec"}
FLOAT_FIELDS = {"outlier", "score"}
STATUSES = {"ny", "yes", "no", "snooze", "contacted"}
MAX_FEED_FILES = 30  # importera bara de senaste N dagarnas filer


def is_configured() -> bool:
    return bool(settings.scout_data_dir or (settings.scout_data_repo and settings.scout_github_token))


# ---------- Källa: lokal mapp eller GitHub ----------

class _LocalSource:
    def __init__(self, root: str):
        self.root = Path(root)

    def list_dir(self, path: str) -> list[dict]:
        d = self.root / path
        if not d.is_dir():
            return []
        return [{"name": p.name, "path": f"{path}/{p.name}", "sha": str(p.stat().st_mtime_ns)}
                for p in sorted(d.iterdir()) if p.is_file()]

    def read(self, path: str) -> bytes | None:
        p = self.root / path
        return p.read_bytes() if p.is_file() else None

    def write_if_changed(self, path: str, content: bytes, message: str) -> bool:
        p = self.root / path
        if p.is_file() and p.read_bytes() == content:
            return False
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
        return True


class _GitHubSource:
    API = "https://api.github.com"

    def __init__(self, repo: str, branch: str, token: str):
        self.repo, self.branch = repo, branch
        self.client = httpx.Client(timeout=30, headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "wla-scout",
        })

    def _url(self, path: str) -> str:
        return f"{self.API}/repos/{self.repo}/contents/{path}"

    def list_dir(self, path: str) -> list[dict]:
        r = self.client.get(self._url(path), params={"ref": self.branch})
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return [{"name": f["name"], "path": f["path"], "sha": f["sha"]}
                for f in r.json() if f.get("type") == "file"]

    def read(self, path: str) -> bytes | None:
        r = self.client.get(self._url(path), params={"ref": self.branch},
                            headers={"Accept": "application/vnd.github.raw+json"})
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.content

    def write_if_changed(self, path: str, content: bytes, message: str) -> bool:
        r = self.client.get(self._url(path), params={"ref": self.branch})
        sha = None
        if r.status_code == 200:
            meta = r.json()
            sha = meta.get("sha")
            try:
                if base64.b64decode(meta.get("content", "")) == content:
                    return False
            except Exception:  # noqa: BLE001
                pass
        elif r.status_code != 404:
            r.raise_for_status()
        body = {"message": message, "branch": self.branch,
                "content": base64.b64encode(content).decode()}
        if sha:
            body["sha"] = sha
        w = self.client.put(self._url(path), json=body)
        w.raise_for_status()
        return True


def _source():
    if settings.scout_data_dir:
        return _LocalSource(settings.scout_data_dir)
    return _GitHubSource(settings.scout_data_repo, settings.scout_data_branch, settings.scout_github_token)


# ---------- Import ----------

def _coerce(field: str, value):
    if value is None or value == "":
        return None if field not in {"headline", "summary"} else value
    try:
        if field in INT_FIELDS:
            return int(float(value))
        if field in FLOAT_FIELDS:
            return float(value)
    except (TypeError, ValueError):
        return None
    return value


def import_feed(db: Session, src=None) -> dict:
    src = src or _source()
    files = [f for f in src.list_dir("feed") if f["name"].endswith(".json")]
    files = sorted(files, key=lambda f: f["name"])[-MAX_FEED_FILES:]
    stats = {"files_read": 0, "items_new": 0, "items_updated": 0, "thumbs": 0, "errors": 0}
    for f in files:
        date = f["name"][:-5]
        run_row = db.get(ScoutRun, date)
        if run_row and (run_row.run or {}).get("_sha") == f["sha"]:
            continue  # oförändrad fil
        raw = src.read(f["path"])
        if raw is None:
            continue
        try:
            doc = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("ogiltig JSON i %s", f["path"])
            stats["errors"] += 1
            continue
        stats["files_read"] += 1
        for it in doc.get("items", []):
            item_id = str(it.get("id") or "").strip()
            if not item_id or not it.get("url"):
                stats["errors"] += 1
                continue
            row = db.get(ScoutItem, item_id)
            is_new = row is None
            if is_new:
                row = ScoutItem(id=item_id, status="ny")
                db.add(row)
            for field in CONTENT_FIELDS:
                if field in it:
                    setattr(row, field, _coerce(field, it[field]))
            if not row.feed_date:
                row.feed_date = date
            if not row.platform:
                row.platform = "youtube"
            stats["items_new" if is_new else "items_updated"] += 1
            thumb_path = it.get("thumb")
            if thumb_path and not row.has_thumb:
                try:
                    data = src.read(thumb_path)
                except Exception as exc:  # noqa: BLE001
                    log.warning("kunde inte hämta bild %s: %s", thumb_path, exc)
                    data = None
                if data:
                    db.flush()
                    ctype = "image/png" if thumb_path.lower().endswith(".png") else "image/jpeg"
                    db.merge(ScoutThumb(item_id=item_id, content_type=ctype, data=data))
                    row.has_thumb = True
                    stats["thumbs"] += 1
        run = dict(doc.get("run") or {})
        run["_sha"] = f["sha"]
        if run_row:
            run_row.run = run
            run_row.imported_at = datetime.now(timezone.utc)
        else:
            db.add(ScoutRun(feed_date=date, run=run))
        db.commit()
    return stats


# ---------- Export av beslut ----------

def decisions_payload(db: Session) -> bytes:
    rows = db.query(ScoutItem).filter(
        (ScoutItem.status != "ny") | (ScoutItem.team_note.isnot(None))
    ).order_by(ScoutItem.id).all()
    items = {}
    for r in rows:
        items[r.id] = {
            "status": r.status,
            "reasons": r.reasons or [],
            "decision_note": r.decision_note,
            "team_note": r.team_note,
            "decided_at": r.decided_at.isoformat() if r.decided_at else None,
            "creator": r.creator,
            "creator_url": r.creator_url,
            "platform": r.platform,
            "feed_date": r.feed_date,
            "headline": r.headline,
        }
    # Ingen tidsstämpel i innehållet: då blir filen identisk när inget ändrats
    # och vi slipper tomma commits.
    return (json.dumps({"items": items}, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode()


def export_decisions(db: Session, src=None) -> bool:
    src = src or _source()
    return src.write_if_changed("decisions.json", decisions_payload(db),
                                "WLA Scout: uppdatera redaktionens beslut")


def run_sync(db: Session) -> dict:
    if not is_configured():
        return {"skipped": "not_configured"}
    src = _source()
    stats = import_feed(db, src)
    stats["decisions_exported"] = export_decisions(db, src)
    return stats
