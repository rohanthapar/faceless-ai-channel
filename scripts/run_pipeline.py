#!/usr/bin/env python3
"""
scripts/run_pipeline.py
Trigger the daily pipeline from the command line (no server needed).

Usage:
    python scripts/run_pipeline.py
    python scripts/run_pipeline.py --dry-run
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from rich.console import Console

console = Console()
app = typer.Typer()


@app.command()
def main(dry_run: bool = typer.Option(False, "--dry-run", help="Skip publishing steps")):
    """Run the full content pipeline once."""
    import os
    if dry_run:
        os.environ["YOUTUBE_PRODUCTION_MODE"] = "false"
        os.environ["INSTAGRAM_PRODUCTION_MODE"] = "false"
        console.print("[yellow]Dry-run mode: publishing disabled[/yellow]")

    from app.scheduler.daily_pipeline import run_pipeline_once

    job_id = str(uuid.uuid4())
    console.print(f"[bold green]Starting pipeline[/bold green] job_id={job_id[:8]}")

    asyncio.run(run_pipeline_once(job_id))
    console.print("[bold green]✅ Pipeline complete![/bold green]")


if __name__ == "__main__":
    app()
