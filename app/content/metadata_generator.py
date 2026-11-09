"""
app/content/metadata_generator.py — Generate platform-specific metadata using Gemini AI.
"""
from __future__ import annotations

import json
import re

from app.config import settings
from app.database import Script, Topic, Video, VideoMetadata
from app.logger import get_logger

logger = get_logger("content.metadata_generator")


def _fallback_youtube_title(topic: Topic) -> str:
    title = topic.title
    # Keep under 100 characters, make it curiosity-triggering
    if len(title) <= 90:
        return title
    return title[:87] + "..."


def _fallback_description(topic: Topic, script: Script) -> str:
    return (
        f"{script.hook}\n\n"
        f"{script.script[:800]}\n\n"
        f"#Shorts #{topic.pillar.value.capitalize()} #Facts #Learning\n\n"
        f"Sources and research available on request."
    )


def _fallback_tags(topic: Topic) -> list[str]:
    base_tags = [
        "shorts", "facts", "learnontiktok", "didyouknow",
        topic.pillar.value, "education", "mindblowing", "science",
    ]
    # Add words from title
    title_words = [w.lower() for w in topic.title.split() if len(w) > 4]
    return base_tags + title_words[:8]


async def generate_video_metadata(
    video: Video,
    topic: Topic,
    script: Script,
) -> VideoMetadata | None:
    """Generate YouTube and Instagram metadata for a video."""
    logger.info("Generating metadata for video id=%d", video.id)

    if settings.gemini_api_key:
        return await _gemini_metadata(video, topic, script)
    else:
        return _fallback_metadata(video, topic, script)


async def _gemini_metadata(
    video: Video,
    topic: Topic,
    script: Script,
) -> VideoMetadata:
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""Generate platform-specific metadata for a short-form educational video.

Topic: {topic.title}
Pillar: {topic.pillar.value}
Script hook: {script.hook}
Script: {script.script[:400]}

Generate:
1. YouTube title (max 100 chars) — curiosity-triggering, accurate, no misleading clickbait
2. YouTube description (100-300 words) — informative, adds context, includes hashtags
3. YouTube tags (10-15 relevant tags, no irrelevant trending keywords)
4. Instagram caption (max 2200 chars) — engaging, conversational, ends with hashtags
5. Instagram hashtags (20-30, mix of popular and niche)

Rules:
- Titles must be accurate to the content
- No "SHOCKING", "INSANE", "YOU WON'T BELIEVE" in titles
- Hashtags must be relevant, not just trending
- YouTube description must NOT be a direct copy of the caption

Respond ONLY as JSON:
{{
  "youtube_title": "...",
  "youtube_description": "...",
  "youtube_tags": ["tag1", "tag2"],
  "youtube_category_id": "27",
  "instagram_caption": "...",
  "instagram_hashtags": ["#hashtag1", "#hashtag2"]
}}"""

    try:
        response = model.generate_content(prompt)
        text = response.text
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON in response")

        data = json.loads(json_match.group())
        return VideoMetadata(
            video_id=video.id,
            youtube_title=data.get("youtube_title", _fallback_youtube_title(topic))[:100],
            youtube_description=data.get("youtube_description", _fallback_description(topic, script)),
            youtube_tags=data.get("youtube_tags", _fallback_tags(topic))[:15],
            youtube_category_id=data.get("youtube_category_id", "27"),
            instagram_caption=data.get("instagram_caption", ""),
            instagram_hashtags=data.get("instagram_hashtags", [])[:30],
        )

    except Exception as exc:
        logger.warning("Gemini metadata generation failed: %s — using fallback", exc)
        return _fallback_metadata(video, topic, script)


def _fallback_metadata(video: Video, topic: Topic, script: Script) -> VideoMetadata:
    return VideoMetadata(
        video_id=video.id,
        youtube_title=_fallback_youtube_title(topic),
        youtube_description=_fallback_description(topic, script),
        youtube_tags=_fallback_tags(topic),
        youtube_category_id="27",
        instagram_caption=f"{script.hook}\n\n{script.script[:400]}",
        instagram_hashtags=[
            "#shorts", "#facts", "#learning", "#science",
            f"#{topic.pillar.value}", "#education", "#mindblown",
        ],
    )
