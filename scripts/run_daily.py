#!/usr/bin/env python3
"""
scripts/run_daily.py — Production daily pipeline runner.

This is the script that launchd / cron calls every day.
It runs the full pipeline: Topic → Script → Storyboard → Voice → Video → Upload.

Usage:
    python scripts/run_daily.py                   # full pipeline with upload
    python scripts/run_daily.py --dry-run         # skip YouTube upload
    python scripts/run_daily.py --topic "..."     # inject specific topic
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from rich.console import Console
from rich.panel import Panel

console = Console()
app = typer.Typer()


async def run(dry_run: bool, topic: str | None) -> bool:
    from app.config import settings
    from app.database import Base, SessionLocal, Job, JobStatus, Topic as TopicModel, ContentPillar, TopicStatus
    from sqlalchemy.ext.asyncio import create_async_engine

    console.rule("[bold blue]🤖 Faceless AI Channel — Daily Run[/bold blue]")
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
        from app.scheduler.daily_pipeline import create_and_run_daily_job
        success = await create_and_run_daily_job(db)

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
):
    ok = asyncio.run(run(dry_run=dry_run, topic=topic))
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    app()
