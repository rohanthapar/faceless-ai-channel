"""
app/quality/content_reviewer.py — AI editorial review of the rendered Short.

Gemini watches the final MP4 and scores it the way a viewer would experience it:
factual accuracy vs the source, hook strength, visual relevance and caption
readability. Videos below the bar are blocked from upload — a skipped day costs
less than a weak video dragging down the channel's retention signals.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from app.config import settings
from app.content.gemini import generate_text
from app.database import Script, Topic, VideoMetadata
from app.logger import get_logger

logger = get_logger("quality.content_reviewer")

SCORE_KEYS = ("accuracy", "hook", "visual_relevance", "captions", "overall")


@dataclass
class ReviewResult:
    passed: bool
    scores: dict[str, float] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    skipped: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


async def _upload_video(client, video_path: Path):
    """Upload to the Gemini Files API and wait until it's ready for prompting."""
    uploaded = await asyncio.to_thread(client.files.upload, file=str(video_path))
    for _ in range(60):
        state = str(getattr(uploaded.state, "name", uploaded.state))
        if state == "ACTIVE":
            return uploaded
        if state == "FAILED":
            raise RuntimeError("Gemini failed to process the video")
        await asyncio.sleep(2)
        uploaded = await asyncio.to_thread(client.files.get, name=uploaded.name)
    raise TimeoutError("Gemini video processing timed out")


async def review_video(
    video_path: Path,
    topic: Topic,
    script: Script,
    metadata: VideoMetadata | None,
) -> ReviewResult:
    """Score the rendered video; fail-open (skipped=True) if Gemini is unavailable."""
    if not settings.gemini_api_key:
        return ReviewResult(passed=True, skipped=True, issues=["No Gemini key — review skipped"])

    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    prompt = f"""You are a strict editor for a YouTube Shorts facts channel. Watch this video
and judge it as a viewer scrolling Shorts would experience it.

Source topic: {topic.title}
Source facts: {topic.summary}
Narration script: {script.script}
YouTube title: {metadata.youtube_title if metadata else "(none)"}

Score each 1-10:
- accuracy: are narration, on-screen text and title faithful to the source facts? Any invented
  claims, exaggerations or a title that doesn't match the video score low.
- hook: would the first 2 seconds (visual + on-screen text + first words) stop a viewer swiping?
- visual_relevance: do the visuals show what the narration is talking about at each moment?
  Random or mismatched stock footage, blank screens or repeated shots score low.
- captions: are captions readable, in sync with speech and not covering important visuals?
- overall: would this video help or hurt a channel's retention and reputation?

Respond ONLY as JSON with no markdown:
{{"accuracy": 0, "hook": 0, "visual_relevance": 0, "captions": 0, "overall": 0,
  "issues": ["short concrete problem", "..."]}}"""

    try:
        uploaded = await _upload_video(client, video_path)
        try:
            raw = await generate_text([uploaded, prompt])
        finally:
            await asyncio.to_thread(client.files.delete, name=uploaded.name)

        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError(f"No JSON in review response: {raw[:200]}")
        data = json.loads(match.group())

        scores = {k: float(data.get(k, 0)) for k in SCORE_KEYS}
        issues = [str(i) for i in data.get("issues", [])]
        passed = (
            scores["overall"] >= settings.review_min_overall
            and scores["accuracy"] >= settings.review_min_accuracy
        )
        logger.info("Content review %s: %s", "PASSED" if passed else "FAILED", scores)
        for issue in issues:
            logger.info("  review issue: %s", issue)
        return ReviewResult(passed=passed, scores=scores, issues=issues)

    except Exception as exc:
        # The video already passed technical QC and uploads are private-first,
        # so an unavailable reviewer shouldn't cost the day's video.
        logger.warning("Content review unavailable — skipping: %s", exc)
        return ReviewResult(passed=True, skipped=True, issues=[f"Review error: {exc}"])
