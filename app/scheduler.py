"""Scheduled monitoring. Run as its own process:  python -m app.scheduler"""
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import Settings, get_settings
from app.logging_config import setup_logging
from app.monitor import run_once

log = logging.getLogger(__name__)


def scheduled_job(job=None):
    """Wrapper that can never raise: a failed run must not affect the next scheduled one."""
    try:
        (job or (lambda: run_once(trigger="scheduled")))()
    except Exception:  # noqa: BLE001
        log.exception("scheduled run raised")


def build_scheduler(settings: Settings | None = None, job=None) -> BlockingScheduler:
    settings = settings or get_settings()
    sched = BlockingScheduler(timezone="UTC")
    sched.add_job(
        lambda: scheduled_job(job), IntervalTrigger(minutes=settings.scrape_interval_minutes), id="scrape",
        max_instances=1, coalesce=True, misfire_grace_time=300,
        next_run_time=datetime.now(timezone.utc) if settings.run_on_start else None,
    )
    return sched


def main():
    settings = get_settings()
    setup_logging(settings.log_level)
    if not settings.scheduler_enabled:
        log.warning("SCHEDULER_ENABLED=false; exiting")
        return
    log.info("scheduler started", extra={"ctx": {"interval_minutes": settings.scrape_interval_minutes}})
    build_scheduler(settings).start()


if __name__ == "__main__":
    main()
