"""CLI för manuella körningar och cron-jobb. `python -m src.cli scout` etc."""
from __future__ import annotations
import sys
from .db import engine, Base
from . import models  # noqa


def init_db():
    Base.metadata.create_all(engine)
    print("✓ DB initierad")


def scout():
    print("scout — implementeras i nästa byggsteg")


def polla_mognad():
    print("polla-mognad — implementeras")


def sync_sheet():
    print("sync-sheet — implementeras")


def dry_run():
    print("dry-run — implementeras")


def oauth_init():
    print("oauth-init — implementeras")


COMMANDS = {
    "init-db": init_db,
    "scout": scout,
    "polla-mognad": polla_mognad,
    "sync-sheet": sync_sheet,
    "dry-run": dry_run,
    "oauth-init": oauth_init,
}


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m src.cli <{ '|'.join(COMMANDS) }>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()
