"""
app/scheduler/daily_pipeline.py — The main daily content production state machine.

Stages:
  DISCOVER → RESEARCH → VERIFY → SELECT_TOPIC → WRITE_SCRIPT →
  CREATE_STORYBOARD → GENERATE_VOICE → GENERATE_VISUALS → EDIT_VIDEO →
  GENERATE_METADATA → QUALITY_CHECK → RENDER_FINAL → PUBLISH_YOUTUBE →
  PUBLISH_INSTAGRAM → COLLECT_ANALYTICS → COMPLETE

Each stage persists its output to the Job.context column before proceeding.
Interrupted jobs can be resumed from their last completed stage.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import (
    Asset,
    Job,
    JobStatus,
    Publication,
    PublicationStatus,
    QualityStatus,
    Script,
    Storyboard,
    Topic,
    TopicStatus,
    Video,
    VideoMetadata,
)
from app.logger import get_logger
from app.content.topic_selector import select_daily_topic
from app.content.scriptwriter import generate_script
from app.content.storyboard import generate_storyboard
from app.content.metadata_generator import generate_video_metadata
from app.media.voice_generator import generate_narration, get_audio_duration
from app.media.image_generator import fetch_assets_for_scene
from app.media.subtitle_generator import generate_subtitles
from app.editor.video_composer import compose_video
from app.quality.video_validator import validate_video
from app.publishing.youtube import upload_video as youtube_upload
from app.publishing.instagram import publish_reel

logger = get_logger("scheduler.daily_pipeline")


def _retime_scenes(scenes: list[dict], audio_duration: float) -> list[dict]:
    """Stretch scene timings to cover the real narration length.

    The storyboard is timed from the script's *estimated* duration; if the
    narration runs longer, ffmpeg's -shortest would cut the audio off.
    Scenes are weighted by their narration word count so visuals stay in sync.
    """
    total = audio_duration + 0.5  # small tail so the last word isn't clipped
    weights = [max(len(s.get("narration_segment", "").split()), 1) for s in scenes]
    weight_sum = sum(weights)
    retimed, t = [], 0.0
    for scene, w in zip(scenes, weights):
        dur = total * w / weight_sum
        retimed.append({**scene, "start_time": round(t, 2), "end_time": round(t + dur, 2), "duration": round(dur, 2)})
        t += dur
    return retimed

STAGES = [
    "SELECT_TOPIC",
    "WRITE_SCRIPT",
    "CREATE_STORYBOARD",
    "GENERATE_VOICE",
    "GENERATE_VISUALS",
    "EDIT_VIDEO",
    "GENERATE_METADATA",
    "QUALITY_CHECK",
    "PUBLISH_YOUTUBE",
    "PUBLISH_INSTAGRAM",
    "COMPLETE",
]


class PipelineError(Exception):
    """Raised when a pipeline stage fails and cannot be recovered."""
    pass


class DailyPipeline:
    def __init__(self, db: AsyncSession, job: Job):
        self.db = db
        self.job = job
        self.ctx: dict[str, Any] = job.context or {}

    async def _save_stage(self, stage: str, **context_updates) -> None:
        """Persist current stage and context to database."""
        self.ctx.update(context_updates)
        self.job.stage = stage
        self.job.context = self.ctx
        self.job.status = JobStatus.RUNNING
        await self.db.commit()

    async def _finish(self, success: bool, error: str = "") -> None:
        self.job.status = JobStatus.COMPLETED if success else JobStatus.FAILED
        self.job.completed_at = datetime.now(timezone.utc)
        if error:
            self.job.error_message = error
        await self.db.commit()
        logger.info("Pipeline %s: %s", "completed" if success else "FAILED", self.job.job_id)

    # ── Stage: SELECT_TOPIC ───────────────────────────────────────────────────

    async def stage_select_topic(self) -> Topic:
        logger.info("[1/10] Selecting daily topic...")
        topic = await select_daily_topic(self.db)
        if not topic:
            raise PipelineError("No suitable topic found after research and scoring")
        await self._save_stage("WRITE_SCRIPT", topic_id=topic.id)
        logger.info("Topic selected: [%d] %s", topic.id, topic.title[:80])
        return topic

    # ── Stage: WRITE_SCRIPT ───────────────────────────────────────────────────

    async def stage_write_script(self, topic: Topic) -> Script:
        logger.info("[2/10] Writing script...")
        script = await generate_script(topic)
        if not script:
            raise PipelineError("Script generation failed")

        self.db.add(script)
        await self.db.commit()
        await self.db.refresh(script)

        topic.status = TopicStatus.SCRIPTED
        await self.db.commit()

        await self._save_stage("CREATE_STORYBOARD", script_id=script.id)
        logger.info("Script written: %d words / ~%.0fs", script.word_count, script.estimated_duration)
        return script

    # ── Stage: CREATE_STORYBOARD ──────────────────────────────────────────────

    async def stage_create_storyboard(self, script: Script) -> Storyboard:
        logger.info("[3/10] Creating storyboard...")
        storyboard = await generate_storyboard(script)
        if not storyboard:
            raise PipelineError("Storyboard generation failed")

        self.db.add(storyboard)
        await self.db.commit()
        await self.db.refresh(storyboard)

        await self._save_stage("GENERATE_VOICE", storyboard_id=storyboard.id)
        logger.info("Storyboard: %d scenes", storyboard.total_scenes)
        return storyboard

    # ── Stage: GENERATE_VOICE ─────────────────────────────────────────────────

    async def stage_generate_voice(self, script: Script) -> Path:
        logger.info("[4/10] Generating narration...")
        job_dir = settings.output_drafts / self.job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_path = job_dir / "narration.mp3"

        ok = await generate_narration(script.script, audio_path)
        if not ok:
            raise PipelineError("Narration generation failed")

        actual_duration = await get_audio_duration(audio_path)
        logger.info("Narration generated: %.1fs → %s", actual_duration, audio_path.name)

        await self._save_stage(
            "GENERATE_VISUALS",
            audio_path=str(audio_path),
            actual_duration=actual_duration,
        )
        return audio_path

    # ── Stage: GENERATE_VISUALS ───────────────────────────────────────────────

    async def stage_generate_visuals(self, storyboard: Storyboard) -> dict[int, dict]:
        logger.info("[5/10] Fetching visual assets...")
        job_dir = settings.output_drafts / self.job.job_id / "assets"
        job_dir.mkdir(parents=True, exist_ok=True)

        scene_assets: dict[int, dict] = {}

        for scene in storyboard.scene_data:
            scene_num = scene["scene_number"]
            assets = await fetch_assets_for_scene(scene, job_dir)

            if assets:
                asset = assets[0]
                # Record in DB
                db_asset = Asset(
                    storyboard_id=storyboard.id,
                    file_path=str(asset.local_path),
                    source_url=asset.source_page_url,
                    creator=asset.creator,
                    licence=asset.licence,
                    asset_type=asset.asset_type,
                    checksum=None,
                    attribution_required=False,
                )
                self.db.add(db_asset)
                scene_assets[scene_num] = {
                    "path": str(asset.local_path),
                    "type": asset.asset_type,
                }
            else:
                logger.warning("No asset for scene %d — will use fallback color", scene_num)
                scene_assets[scene_num] = {"path": None, "type": "fallback"}

        await self.db.commit()
        await self._save_stage("EDIT_VIDEO", scene_assets=scene_assets)
        logger.info("Fetched assets for %d/%d scenes", len(scene_assets), len(storyboard.scene_data))
        return scene_assets

    # ── Stage: EDIT_VIDEO ─────────────────────────────────────────────────────

    async def stage_edit_video(
        self,
        storyboard: Storyboard,
        audio_path: Path,
        actual_duration: float,
        scene_assets: dict[int, dict],
        script: Script,
    ) -> Path:
        logger.info("[6/10] Composing video...")
        job_dir = settings.output_drafts / self.job.job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # Generate subtitles
        subtitle_dir = job_dir / "subtitles"
        subtitle_files = await generate_subtitles(
            audio_path=audio_path,
            script_text=script.script,
            total_duration=actual_duration,
            output_dir=subtitle_dir,
        )

        # Build asset path maps
        asset_paths = {}
        asset_types = {}
        for scene_num, asset_info in scene_assets.items():
            if asset_info.get("path"):
                asset_paths[scene_num] = Path(asset_info["path"])
                asset_types[scene_num] = asset_info.get("type", "image")

        # Find optional background music
        music_dir = settings.assets_dir / "music"
        music_path = None
        music_files = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.m4a"))
        if music_files:
            import random
            music_path = random.choice(music_files)

        output_path = settings.output_rendered / f"{self.job.job_id}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)

        ok = await compose_video(
            scenes=_retime_scenes(storyboard.scene_data, actual_duration),
            asset_paths=asset_paths,
            asset_types=asset_types,
            narration_path=audio_path,
            ass_subtitle_path=subtitle_files.get("ass", Path("/nonexistent")),
            output_path=output_path,
            music_path=music_path,
            # Homebrew ffmpeg ships without libass, so the SRT/Pillow fallback is required
            srt_subtitle_path=subtitle_files.get("srt"),
        )

        if not ok:
            raise PipelineError("Video composition failed")

        await self._save_stage("GENERATE_METADATA", video_path=str(output_path))
        logger.info("Video composed: %s", output_path.name)
        return output_path

    # ── Stage: GENERATE_METADATA ──────────────────────────────────────────────

    async def stage_generate_metadata(
        self,
        video: Video,
        topic: Topic,
        script: Script,
    ) -> VideoMetadata:
        logger.info("[7/10] Generating metadata...")
        meta = await generate_video_metadata(video, topic, script)
        if not meta:
            raise PipelineError("Metadata generation failed")

        self.db.add(meta)
        await self.db.commit()
        await self.db.refresh(meta)

        await self._save_stage("QUALITY_CHECK", metadata_id=meta.id)
        return meta

    # ── Stage: QUALITY_CHECK ──────────────────────────────────────────────────

    async def stage_quality_check(self, video: Video, video_path: Path, script: Script) -> bool:
        logger.info("[8/10] Running quality checks...")
        report = await validate_video(video_path, script_text=script.script)

        video.quality_status = QualityStatus.PASSED if report.passed else QualityStatus.FAILED
        video.quality_report = report.to_dict()
        await self.db.commit()

        if not report.passed:
            failed = [k for k, v in report.checks.items() if not v]
            logger.error("Quality FAILED: %s", failed)
            raise PipelineError(f"Quality checks failed: {failed}")

        await self._save_stage("PUBLISH_YOUTUBE")
        logger.info("Quality checks PASSED")
        return True

    # ── Stage: PUBLISH_YOUTUBE ────────────────────────────────────────────────

    async def stage_publish_youtube(
        self,
        video: Video,
        video_path: Path,
        metadata: VideoMetadata,
    ) -> Publication:
        logger.info("[9a/10] Publishing to YouTube...")
        pub = Publication(
            video_id=video.id,
            platform="youtube",
            status=PublicationStatus.UPLOADING,
        )
        self.db.add(pub)
        await self.db.commit()
        await self.db.refresh(pub)

        result = await youtube_upload(video_path, metadata, pub)

        if result.get("success"):
            pub.status = PublicationStatus.PUBLISHED
            pub.platform_video_id = result.get("video_id")
            pub.platform_url = result.get("url")
            pub.published_at = datetime.now(timezone.utc)
            logger.info("✅ YouTube: %s", result.get("url"))
        else:
            pub.status = PublicationStatus.FAILED
            pub.error_message = result.get("error", "Unknown error")
            logger.error("YouTube upload failed: %s", pub.error_message)

        await self.db.commit()
        return pub

    # ── Stage: PUBLISH_INSTAGRAM ──────────────────────────────────────────────

    async def stage_publish_instagram(
        self,
        video: Video,
        video_path: Path,
        metadata: VideoMetadata,
    ) -> Publication:
        logger.info("[9b/10] Publishing to Instagram...")
        pub = Publication(
            video_id=video.id,
            platform="instagram",
            status=PublicationStatus.UPLOADING,
        )
        self.db.add(pub)
        await self.db.commit()
        await self.db.refresh(pub)

        result = await publish_reel(video_path, metadata, pub)

        if result.get("success"):
            pub.status = PublicationStatus.PUBLISHED
            pub.platform_video_id = result.get("media_id")
            pub.platform_url = result.get("url")
            pub.published_at = datetime.now(timezone.utc)
            logger.info("✅ Instagram: %s", result.get("url"))
        elif result.get("skipped"):
            pub.status = PublicationStatus.SKIPPED
            pub.error_message = result.get("error")
            logger.info("Instagram skipped (production mode off)")
        else:
            pub.status = PublicationStatus.FAILED
            pub.error_message = result.get("error", "Unknown error")
            logger.error("Instagram publish failed: %s", pub.error_message)

        await self.db.commit()
        return pub

    # ── Main run loop ─────────────────────────────────────────────────────────

    async def run(self) -> bool:
        """Execute the full pipeline, resuming from last saved stage if interrupted."""
        self.job.status = JobStatus.RUNNING
        self.job.started_at = datetime.now(timezone.utc)
        await self.db.commit()

        try:
            current_stage = self.job.stage or "SELECT_TOPIC"
            logger.info("Starting pipeline %s from stage: %s", self.job.job_id, current_stage)

            # ─ SELECT_TOPIC ─────────────────────────────
            if self.ctx.get("topic_id"):
                from sqlalchemy import select as sa_select
                topic = (await self.db.execute(
                    sa_select(Topic).where(Topic.id == self.ctx["topic_id"])
                )).scalar_one_or_none()
            else:
                topic = await self.stage_select_topic()
            if not topic:
                raise PipelineError("No topic available")

            # ─ WRITE_SCRIPT ──────────────────────────────
            if self.ctx.get("script_id"):
                from sqlalchemy import select as sa_select
                script = (await self.db.execute(
                    sa_select(Script).where(Script.id == self.ctx["script_id"])
                )).scalar_one_or_none()
            else:
                script = await self.stage_write_script(topic)

            # ─ CREATE_STORYBOARD ──────────────────────────
            if self.ctx.get("storyboard_id"):
                from sqlalchemy import select as sa_select
                storyboard = (await self.db.execute(
                    sa_select(Storyboard).where(Storyboard.id == self.ctx["storyboard_id"])
                )).scalar_one_or_none()
            else:
                storyboard = await self.stage_create_storyboard(script)

            # ─ GENERATE_VOICE ─────────────────────────────
            if self.ctx.get("audio_path"):
                audio_path = Path(self.ctx["audio_path"])
                actual_duration = self.ctx.get("actual_duration", 20.0)
            else:
                audio_path = await self.stage_generate_voice(script)
                actual_duration = self.ctx.get("actual_duration", 20.0)

            # ─ GENERATE_VISUALS ───────────────────────────
            if self.ctx.get("scene_assets"):
                scene_assets = self.ctx["scene_assets"]
                # Convert keys back to int
                scene_assets = {int(k): v for k, v in scene_assets.items()}
            else:
                scene_assets = await self.stage_generate_visuals(storyboard)

            # ─ EDIT_VIDEO ─────────────────────────────────
            if self.ctx.get("video_path"):
                video_path = Path(self.ctx["video_path"])
            else:
                video_path = await self.stage_edit_video(
                    storyboard, audio_path, actual_duration, scene_assets, script
                )

            # Create Video DB record
            video = Video(
                topic_id=topic.id,
                script_id=script.id,
                job_id=self.job.job_id,
                render_path=str(video_path),
                quality_status=QualityStatus.PENDING,
            )
            self.db.add(video)
            await self.db.commit()
            await self.db.refresh(video)

            # ─ GENERATE_METADATA ──────────────────────────
            if self.ctx.get("metadata_id"):
                from sqlalchemy import select as sa_select
                metadata = (await self.db.execute(
                    sa_select(VideoMetadata).where(VideoMetadata.id == self.ctx["metadata_id"])
                )).scalar_one_or_none()
            else:
                metadata = await self.stage_generate_metadata(video, topic, script)

            # ─ QUALITY_CHECK ──────────────────────────────
            await self.stage_quality_check(video, video_path, script)

            # ─ PUBLISH_YOUTUBE ────────────────────────────
            yt_pub = await self.stage_publish_youtube(video, video_path, metadata)

            # ─ PUBLISH_INSTAGRAM ──────────────────────────
            # Instagram failure doesn't block completion
            try:
                ig_pub = await self.stage_publish_instagram(video, video_path, metadata)
            except Exception as exc:
                logger.error("Instagram stage failed non-critically: %s", exc)

            # ─ Update topic status ──────────────────────────
            topic.status = TopicStatus.PUBLISHED
            await self.db.commit()

            # ─ Move to published directory ──────────────────
            import shutil
            published_path = settings.output_published / video_path.name
            if video_path.exists():
                shutil.copy2(video_path, published_path)
                video.render_path = str(published_path)
                await self.db.commit()

            await self._finish(success=True)
            return True

        except PipelineError as exc:
            await self._finish(success=False, error=str(exc))
            await _notify_operator(f"Pipeline FAILED: {exc}", self.job.job_id)
            return False
        except Exception as exc:
            logger.exception("Unexpected pipeline error")
            await self._finish(success=False, error=str(exc))
            await _notify_operator(f"Pipeline ERROR: {exc}", self.job.job_id)
            return False


async def _notify_operator(message: str, job_id: str) -> None:
    """Send operator notification via webhook (if configured)."""
    if not settings.notification_webhook_url:
        return
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            await client.post(
                settings.notification_webhook_url,
                json={"text": f"🚨 Faceless AI Channel\nJob: {job_id}\n{message}"},
                timeout=10.0,
            )
    except Exception:
        pass  # Notifications are best-effort


async def create_and_run_daily_job(db: AsyncSession) -> bool:
    """
    Create a new daily pipeline job and run it.
    Prevents concurrent duplicate jobs.
    """
    from sqlalchemy import select as sa_select

    # Check for running job
    running = (await db.execute(
        sa_select(Job).where(
            Job.job_type == "daily_pipeline",
            Job.status.in_([JobStatus.RUNNING, JobStatus.PENDING]),
        )
    )).scalar_one_or_none()

    if running:
        logger.warning("Daily pipeline already running (job_id=%s)", running.job_id)
        return False

    job_id = f"daily_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    job = Job(
        job_id=job_id,
        job_type="daily_pipeline",
        status=JobStatus.PENDING,
        stage="SELECT_TOPIC",
        context={},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    logger.info("Starting daily pipeline: %s", job_id)
    pipeline = DailyPipeline(db, job)
    return await pipeline.run()
