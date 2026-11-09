"""
app/content/metadata_generator.py — Generate platform-specific metadata using Gemini AI.
"""
from __future__ import annotations

import asyncio
import json
import re

from app.config import settings
from app.database import Script, Topic, Video, VideoMetadata
from app.logger import get_logger

logger = get_logger("content.metadata_generator")

# ── Channel footer appended to every YouTube description ─────────────────────
CHANNEL_FOOTER = (
    "\n\n──────────────────────\n"
    f"🧠 {settings.channel_name} — {settings.channel_handle}\n"
    "New Short every day at 6:30 PM IST.\n"
    f"{settings.youtube_channel_url}\n"
    "──────────────────────\n"
    "#Shorts #Psychology #MindGlitchedAI #Facts #BrainFacts #LearnOnYouTube"
)


def _fallback_youtube_title(topic: Topic) -> str:
    title = topic.title
    if len(title) <= 90:
        return title
    return title[:87] + "..."


def _fallback_description(topic: Topic, script: Script) -> str:
    return (
        f"{script.hook}\n\n"
        f"{script.script[:800]}\n\n"
        f"#{topic.pillar.value.capitalize()} #Facts #Shorts"
        f"{CHANNEL_FOOTER}"
    )


def _fallback_tags(topic: Topic) -> list[str]:
    base_tags = [
        "shorts", "youtubeshorts", "facts", "psychology", "mindblown",
        "learnontiktok", "didyouknow", topic.pillar.value,
        "education", "science", "brainfacts", "mindglitchedai",
    ]
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
    return _fallback_metadata(video, topic, script)


async def _gemini_metadata(
    video: Video,
    topic: Topic,
    script: Script,
) -> VideoMetadata:
    try:
        from google import genai
    except ImportError:
        return _fallback_metadata(video, topic, script)

    prompt = f"""Generate platform-specific metadata for a YouTube Shorts video.

Channel: {settings.channel_name} ({settings.channel_handle})
Topic: {topic.title}
Content pillar: {topic.pillar.value}
Script hook: {script.hook}
Full script: {script.script[:500]}

Generate:
1. youtube_title — max 70 chars, curiosity-triggering, accurate. No "SHOCKING"/"INSANE" clickbait.
2. youtube_description — 80-200 words. First 2 lines must hook the reader (shown before "more").
   End with these exact hashtags: #Shorts #Psychology #MindGlitchedAI
3. youtube_tags — 12-15 specific tags relevant to the content. Include "shorts", "psychology", "mindglitchedai".
4. youtube_category_id — use "27" (Education)
5. instagram_caption — max 300 chars, punchy, ends with 3 hashtags

Rules:
- Titles must be factually accurate
- Description must NOT repeat the title verbatim
- Tags must be specific, not just trending noise

Respond ONLY as JSON with no markdown:
{{
  "youtube_title": "...",
  "youtube_description": "...",
  "youtube_tags": ["tag1", "tag2"],
  "youtube_category_id": "27",
  "instagram_caption": "..."
}}"""

    try:
        client = genai.Client(api_key=settings.gemini_api_key)
        # Gemini intermittently returns 503 UNAVAILABLE; falling back means the
        # raw news headline becomes the YouTube title, so retry before giving up.
        for attempt in range(4):
            try:
                response = client.models.generate_content(
                    model="gemini-3.8-flash",
                    contents=prompt,
                )
                break
            except Exception as exc:
                if attempt == 3 or "503" not in str(exc):
                    raise
                logger.warning("Gemini metadata 503 — retrying in %ds", 30 * (attempt + 1))
                await asyncio.sleep(30 * (attempt + 1))
        raw = response.text.strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON in Gemini response")

        data = json.loads(json_match.group())

        # Always append the channel footer to the YouTube description
        yt_desc = data.get("youtube_description", _fallback_description(topic, script))
        if CHANNEL_FOOTER.strip() not in yt_desc:
            yt_desc += CHANNEL_FOOTER

        # Build Instagram hashtags from pillar + standard set
        ig_hashtags = [
            "#Shorts", "#Psychology", "#MindGlitchedAI", "#BrainFacts",
            "#Facts", "#LearnOnYouTube", "#Science", "#Education",
            f"#{topic.pillar.value.capitalize()}", "#Mindset", "#CognitiveBias",
        ]

        return VideoMetadata(
            video_id=video.id,
            youtube_title=data.get("youtube_title", _fallback_youtube_title(topic))[:100],
            youtube_description=yt_desc,
            youtube_tags=data.get("youtube_tags", _fallback_tags(topic))[:15],
            youtube_category_id=data.get("youtube_category_id", "27"),
            instagram_caption=data.get("instagram_caption", f"{script.hook}\n\n#Shorts #Psychology #MindGlitchedAI"),
            instagram_hashtags=ig_hashtags,
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
        instagram_caption=f"{script.hook}\n\n#Shorts #Psychology #MindGlitchedAI",
        instagram_hashtags=[
            "#Shorts", "#Psychology", "#MindGlitchedAI", "#BrainFacts",
            "#Facts", "#LearnOnYouTube", f"#{topic.pillar.value.capitalize()}",
        ],
    )
