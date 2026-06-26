"""CLI för manuella körningar och cron-jobb. `python -m src.cli <cmd>`."""
from __future__ import annotations
import json
import sys
from .db import engine, Base, SessionLocal
from . import models  # noqa
from .services.scout import run_scout
from .services.maturity import run_maturity
from .services.sheet_sync import run_sheet_sync
from .services.outcomes import run_outcomes
from .services.google_oauth import interactive_login
from .services.winner_profile import build_profile_from_analytics
from .services.alerts import daily_status_to_slack


def init_db():
    Base.metadata.create_all(engine)
    print("✓ DB initierad")


def scout():
    with SessionLocal() as db:
        print(json.dumps(run_scout(db), indent=2, default=str))


def polla_mognad():
    with SessionLocal() as db:
        print(json.dumps(run_maturity(db), indent=2, default=str))


def sync_sheet():
    with SessionLocal() as db:
        print(json.dumps(run_sheet_sync(db), indent=2, default=str))


def outcomes():
    with SessionLocal() as db:
        print(json.dumps(run_outcomes(db), indent=2, default=str))


def winner_profile():
    with SessionLocal() as db:
        p = build_profile_from_analytics(db)
        if p:
            print(json.dumps({
                "id": p.id, "based_on_videos": p.based_on_videos,
                "typical_duration_sec": p.typical_duration_sec,
                "top_keywords": p.keywords[:15],
            }, indent=2, default=str))
        else:
            print("Ingen profil byggd (analytics tom eller WLA_CHANNEL_ID saknas)")


def dry_run():
    import os
    os.environ["DRY_RUN"] = "1"
    print("dry-run: scoutar utan att skriva eller anropa LLM (kommer i nästa iteration)")
    with SessionLocal() as db:
        print(json.dumps(run_scout(db), indent=2, default=str))


def oauth_init():
    creds = interactive_login()
    print(f"✓ Token sparad. Refresh token: {'finns' if creds.refresh_token else 'saknas'}")


def daily_status():
    ok = daily_status_to_slack()
    print("✓ skickad till Slack" if ok else "⚠ kunde inte skicka (saknad webhook eller HTTP-fel)")


COMMANDS = {
    "init-db": init_db,
    "scout": scout,
    "polla-mognad": polla_mognad,
    "sync-sheet": sync_sheet,
    "outcomes": outcomes,
    "winner-profile": winner_profile,
    "dry-run": dry_run,
    "oauth-init": oauth_init,
    "daily-status": daily_status,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m src.cli <{ '|'.join(COMMANDS) }>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
