"""
app/content/storyboard.py — Convert a script into a visual storyboard.

Splits the script into scenes, assigns visual descriptions, camera movements,
lighting and mood for each scene. 4-6 scenes for a 20-second video.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from typing import Any

from app.config import settings
from app.content.gemini import generate_text
from app.database import Script, Storyboard
from app.logger import get_logger

logger = get_logger("content.storyboard")

CAMERA_MOVEMENTS = [
    "slow push-in",
    "gentle dolly forward",
    "subtle zoom out",
    "parallax drift",
    "slow pan left",
    "slow pan right",
    "tracking movement",
    "atmospheric float",
]

LIGHTING_MOODS = [
    "dramatic side lighting",
    "warm golden hour",
    "cool blue cinematic",
    "high contrast noir",
    "soft diffused daylight",
    "moody dark atmospheric",
    "bright clean editorial",
]


@dataclass
class Scene:
    scene_number: int
    start_time: float
    end_time: float
    narration_segment: str
    visual_description: str
    camera_angle: str
    camera_movement: str
    lighting: str
    mood: str
    subject: str
    transition: str
    search_keywords: list[str] = field(default_factory=list)
    asset_source: str = "pexels"
    asset_licence: str = "Pexels License (free for commercial use)"

    def to_dict(self) -> dict[str, Any]:
        return {
            "scene_number": self.scene_number,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration": round(self.end_time - self.start_time, 2),
            "narration_segment": self.narration_segment,
            "visual_description": self.visual_description,
            "camera_angle": self.camera_angle,
            "camera_movement": self.camera_movement,
            "lighting": self.lighting,
            "mood": self.mood,
            "subject": self.subject,
            "transition": self.transition,
            "search_keywords": self.search_keywords,
            "asset_source": self.asset_source,
            "asset_licence": self.asset_licence,
        }


def _split_into_sentences(text: str) -> list[str]:
    """Split text into sentences, preserving meaning."""
    sentences = re.split(r'(?<=[.!?])\s+', text.strip())
    return [s.strip() for s in sentences if s.strip()]


def _group_sentences_into_scenes(
    sentences: list[str],
    target_scenes: int,
) -> list[list[str]]:
    """Group sentences into approximately target_scenes groups."""
    if not sentences:
        return []
    if len(sentences) <= target_scenes:
        return [[s] for s in sentences]

    group_size = math.ceil(len(sentences) / target_scenes)
    groups = []
    for i in range(0, len(sentences), group_size):
        groups.append(sentences[i : i + group_size])
    return groups[:target_scenes]


def _extract_keywords(text: str) -> list[str]:
    """Extract meaningful search keywords from scene text."""
    # Remove common stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "can", "this", "that",
        "these", "those", "and", "or", "but", "if", "in", "on",
        "at", "to", "for", "of", "with", "by", "from", "it", "its",
        "you", "your", "we", "our", "they", "their", "he", "she",
        "not", "no", "so", "as", "into", "over", "about", "what",
        "how", "when", "where", "why", "which", "who",
    }
    words = re.findall(r'\b[a-zA-Z]{4,}\b', text)
    keywords = [w.lower() for w in words if w.lower() not in stop_words]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique.append(kw)
    return unique[:5]


def _infer_visual_description(narration: str, scene_num: int, total_scenes: int) -> dict:
    """Infer visual attributes from narration text."""
    import random

    # Choose camera movement — avoid repeating same one consecutively
    movement = CAMERA_MOVEMENTS[(scene_num - 1) % len(CAMERA_MOVEMENTS)]
    lighting = LIGHTING_MOODS[(scene_num - 1) % len(LIGHTING_MOODS)]

    # Mood based on position in video
    if scene_num == 1:
        mood = "attention-grabbing, intense"
        camera_angle = "tight close-up"
    elif scene_num == total_scenes:
        mood = "resolving, satisfying"
        camera_angle = "wide establishing"
    else:
        mood = "engaging, curious"
        camera_angle = "medium shot"

    # Transition
    if scene_num < total_scenes:
        transitions = ["cross-dissolve", "hard cut", "fade through black", "smooth cut"]
        transition = transitions[scene_num % len(transitions)]
    else:
        transition = "none"

    # Extract subject from text
    nouns_pattern = re.compile(
        r'\b(brain|mind|memory|person|scientist|researcher|experiment|'
        r'technology|discovery|mystery|history|event|place|building|'
        r'animal|space|planet|data|study|research|effect|bias|phenomenon)\b',
        re.IGNORECASE,
    )
    subjects = nouns_pattern.findall(narration)
    subject = subjects[0].lower() if subjects else "abstract concept visualization"

    return {
        "camera_angle": camera_angle,
        "camera_movement": movement,
        "lighting": lighting,
        "mood": mood,
        "subject": subject,
        "transition": transition,
    }


async def _gemini_storyboard(script: Script) -> list[Scene] | None:
    """Use Gemini to generate rich visual descriptions per scene."""
    if not settings.gemini_api_key:
        return None

    try:
        from google import genai
    except ImportError:
        return None

    total_duration = script.estimated_duration or 35.0
    # A new visual every ~3s keeps Shorts viewers from swiping away
    target_scenes = max(7, min(12, round(total_duration / 3)))

    prompt = f"""You are a cinematic storyboard director for a short-form vertical video (9:16, {total_duration:.0f} seconds).

