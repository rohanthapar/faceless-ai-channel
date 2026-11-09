"""
app/scheduler/job_manager.py — APScheduler setup for daily pipeline execution.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.database import SessionLocal
from app.logger import get_logger
from app.scheduler.daily_pipeline import create_and_run_daily_job

logger = get_logger("scheduler.job_manager")

_scheduler: AsyncIOScheduler | None = None


async def _run_daily_pipeline_job():
    """APScheduler job callback."""
    logger.info("⏰ Scheduled daily pipeline triggered")
    async with SessionLocal() as db:
        try:
            await create_and_run_daily_job(db)
        except Exception as exc:
            logger.exception("Scheduled pipeline failed: %s", exc)


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone=settings.timezone)
    return _scheduler


def start_scheduler() -> None:
    """Start the APScheduler with the configured daily pipeline trigger."""
    scheduler = get_scheduler()

    if scheduler.running:
        logger.warning("Scheduler already running")
        return

    # Parse the configured time (HH:MM)
    try:
        hour, minute = settings.daily_pipeline_time.split(":")
    except (ValueError, AttributeError):
        hour, minute = "9", "0"
        logger.warning("Invalid DAILY_PIPELINE_TIME — defaulting to 09:00")

    scheduler.add_job(
        _run_daily_pipeline_job,
        trigger=CronTrigger(
            hour=int(hour),
            minute=int(minute),
            timezone=settings.timezone,
        ),
        id="daily_pipeline",
        name="Daily Content Pipeline",
        replace_existing=True,
        misfire_grace_time=3600,  # Allow 1 hour of grace time
    )

    scheduler.start()
    logger.info(
        "✅ Scheduler started — daily pipeline at %s (%s)",
        settings.daily_pipeline_time,
        settings.timezone,
    )


def stop_scheduler() -> None:
    """Gracefully stop the scheduler."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped")


def pause_scheduler() -> None:
    """Pause all scheduled jobs."""
    scheduler = get_scheduler()
    scheduler.pause()
    logger.info("Scheduler paused")


def resume_scheduler() -> None:
    """Resume paused scheduler."""
    scheduler = get_scheduler()
    scheduler.resume()
    logger.info("Scheduler resumed")


def get_next_run_time() -> datetime | None:
    """Return the next scheduled pipeline run time."""
    scheduler = get_scheduler()
    if not scheduler.running:
        return None
    job = scheduler.get_job("daily_pipeline")
    if job:
        return job.next_run_time
    return None


async def trigger_pipeline_now() -> bool:
    """Trigger the daily pipeline immediately (for dashboard manual run)."""
    logger.info("Manual pipeline trigger requested")
    async with SessionLocal() as db:
        return await create_and_run_daily_job(db)
