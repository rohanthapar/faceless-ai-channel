"""
tests/test_pipeline_retry.py — control flow of the AI-review retry loop.

Every slow/external stage (Gemini, TTS, stock footage, ffmpeg, YouTube) is
replaced with a stub so only DailyPipeline.run's decision logic is exercised.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.database import ContentPillar, Job, JobStatus, Script, Storyboard, Topic, TopicStatus, VideoMetadata
from app.quality.content_reviewer import ReviewResult
from app.scheduler import daily_pipeline as dp


def _review(overall: float, accuracy: float = 9.0) -> ReviewResult:
    passed = overall >= settings.review_min_overall and accuracy >= settings.review_min_accuracy
    return ReviewResult(passed=passed, scores={"overall": overall, "accuracy": accuracy}, issues=["stub"])


async def _run_pipeline(db_session, tmp_path, monkeypatch, reviews: list[ReviewResult]):
    """Run the pipeline with stubbed stages; returns call log + uploaded path."""
    published = tmp_path / "published"
    published.mkdir()
    monkeypatch.setattr(type(settings), "output_published", property(lambda self: published))

    validation = SimpleNamespace(passed=True, checks={}, to_dict=lambda: {"passed": True})

    async def fake_validate(*a, **k):
        return validation

    queue = list(reviews)

    async def fake_review(*a, **k):
        return queue.pop(0)

    monkeypatch.setattr(dp, "validate_video", fake_validate)
    monkeypatch.setattr(dp, "review_video", fake_review)

    job = Job(job_id="daily_20261111_130000_test01", job_type="daily_pipeline", status=JobStatus.PENDING)
    db_session.add(job)
    await db_session.commit()
    pipe = dp.DailyPipeline(db_session, job)
    calls: list[str] = []
    uploaded: dict = {}

    async def select_topic():
        calls.append("topic")
        t = Topic(title="T", summary="S", pillar=ContentPillar.SCIENCE, source_urls=[], status=TopicStatus.SELECTED)
        db_session.add(t)
        await db_session.commit()
        await pipe._save_stage("WRITE_SCRIPT", topic_id=t.id)
        return t

    async def write_script(topic):
        calls.append("script")
        s = Script(topic_id=topic.id, hook="h", script="s", word_count=60, estimated_duration=25)
        db_session.add(s)
        await db_session.commit()
        await pipe._save_stage("CREATE_STORYBOARD", script_id=s.id)
        return s

    async def storyboard(script):
        calls.append("storyboard")
        sb = Storyboard(script_id=script.id, scene_data=[], visual_style={}, total_scenes=0)
        db_session.add(sb)
        await db_session.commit()
        return sb

    async def voice(script):
        calls.append("voice")
        pipe.ctx["actual_duration"] = 25.0
        return tmp_path / "narration.mp3"

    async def visuals(sb):
        calls.append("visuals")
        return {1: {"path": None, "type": "fallback"}}

    async def edit(sb, audio, dur, assets, script):
        calls.append("edit")
        out = tmp_path / "render.mp4"
        out.write_text(f"render #{calls.count('edit')}")
        return out

    async def metadata(video, topic, script):
        calls.append("metadata")
        m = VideoMetadata(video_id=video.id, youtube_title="title")
        db_session.add(m)
        await db_session.commit()
        return m

    async def publish_youtube(video, path, meta):
        calls.append("upload")
        uploaded["content"] = Path(path).read_text()
        return SimpleNamespace(status="PUBLISHED")

    async def publish_instagram(*a):
        return None

    for name, fn in {
        "stage_select_topic": select_topic, "stage_write_script": write_script,
        "stage_create_storyboard": storyboard, "stage_generate_voice": voice,
        "stage_generate_visuals": visuals, "stage_edit_video": edit,
        "stage_generate_metadata": metadata, "stage_publish_youtube": publish_youtube,
        "stage_publish_instagram": publish_instagram,
    }.items():
        monkeypatch.setattr(pipe, name, fn)

    ok = await pipe.run()
    return ok, calls, uploaded, job


async def test_review_pass_first_time(db_session, tmp_path, monkeypatch):
    ok, calls, uploaded, job = await _run_pipeline(db_session, tmp_path, monkeypatch, [_review(8)])
    assert ok and calls.count("edit") == 1 and calls.count("upload") == 1
    assert job.status == JobStatus.COMPLETED and not job.error_message


async def test_review_retries_with_new_visuals_then_passes(db_session, tmp_path, monkeypatch):
    ok, calls, uploaded, _ = await _run_pipeline(db_session, tmp_path, monkeypatch, [_review(4), _review(7)])
    assert ok
    assert calls.count("edit") == 2 and calls.count("storyboard") == 2
    assert calls.count("script") == 1  # accuracy was fine: script kept
    assert uploaded["content"] == "render #2"


async def test_low_accuracy_regenerates_script(db_session, tmp_path, monkeypatch):
    ok, calls, _, _ = await _run_pipeline(
        db_session, tmp_path, monkeypatch, [_review(5, accuracy=4), _review(8)]
    )
    assert ok and calls.count("script") == 2 and calls.count("voice") == 2 and calls.count("metadata") == 2


async def test_all_attempts_fail_uploads_best_private(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "youtube_default_privacy", "private")
    ok, calls, uploaded, job = await _run_pipeline(
        db_session, tmp_path, monkeypatch, [_review(3), _review(5), _review(4)]
    )
    assert ok and calls.count("edit") == 3 and calls.count("upload") == 1
    assert uploaded["content"] == "render #2"  # the 5/10 attempt, not the last one
    assert "AI review failed" in job.error_message


async def test_all_attempts_fail_never_uploads_publicly(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "youtube_default_privacy", "public")
    ok, calls, _, job = await _run_pipeline(
        db_session, tmp_path, monkeypatch, [_review(3), _review(5), _review(4)]
    )
    assert not ok and "upload" not in calls and job.status == JobStatus.FAILED


async def test_job_context_persists_across_stages(db_session, tmp_path, monkeypatch):
    """Regression: in-place JSON mutation meant only topic_id was ever saved."""
    _, _, _, job = await _run_pipeline(db_session, tmp_path, monkeypatch, [_review(8)])
    await db_session.refresh(job)
    assert {"topic_id", "script_id"} <= set(job.context)
