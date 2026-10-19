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
from .services.winner_profile import build_profile_from_public, get_active_profile
from .services.alerts import alert, daily_status_to_slack

log = logging.getLogger("scheduler")


def _scout_job():
    with SessionLocal() as db:
        try:
            # Säkerställ att vinnarprofilen finns innan scout körs.
            # Utan profil → bara 18 hårdkodade base-queries → liten pool.
            if get_active_profile(db) is None:
                try:
                    build_profile_from_public(db)
                    log.info("winner profile built (was missing)")
                except Exception as exc:  # noqa: BLE001
                    log.warning("winner profile build failed: %s", exc)
            res = run_scout(db)
            log.info("scout ok: %s", res)
            _check_low_yield(db)
        except Exception as exc:  # noqa: BLE001
            alert(f"Scouting-pipeline failed: {exc}", "error")
            log.exception("scout failed")


def _profile_job():
    """Rebygg vinnarprofilen veckovis så nya hits från WLA-kanalen syns."""
    with SessionLocal() as db:
        try:
            build_profile_from_public(db)
            log.info("winner profile rebuilt")
        except Exception as exc:  # noqa: BLE001
            log.warning("profile rebuild failed: %s", exc)


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


def _daily_status_job():
    """Skickar dagsstatus till Slack — pool size + antal kandidater + senaste scout-info."""
    try:
        ok = daily_status_to_slack()
        if not ok:
            log.warning("daily-status: kunde inte skicka (saknad webhook eller HTTP-fel)")
    except Exception as exc:  # noqa: BLE001
        log.exception("daily-status failed: %s", exc)


def _scout_sync_job():
    """Hämta nya storys från Claude-scoutens datarepo och skriv tillbaka beslut."""
    from .services.scout_sync import run_sync, is_configured
    if not is_configured():
        return
    with SessionLocal() as db:
        try:
            res = run_sync(db)
            if res.get("items_new") or res.get("decisions_exported"):
                log.info("scout-sync: %s", res)
        except Exception as exc:  # noqa: BLE001
            log.exception("scout-sync failed: %s", exc)


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
    # Vinnarprofil rebuild varje söndag kl 03:00
    sched.add_job(_profile_job, CronTrigger.from_crontab("0 3 * * 0", timezone=settings.tz), id="profile", replace_existing=True)
    # Dagsstatus till Slack kl 06:30 — efter scout-jobbet 06:00 hunnit klart
    sched.add_job(_daily_status_job, CronTrigger.from_crontab(settings.schedule_cron_daily_status, timezone=settings.tz), id="daily_status", replace_existing=True)
    # Claude-scoutens Storys: synka var 15:e minut (avstängt om inget datarepo är konfigurerat)
    sched.add_job(_scout_sync_job, CronTrigger.from_crontab(settings.schedule_cron_scout_sync, timezone=settings.tz), id="scout_sync", replace_existing=True)
    sched.start()
    log.info("Scheduler aktiv: scout=%s maturity=%s sheet=%s daily_status=%s tz=%s",
             settings.schedule_cron_scout, settings.schedule_cron_maturity,
             settings.schedule_cron_sheet_sync, settings.schedule_cron_daily_status, settings.tz)
    return sched