Script:
{script.script}

Create a {target_scenes}-scene storyboard. Each scene should have DIFFERENT visuals — no repetition.
Each scene covers roughly {total_duration/target_scenes:.1f} seconds of narration.

For each scene provide:
- narration_segment: exact words from the script for this scene
- visual_description: specific cinematic shot description (not generic)
- subject: the ONE thing on screen, as 1-2 common words a stock library surely has
  (e.g. "tent", "avalanche", "mantis shrimp", "radar dish") — not an action or a phrase
- search_keywords: exactly 3 literal stock-footage search terms, ordered specific → generic.
  The first may be 2-4 words; the LAST must be 1-2 common words (e.g. "tent", "snowy forest").
  Only things a camera could actually film that directly show what the narration says —
  no metaphors, moods, glitch effects or abstract concepts that stock sites won't have
- camera_angle: (close-up / medium / wide / aerial / macro)
- camera_movement: pick one from [slow push-in, dolly forward, subtle zoom, parallax drift, pan left, pan right, tracking, atmospheric float]
- lighting: specific lighting description
- mood: emotional quality of the shot
- transition: (hard cut / cross-dissolve / fade)

Respond ONLY as a JSON array with no markdown:
[
  {{
    "narration_segment": "...",
    "visual_description": "...",
    "subject": "...",
    "search_keywords": ["keyword1", "keyword2", "keyword3"],
    "camera_angle": "...",
    "camera_movement": "...",
    "lighting": "...",
    "mood": "...",
    "transition": "..."
  }}
]"""

    try:
        raw = await generate_text(prompt)
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)

        json_match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not json_match:
            return None
        scenes_data = json.loads(json_match.group())

        total_time = script.estimated_duration or 35.0
        time_per_scene = total_time / len(scenes_data)

        scenes = []
        for i, sd in enumerate(scenes_data):
            start = i * time_per_scene
            end = start + time_per_scene
            scenes.append(
                Scene(
                    scene_number=i + 1,
                    start_time=round(start, 2),
                    end_time=round(end, 2),
                    narration_segment=sd.get("narration_segment", ""),
                    visual_description=sd.get("visual_description", ""),
                    camera_angle=sd.get("camera_angle", "medium shot"),
                    camera_movement=sd.get("camera_movement", "slow push-in"),
                    lighting=sd.get("lighting", "natural"),
                    mood=sd.get("mood", "engaging"),
                    subject=sd.get("subject", "abstract"),
                    transition=sd.get("transition", "hard cut"),
                    search_keywords=sd.get("search_keywords", []),
                    asset_source="pixabay",
                    asset_licence="Pixabay License (free for commercial use)",
                )
            )
        return scenes

    except Exception as exc:
        logger.warning("Gemini storyboard failed: %s", exc)
        return None


def _heuristic_storyboard(script: Script) -> list[Scene]:
    """Generate storyboard from script without AI."""
    sentences = _split_into_sentences(script.script)
    total_duration = script.estimated_duration or 35.0
    target_scenes = max(6, min(8, len(sentences)))

    groups = _group_sentences_into_scenes(sentences, target_scenes)
    time_per_scene = total_duration / len(groups)
    scenes = []

    for i, sentence_group in enumerate(groups):
        narration = " ".join(sentence_group)
        attrs = _infer_visual_description(narration, i + 1, len(groups))
        keywords = _extract_keywords(narration)

        scenes.append(
            Scene(
                scene_number=i + 1,
                start_time=round(i * time_per_scene, 2),
                end_time=round((i + 1) * time_per_scene, 2),
                narration_segment=narration,
                visual_description=f"Cinematic {attrs['camera_angle']} of {attrs['subject']}",
                camera_angle=attrs["camera_angle"],
                camera_movement=attrs["camera_movement"],
                lighting=attrs["lighting"],
                mood=attrs["mood"],
                subject=attrs["subject"],
                transition=attrs["transition"],
                search_keywords=keywords,
                asset_source="pexels",
                asset_licence="Pexels License (free for commercial use)",
            )
        )

    return scenes


async def generate_storyboard(script: Script) -> Storyboard | None:
    """Generate a visual storyboard from a script."""
    logger.info("Generating storyboard for script id=%d", script.id)

    # Try Gemini first, fall back to heuristic
    scenes = await _gemini_storyboard(script)
    if not scenes:
        logger.info("Using heuristic storyboard")
        scenes = _heuristic_storyboard(script)

    if not scenes:
        logger.error("Failed to generate storyboard")
        return None

    visual_style = {
        "aspect_ratio": "9:16",
        "resolution": f"{settings.video_width}x{settings.video_height}",
        "fps": settings.video_fps,
        "brand_color": settings.brand_color,
        "caption_font": settings.caption_font,
        "caption_size": settings.caption_size,
    }

    logger.info("Storyboard: %d scenes / %.1fs total", len(scenes), script.estimated_duration or 0)

    return Storyboard(
        script_id=script.id,
        scene_data=[s.to_dict() for s in scenes],
        visual_style=visual_style,
        total_scenes=len(scenes),
    )
