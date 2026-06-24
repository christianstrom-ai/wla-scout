"""APScheduler-jobb. Triggas via main.py startup. Cron-uttryck i config."""
from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import SessionLocal
from .models import DailyDelivery
from .services.scout import run_scout
from .services.maturity import run_maturity
from .services.sheet_sync import run_sheet_sync
from .services.alerts import alert

log = logging.getLogger("scheduler")


def _scout_job():
    with SessionLocal() as db:
        try:
            res = run_scout(db)
            log.info("scout ok: %s", res)
            _check_low_yield(db)
        except Exception as exc:  # noqa: BLE001
            alert(f"Scouting-pipeline failed: {exc}", "error")
            log.exception("scout failed")


def _maturity_job():
    with SessionLocal() as db:
        try:
            run_maturity(db)
        except Exception as exc:  # noqa: BLE001
            alert(f"Maturity-poll failed: {exc}", "error")


def _sheet_job():
    with SessionLocal() as db:
        try:
            run_sheet_sync(db)
        except Exception as exc:  # noqa: BLE001
            alert(f"Sheet-sync failed: {exc}", "error")


def _check_low_yield(db):
    """Larma om < 5 kandidater levererats N dagar i rad."""
    from datetime import date
    days = settings.low_yield_alert_days
    today = date.today()
    streak = 0
    for i in range(days):
        d = (today - timedelta(days=i)).isoformat()
        row = db.query(DailyDelivery).filter_by(date=d).first()
        if not row or row.candidate_count < 5:
            streak += 1
        else:
            break
    if streak >= days:
        alert(f"Låg yield: <5 kandidater {streak} dagar i rad. Justera quality_floor eller scoring.", "warn")


def setup_scheduler() -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=settings.tz)
    sched.add_job(_scout_job, CronTrigger.from_crontab(settings.schedule_cron_scout, timezone=settings.tz), id="scout", replace_existing=True)
    sched.add_job(_maturity_job, CronTrigger.from_crontab(settings.schedule_cron_maturity, timezone=settings.tz), id="maturity", replace_existing=True)
    sched.add_job(_sheet_job, CronTrigger.from_crontab(settings.schedule_cron_sheet_sync, timezone=settings.tz), id="sheet_sync", replace_existing=True)
    sched.start()
    log.info("Scheduler aktiv: scout=%s maturity=%s sheet=%s tz=%s",
             settings.schedule_cron_scout, settings.schedule_cron_maturity,
             settings.schedule_cron_sheet_sync, settings.tz)
    return sched
