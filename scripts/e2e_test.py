#!/usr/bin/env python3
"""
scripts/e2e_test.py — End-to-end pipeline smoke test.

Runs the full daily pipeline once and prints a detailed report.
Does NOT upload to YouTube/Instagram (dry-run mode).

Usage:
    python scripts/e2e_test.py
    python scripts/e2e_test.py --topic "10 Psychology Facts That Will Blow Your Mind"
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


async def run_e2e(custom_topic: str | None = None) -> None:
    from app.config import settings
    from app.database import Base, SessionLocal, Job, JobStatus
    from sqlalchemy.ext.asyncio import create_async_engine

    # ── 1. Init DB ──────────────────────────────────────────────────────────
    console.rule("[bold blue]🤖 Faceless AI Channel — End-to-End Test[/bold blue]")
    console.print(f"\n[dim]Output directory: {settings.output_rendered}[/dim]\n")

    db_url = str(settings.database_url).replace("sqlite:///", "sqlite+aiosqlite:///")
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    console.print("[green]✅ Database ready[/green]")

    # ── 2. Research & Topic ─────────────────────────────────────────────────
    start = time.time()
    console.rule("[bold]Stage 1 — Research & Topic Selection[/bold]")

    if custom_topic:
        # Inject a manual topic for testing
        from app.database import Topic, ContentPillar, TopicStatus
        async with SessionLocal() as db:
            topic = Topic(
                title=custom_topic,
                pillar=ContentPillar.PSYCHOLOGY,
                reliability_score=0.9,
                trend_score=0.9,
                total_score=0.9,
                source_urls=["https://test.local"],
                status=TopicStatus.SELECTED,
            )
            db.add(topic)
            await db.commit()
            await db.refresh(topic)
            console.print(f"[green]✅ Injected test topic:[/green] {topic.title}")
            topic_id = topic.id
    else:
        from app.content.topic_selector import select_daily_topic
        async with SessionLocal() as db:
            topic = await select_daily_topic(db)
            if not topic:
                console.print("[red]❌ No topic found — check RSS feeds and internet connection[/red]")
                return
            topic_id = topic.id
            console.print(f"[green]✅ Topic selected:[/green] {topic.title}")
            console.print(f"   Score: {topic.total_score:.2f} | Pillar: {topic.pillar}")

    console.print(f"   [dim]Time: {time.time()-start:.1f}s[/dim]\n")

    # ── 3. Script ───────────────────────────────────────────────────────────
    console.rule("[bold]Stage 2 — Script Writing[/bold]")
    t = time.time()
    from app.database import Topic
    async with SessionLocal() as db:
        from sqlalchemy import select
        topic = (await db.execute(select(Topic).where(Topic.id == topic_id))).scalar_one()
        from app.content.scriptwriter import generate_script
        script = await generate_script(topic)
        if not script:
            console.print("[red]❌ Script generation failed[/red]")
            return
        db.add(script)
        await db.commit()
        await db.refresh(script)
        script_id = script.id

    console.print(f"[green]✅ Script ready[/green] ({script.word_count} words, ~{script.estimated_duration:.0f}s)")
    console.print(Panel(script.script[:400] + ("..." if len(script.script) > 400 else ""),
                        title="Script Preview", border_style="dim"))
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 4. Storyboard ───────────────────────────────────────────────────────
    console.rule("[bold]Stage 3 — Storyboard[/bold]")
    t = time.time()
    from app.database import Script
    async with SessionLocal() as db:
        from sqlalchemy import select
        script = (await db.execute(select(Script).where(Script.id == script_id))).scalar_one()
        from app.content.storyboard import generate_storyboard
        storyboard = await generate_storyboard(script)
        if not storyboard:
            console.print("[red]❌ Storyboard failed[/red]")
            return
        db.add(storyboard)
        await db.commit()
        await db.refresh(storyboard)
        storyboard_id = storyboard.id

    console.print(f"[green]✅ Storyboard:[/green] {storyboard.total_scenes} scenes")
    for i, scene in enumerate(storyboard.scene_data[:3], 1):
        console.print(f"   Scene {i}: {scene.get('visual_query','?')[:60]} [{scene.get('duration','?')}s]")
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 5. Voice ────────────────────────────────────────────────────────────
    console.rule("[bold]Stage 4 — Voice Generation (Edge TTS)[/bold]")
    t = time.time()
    job_dir = settings.output_drafts / "e2e_test"
    job_dir.mkdir(parents=True, exist_ok=True)
    audio_path = job_dir / "narration.mp3"

    from app.database import Script
    async with SessionLocal() as db:
        from sqlalchemy import select
        script = (await db.execute(select(Script).where(Script.id == script_id))).scalar_one()
        from app.media.voice_generator import generate_narration, get_audio_duration
        ok = await generate_narration(script.script, audio_path)
        if not ok:
            console.print("[red]❌ Voice generation failed[/red]")
            return
        actual_duration = await get_audio_duration(audio_path)

    console.print(f"[green]✅ Narration:[/green] {audio_path.name} ({actual_duration:.1f}s, {audio_path.stat().st_size//1024}KB)")
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 6. Visuals ──────────────────────────────────────────────────────────
    console.rule("[bold]Stage 5 — Visual Asset Fetching[/bold]")
    t = time.time()
    asset_dir = job_dir / "assets"
    asset_dir.mkdir(exist_ok=True)

    from app.database import Storyboard
    async with SessionLocal() as db:
        from sqlalchemy import select
        storyboard = (await db.execute(select(Storyboard).where(Storyboard.id == storyboard_id))).scalar_one()
        from app.media.image_generator import fetch_assets_for_scene
        scene_assets: dict[int, dict] = {}
        fetched = 0
        for scene in storyboard.scene_data:
            assets = await fetch_assets_for_scene(scene, asset_dir)
            sn = scene["scene_number"]
            if assets:
                scene_assets[sn] = {"path": str(assets[0].local_path), "type": assets[0].asset_type}
                fetched += 1
                console.print(f"   Scene {sn}: [green]✅[/green] {assets[0].asset_type} — {assets[0].local_path.name}")
            else:
                scene_assets[sn] = {"path": None, "type": "fallback"}
                console.print(f"   Scene {sn}: [yellow]⚠ fallback color[/yellow]")

    console.print(f"[green]✅ Assets:[/green] {fetched}/{len(scene_assets)} fetched")
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 7. Subtitles ────────────────────────────────────────────────────────
    console.rule("[bold]Stage 6a — Subtitles (heuristic)[/bold]")
    t = time.time()
    sub_dir = job_dir / "subtitles"
    async with SessionLocal() as db:
        from sqlalchemy import select
        script = (await db.execute(select(Script).where(Script.id == script_id))).scalar_one()
        from app.media.subtitle_generator import generate_subtitles
        sub_files = await generate_subtitles(audio_path, script.script, actual_duration, sub_dir)

    console.print(f"[green]✅ Subtitles:[/green] {list(sub_files.keys())}")
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 8. Video Compose ────────────────────────────────────────────────────
    console.rule("[bold]Stage 6b — FFmpeg Video Composition[/bold]")
    t = time.time()
    output_path = settings.output_rendered / "e2e_test.mp4"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    asset_paths = {}
    asset_types = {}
    for sn, info in scene_assets.items():
        if info.get("path"):
            asset_paths[sn] = Path(info["path"])
            asset_types[sn] = info.get("type", "image")

    from app.database import Storyboard
    async with SessionLocal() as db:
        from sqlalchemy import select
        storyboard = (await db.execute(select(Storyboard).where(Storyboard.id == storyboard_id))).scalar_one()
        from app.editor.video_composer import compose_video
        ok = await compose_video(
            scenes=storyboard.scene_data,
            asset_paths=asset_paths,
            asset_types=asset_types,
            narration_path=audio_path,
            ass_subtitle_path=sub_files.get("ass", Path("/nonexistent")),
            srt_subtitle_path=sub_files.get("srt"),
            output_path=output_path,
            music_path=None,
        )

    if not ok or not output_path.exists():
        console.print("[red]❌ Video composition failed — check FFmpeg logs above[/red]")
        return

    size_mb = output_path.stat().st_size / 1024 / 1024
    console.print(f"[green]✅ Video rendered:[/green] {output_path.name}")
    console.print(f"   Size: {size_mb:.1f} MB | Path: {output_path}")
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 9. Quality Check ────────────────────────────────────────────────────
    console.rule("[bold]Stage 7 — Quality Validation[/bold]")
    t = time.time()
    async with SessionLocal() as db:
        from sqlalchemy import select
        script = (await db.execute(select(Script).where(Script.id == script_id))).scalar_one()
        from app.quality.video_validator import validate_video
        report = await validate_video(output_path, script_text=script.script)

    tbl = Table(box=box.SIMPLE, show_header=True)
    tbl.add_column("Check", style="dim")
    tbl.add_column("Result")
    for check, passed in report.checks.items():
        tbl.add_row(check, "[green]PASS[/green]" if passed else "[red]FAIL[/red]")
    console.print(tbl)

    overall = "[bold green]✅ PASSED[/bold green]" if report.passed else "[bold red]❌ FAILED[/bold red]"
    console.print(f"Overall Quality: {overall}")
    console.print(f"   [dim]Time: {time.time()-t:.1f}s[/dim]\n")

    # ── 10. Summary ─────────────────────────────────────────────────────────
    total = time.time() - start
    console.rule("[bold green]🎬 End-to-End Test Complete[/bold green]")

    summary = Table(box=box.ROUNDED, show_header=False)
    summary.add_column("", style="bold")
    summary.add_column("")
    summary.add_row("Topic", topic.title[:70])
    summary.add_row("Duration", f"{actual_duration:.1f}s")
    summary.add_row("File", str(output_path))
    summary.add_row("Size", f"{size_mb:.1f} MB")
    summary.add_row("QC", "PASSED ✅" if report.passed else "FAILED ❌")
    summary.add_row("Total time", f"{total:.0f}s")
    summary.add_row("Publishing", "DRY RUN (set YOUTUBE_PRODUCTION_MODE=true to upload)")
    console.print(summary)

    console.print(f"\n[bold]Open the video:[/bold]")
    console.print(f"  open '{output_path}'\n")


app = typer.Typer()

@app.command()
def main(
    topic: str = typer.Option(None, "--topic", "-t", help="Override topic title for testing")
):
    asyncio.run(run_e2e(custom_topic=topic))


if __name__ == "__main__":
    app()
