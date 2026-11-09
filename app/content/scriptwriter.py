"""
app/content/scriptwriter.py — Generate original short-form scripts using Gemini AI.

Generates 3 hook variants, 2 script variants, scores them, and returns the best.
Falls back to a template-based approach if the Gemini API is unavailable.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.config import settings
from app.database import ContentPillar, Script, Topic
from app.logger import get_logger

logger = get_logger("content.scriptwriter")


@dataclass
class ScriptCandidate:
    hook: str
    full_script: str
    word_count: int
    estimated_duration_seconds: float
    clarity_score: float
    originality_score: float
    retention_score: float
    total_score: float


PILLAR_GUIDANCE = {
    ContentPillar.PSYCHOLOGY: (
        "Focus on human behaviour, cognitive biases, psychological experiments, "
        "memory, perception, and counterintuitive mental phenomena. "
        "Use second-person 'you' to create personal connection."
    ),
    ContentPillar.HISTORY: (
        "Focus on forgotten events, historical mysteries, archaeological finds, "
        "unusual personalities, and strange but documented incidents. "
        "Build a narrative arc with a surprising reveal."
    ),
    ContentPillar.TECHNOLOGY: (
        "Focus on AI, emerging technology, engineering breakthroughs, and "
        "unexpected applications of science. Keep it accessible to non-experts."
    ),
    ContentPillar.STORYTELLING: (
        "Tell a compelling real-world story with clear beginning, middle, and end. "
        "Prioritise narrative tension and resolution. Attribute sources appropriately."
    ),
}


def _words_per_minute() -> float:
    """Average narration speed target."""
    return 150.0  # words/min — conversational paced narration


def _estimate_duration(text: str) -> float:
    """Estimate spoken duration in seconds."""
    words = len(text.split())
    return (words / _words_per_minute()) * 60


async def _gemini_generate_script(topic: Topic) -> list[ScriptCandidate]:
    """Use Gemini Flash to generate script candidates."""
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    guidance = PILLAR_GUIDANCE.get(topic.pillar, "")
    min_words = 40
    max_words = 70

    prompt = f"""You are a scriptwriter for a faceless short-form video channel (YouTube Shorts / Instagram Reels).

Topic: {topic.title}
Summary: {topic.summary or ''}
Content pillar: {topic.pillar.value}
Pillar guidance: {guidance}

Requirements:
- Write for a 15-30 second video at natural narration pace (~150 words/minute)
- Target {min_words}-{max_words} spoken words
- Structure: Hook (1-3 sec) → Context → Main Revelation → Payoff → Optional micro-CTA
- Natural, conversational English — not robotic or formal
- No "Hey guys", "In today's video", "Don't forget to like" intros
- Strong opening hook that creates immediate curiosity
- One clear main idea
- No invented facts, quotes, or events
- Note uncertainty where facts are disputed
- No misleading clickbait

Generate exactly:
1. THREE different hook openings (one sentence each, maximum 15 words)
2. TWO complete scripts (each {min_words}-{max_words} words)
3. Score each script 0.0-1.0 on: clarity, originality, retention_potential

Respond ONLY in this exact JSON format:
{{
  "hooks": ["hook 1", "hook 2", "hook 3"],
  "scripts": [
    {{
      "text": "Full script text here...",
      "clarity": 0.85,
      "originality": 0.80,
      "retention": 0.90
    }},
    {{
      "text": "Alternative script text here...",
      "clarity": 0.75,
      "originality": 0.85,
      "retention": 0.80
    }}
  ]
}}"""

    try:
        response = model.generate_content(prompt)
        text = response.text

        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON in Gemini response")

        data = json.loads(json_match.group())
        hooks = data.get("hooks", [])
        scripts_data = data.get("scripts", [])

        if not hooks or not scripts_data:
            raise ValueError("Missing hooks or scripts in response")

        # Pick the best hook (simple heuristic: shortest that still creates curiosity)
        best_hook = max(hooks, key=lambda h: _hook_score(h)) if hooks else hooks[0]

        candidates = []
        for s in scripts_data:
            text_body = s.get("text", "")
            if not text_body:
                continue
            word_count = len(text_body.split())
            duration = _estimate_duration(text_body)
            clarity = float(s.get("clarity", 0.7))
            originality = float(s.get("originality", 0.7))
            retention = float(s.get("retention", 0.7))
            total = (clarity + originality + retention) / 3

            candidates.append(
                ScriptCandidate(
                    hook=best_hook,
                    full_script=text_body,
                    word_count=word_count,
                    estimated_duration_seconds=duration,
                    clarity_score=clarity,
                    originality_score=originality,
                    retention_score=retention,
                    total_score=total,
                )
            )

        candidates.sort(key=lambda c: c.total_score, reverse=True)
        return candidates

    except Exception as exc:
        logger.warning("Gemini script generation failed: %s", exc)
        return []


def _hook_score(hook: str) -> float:
    """Score a hook for curiosity and brevity."""
    curiosity_words = ["what if", "did you know", "why", "how", "secret", "truth", "actually"]
    score = sum(1 for w in curiosity_words if w in hook.lower()) * 0.3
    # Prefer medium length (8-15 words)
    words = len(hook.split())
    if 8 <= words <= 15:
        score += 1.0
    return score


def _template_script(topic: Topic) -> ScriptCandidate:
    """Fallback template-based script when AI is unavailable."""
    title = topic.title
    summary = topic.summary or "This fascinating topic deserves your full attention."

    hook = f"Here's something most people don't know about {title.split()[0].lower()} science."
    script = (
        f"{hook}\n\n"
        f"{summary[:200]}\n\n"
        f"Scientists and researchers have documented this phenomenon extensively, "
        f"and it challenges many of our everyday assumptions.\n\n"
        f"Next time you encounter this, you'll see it very differently."
    )

    # Trim to target word count
    words = script.split()
    if len(words) > 70:
        script = " ".join(words[:68]) + "."

    return ScriptCandidate(
        hook=hook,
        full_script=script,
        word_count=len(script.split()),
        estimated_duration_seconds=_estimate_duration(script),
        clarity_score=0.65,
        originality_score=0.50,
        retention_score=0.60,
        total_score=0.58,
    )


async def generate_script(topic: Topic) -> Script | None:
    """
    Generate, score and select the best script for a topic.
    Returns a Script ORM instance (not yet saved to DB — caller saves).
    """
    if not topic.summary and not topic.title:
        logger.error("Topic has no title or summary")
        return None

    logger.info("Generating script for: %s", topic.title[:80])

    candidates: list[ScriptCandidate] = []

    if settings.gemini_api_key:
        candidates = await _gemini_generate_script(topic)

    if not candidates:
        logger.warning("Using template fallback script")
        candidates = [_template_script(topic)]

    # Validate duration constraint
    valid = [
        c for c in candidates
        if settings.video_min_duration <= c.estimated_duration_seconds <= settings.video_max_duration + 5
    ]
    if not valid:
        valid = candidates  # accept best even if slightly out of range

    best = valid[0]

    logger.info(
        "Selected script: %d words / ~%.0fs / score=%.2f",
        best.word_count,
        best.estimated_duration_seconds,
        best.total_score,
    )

    return Script(
        topic_id=topic.id,
        hook=best.hook,
        script=best.full_script,
        word_count=best.word_count,
        estimated_duration=best.estimated_duration_seconds,
        language="en",
        clarity_score=best.clarity_score,
        originality_score=best.originality_score,
        retention_score=best.retention_score,
        verification_status="ai_generated",
    )
