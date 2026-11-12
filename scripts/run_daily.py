#!/usr/bin/env python3
"""
scripts/run_daily.py — Production daily pipeline runner.

This is the script that launchd / cron calls every day.
It runs the full pipeline: Topic → Script → Storyboard → Voice → Video → Upload.

Usage:
    python scripts/run_daily.py                   # full pipeline with upload
    python scripts/run_daily.py --dry-run         # skip YouTube upload
    python scripts/run_daily.py --topic "..."     # inject specific topic
    python scripts/run_daily.py --resume          # continue a stuck/failed run

launchd runs this at several evening slots (RUN_SLOTS). The first creates the
day's job; later slots resume it if it failed, or exit if it already uploaded.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()
app = typer.Typer()

# Must match the StartCalendarInterval slots in com.faceless.daily-pipeline.plist
RUN_SLOTS = ["18:30", "20:00", "21:30"]


def _wait_for_network(timeout_s: int = 300) -> bool:
    """launchd fires right after the Mac wakes, often before Wi-Fi reconnects."""
    import socket
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            socket.getaddrinfo("generativelanguage.googleapis.com", 443)
            return True
        except OSError:
            time.sleep(10)
    return False


def _job_local_date(job_id: str, tz: str):
    """Job ids embed their UTC start time: daily_YYYYMMDD_HHMMSS_xxxxxx."""
    stamp = datetime.strptime(job_id.split("_")[1] + job_id.split("_")[2], "%Y%m%d%H%M%S")
    return stamp.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(tz)).date()


def _notify(title: str, message: str) -> None:
    """macOS notification so the owner sees the outcome without opening logs."""
    # Text goes in as argv so quotes in error messages can't break the AppleScript
    subprocess.run(
        ["osascript",
         "-e", "on run argv",
         "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
         "-e", "end run",
         title, message[:220]],
        check=False, capture_output=True,
    )


async def _report(db, job, success: bool) -> None:
    """Notify + append one line to logs/daily_summary.log for every run."""
    from sqlalchemy import select
    from app.config import settings
    from app.database import Publication, Video

    url = None
    if success:
        url = (await db.execute(
            select(Publication.platform_url)
            .join(Video, Video.id == Publication.video_id)
            .where(Video.job_id == job.job_id, Publication.platform == "youtube",
                   Publication.platform_url.is_not(None))
            .order_by(Publication.id.desc()).limit(1)
        )).scalar_one_or_none()

    now = datetime.now(ZoneInfo(settings.timezone))
    later = [s for s in RUN_SLOTS if s > now.strftime("%H:%M")]
    if success and job.error_message:
        title, msg = "⚠️ Short uploaded — needs your check", f"{url} — {job.error_message}"
    elif success:
        title, msg = "✅ Today's Short uploaded (private)", f"Approve in YouTube Studio: {url}"
    else:
        retry = f"Auto-retry at {later[0]}." if later else "No more retries today."
        title, msg = "❌ Daily Short failed", f"Stage {job.stage}: {job.error_message} — {retry}"

    _notify(title, msg)
    summary = settings.log_file.parent / "daily_summary.log"
    summary.parent.mkdir(parents=True, exist_ok=True)
    with summary.open("a", encoding="utf-8") as f:
        f.write(f"{now:%Y-%m-%d %H:%M} | {job.job_id} | {title} | {msg}\n")


async def run(dry_run: bool, topic: str | None, resume: bool = False) -> bool:
    from app.config import settings
    from app.database import Base, SessionLocal, Job, JobStatus, Topic as TopicModel, ContentPillar, TopicStatus
    from sqlalchemy.ext.asyncio import create_async_engine

    console.rule("[bold blue]🤖 Faceless AI Channel — Daily Run[/bold blue]")

    if not _wait_for_network():
        later = [s for s in RUN_SLOTS if s > datetime.now(ZoneInfo(settings.timezone)).strftime("%H:%M")]
        _notify("❌ Daily Short didn't start", "No internet connection after 5 minutes. "
                + (f"Auto-retry at {later[0]}." if later else "No more retries today."))
        return False
    console.print(f"Mode: {'[yellow]DRY RUN (no upload)[/yellow]' if dry_run else '[green]PRODUCTION (will upload)[/green]'}\n")

    # Init DB
    db_url = str(settings.database_url).replace("sqlite:///", "sqlite+aiosqlite:///")
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Inject topic if provided
    if topic:
        async with SessionLocal() as db:
            t = TopicModel(
                title=topic,
                pillar=ContentPillar.PSYCHOLOGY,
                reliability_score=0.9,
                trend_score=0.9,
                total_score=0.9,
                source_urls=["https://manual.local"],
                status=TopicStatus.SELECTED,
            )
            db.add(t)
            await db.commit()
            console.print(f"[dim]Injected topic: {topic}[/dim]")

    # Override production mode if dry run
    if dry_run:
        settings.youtube_production_mode = False
        settings.instagram_production_mode = False

    start = time.time()
    async with SessionLocal() as db:
        from sqlalchemy import select
        from app.scheduler.daily_pipeline import DailyPipeline, create_and_run_daily_job

        latest = (await db.execute(
            select(Job).where(Job.job_type == "daily_pipeline").order_by(Job.id.desc()).limit(1)
        )).scalar_one_or_none()
        today = datetime.now(ZoneInfo(settings.timezone)).date()
        is_today = latest is not None and _job_local_date(latest.job_id, settings.timezone) == today

        if is_today and latest.status == JobStatus.COMPLETED and not resume:
            # launchd fires several times an evening as automatic retries;
            # once today's video is out, later slots do nothing
            console.print("[green]Today's video is already done — nothing to do.[/green]")
            return True

        if resume and latest is None:
            console.print("[red]No job to resume[/red]")
            return False

        if resume or (is_today and latest.status != JobStatus.COMPLETED):
            # Continue today's unfinished job from its last saved stage (same topic/script)
            console.print(f"[dim]Resuming {latest.job_id} from stage {latest.stage}[/dim]")
            job = latest
            success = await DailyPipeline(db, job).run()
        else:
            # A process killed mid-run leaves its job RUNNING, which would block
            # every future run — close out stale jobs from earlier days first
            stale = (await db.execute(
                select(Job).where(
                    Job.job_type == "daily_pipeline",
                    Job.status.in_([JobStatus.RUNNING, JobStatus.PENDING]),
                )
            )).scalars().all()
            for s in stale:
                s.status = JobStatus.FAILED
                s.error_message = (s.error_message or "") + " [abandoned: superseded by next day's run]"
            await db.commit()
            success = await create_and_run_daily_job(db)
            job = (await db.execute(
                select(Job).where(Job.job_type == "daily_pipeline").order_by(Job.id.desc()).limit(1)
            )).scalar_one()

        await _report(db, job, success)

    elapsed = time.time() - start
    if success:
        console.print(Panel(
            f"[bold green]✅ Pipeline completed in {elapsed:.0f}s[/bold green]\n"
            + ("Video uploaded to YouTube! ✈️" if not dry_run else "Dry run — no upload."),
            border_style="green",
        ))
    else:
        console.print(Panel(
            "[bold red]❌ Pipeline failed — check logs/app.log[/bold red]",
            border_style="red",
        ))

    return success


@app.command()
def main(
    dry_run: bool = typer.Option(False, "--dry-run", help="Skip YouTube/Instagram upload"),
    topic: str = typer.Option(None, "--topic", "-t", help="Inject a specific topic title"),
    resume: bool = typer.Option(False, "--resume", help="Resume the latest unfinished job"),
):
    ok = asyncio.run(run(dry_run=dry_run, topic=topic, resume=resume))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    app()
