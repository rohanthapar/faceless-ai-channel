"""
app/content/scriptwriter.py — Generate original short-form scripts using Gemini AI.

Generates 2 script variants, scores them, and returns the best.
Falls back to a topic-aware template if the Gemini API is unavailable.
"""
from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass

from app.config import settings
from app.content.gemini import generate_text
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
        "Prioritise narrative tension and resolution."
    ),
    ContentPillar.SCIENCE: (
        "Focus on space, nature, animals, the human body and physics. Lead with the "
        "most jaw-dropping concrete detail and make scale tangible with comparisons."
    ),
}


def _words_per_minute() -> float:
    return 148.0  # measured TTS pace at +10% rate (135 wpm at +0%)


def _ensure_complete_ending(text: str) -> str:
    """Drop a trailing unfinished sentence ("...the panic where...").

    Trailing-off "loop" endings sound like the video was cut off — the AI
    reviewer flagged it and viewers would too.
    """
    text = text.strip()
    if re.search(r"(?<!\.)[.!?][\"')\]]*$", text):
        return text
    sentences = re.split(r"(?<=[.!?])\s+", re.sub(r"(\.\.\.|…)$", "", text))
    *complete, last = sentences
    # Prefer cutting the unfinished sentence back to its last complete clause
    if "," in last:
        clause = last.rsplit(",", 1)[0].strip()
        if len(clause.split()) >= 6:
            return " ".join([*complete, clause + "."])
    if complete and len(" ".join(complete).split()) >= 40:
        return " ".join(complete)
    return re.sub(r"[\s.,;:…-]+$", "", text) + "."


def _estimate_duration(text: str) -> float:
    words = len(text.split())
    return (words / _words_per_minute()) * 60


def _hook_score(hook: str) -> float:
    curiosity_words = ["what if", "why", "how", "secret", "truth", "actually",
                       "never", "nobody", "impossible", "?"]
    score = sum(1 for w in curiosity_words if w in hook.lower()) * 0.3
    words = len(hook.split())
    if 3 <= words <= 7:  # on-screen hook text must be readable in ~1 second
        score += 1.0
    return score


async def _gemini_generate_script(topic: Topic) -> list[ScriptCandidate]:
    """Use Gemini to generate script candidates via the google-genai SDK."""
    try:
        from google import genai
    except ImportError:
        logger.warning("google-genai not installed — skipping Gemini script")
        return []

    guidance = PILLAR_GUIDANCE.get(topic.pillar, "")

    prompt = f"""You are a scriptwriter for a faceless short-form video channel (YouTube Shorts).

Topic: {topic.title}
Summary: {(topic.summary or topic.title)[:400]}
Content pillar: {topic.pillar.value}
Pillar guidance: {guidance}

Requirements:
- Write for a 20-30 second video at narration pace (~148 words/minute)
- Target 55-70 spoken words total — never more than 70
- Structure: Hook → 2-3 punchy beats that escalate → twist/payoff line
- The FIRST sentence is the spoken hook: max 12 words, states the most surprising
  part immediately (no slow build-up, no "Did you know", no "Imagine")
- The LAST sentence must be a COMPLETE sentence ending in a full stop — never trail
  off with "...", "where", "which" or an unfinished clause. It should land the payoff
  and naturally invite a rewatch of the opening line.
- No "follow for more", "subscribe", "Hey guys", "In today's video" phrases
- Natural conversational English — short sentences, not formal or robotic
- One clear central idea
- Only write facts that are plausibly true — do not invent statistics
- Stay faithful to the topic and summary above. Do NOT invent a different angle,
  named concepts, or claims that are not supported by the source. If the pillar
  guidance doesn't fit the topic, follow the topic, not the guidance.
- Make every word count — no filler, no padding

Generate exactly:
1. THREE on-screen hook texts shown in big letters on the first frame
   (3-7 words each, punchy, creates a curiosity gap, not a copy of the first sentence)
2. TWO complete scripts (each 55-70 words)
3. Score each script 0.0-1.0 on: clarity, originality, retention_potential

Respond ONLY in this exact JSON format with no markdown:
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
        raw = await generate_text(prompt)

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)

        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            raise ValueError(f"No JSON found in Gemini response: {raw[:200]}")

        data = json.loads(json_match.group())
        hooks: list[str] = data.get("hooks", [])
        scripts_data: list[dict] = data.get("scripts", [])

        if not hooks or not scripts_data:
            raise ValueError("Missing hooks or scripts in Gemini response")

        best_hook = max(hooks, key=_hook_score) if len(hooks) > 1 else hooks[0]

        candidates: list[ScriptCandidate] = []
        for s in scripts_data:
            text_body = _ensure_complete_ending(s.get("text", "").strip())
            if not text_body or len(text_body.split()) < 20:
                continue
            wc = len(text_body.split())
            dur = _estimate_duration(text_body)
            clarity = float(s.get("clarity", 0.7))
            originality = float(s.get("originality", 0.7))
            retention = float(s.get("retention", 0.7))
            total = (clarity + originality + retention) / 3
            candidates.append(ScriptCandidate(
                hook=best_hook,
                full_script=text_body,
                word_count=wc,
                estimated_duration_seconds=dur,
                clarity_score=clarity,
                originality_score=originality,
                retention_score=retention,
                total_score=total,
            ))

        candidates.sort(key=lambda c: c.total_score, reverse=True)
        logger.info("Gemini returned %d script candidates", len(candidates))
        return candidates

    except Exception as exc:
        logger.warning("Gemini script generation failed: %s — will use template", exc)
        return []


def _template_script(topic: Topic) -> ScriptCandidate:
    """Topic-aware template fallback — produces sensible content from title + summary."""
    title = topic.title.strip()
    summary = (topic.summary or "").strip()

    # Build hook from title
    title_lower = title.lower()
    if title_lower.startswith(("why", "how", "what", "when", "where")):
        hook = f"{title} — and the answer might surprise you."
    elif any(d.isdigit() for d in title):
        hook = f"Here are {title} you've probably never heard of."
    else:
        hook = f"Most people have no idea that {title_lower}."

    # Build body from summary (or paraphrase the title)
    if summary and len(summary.split()) >= 15:
        # Take first 2 sentences of summary as body
        sentences = re.split(r'(?<=[.!?])\s+', summary)
        body = " ".join(sentences[:2])
        body = body[:300]
    else:
        body = (
            f"{title} is one of the most fascinating but overlooked subjects in its field. "
            f"Research shows this affects nearly everyone, yet few people realise it's happening."
        )

    cta = "Follow for one mind-bending fact every day."

    script = f"{hook} {body} {cta}"

    # Trim to target length
    words = script.split()
    if len(words) > 68:
        script = " ".join(words[:66]) + "."

    return ScriptCandidate(
        hook=hook,
        full_script=script,
        word_count=len(script.split()),
        estimated_duration_seconds=_estimate_duration(script),
        clarity_score=0.70,
        originality_score=0.55,
        retention_score=0.65,
        total_score=0.63,
    )


async def generate_script(topic: Topic) -> Script | None:
    """
    Generate, score and select the best script for a topic.
    Returns a Script ORM instance (not yet saved — caller saves).
    """
    if not topic.title:
        logger.error("Topic has no title")
        return None

    logger.info("Generating script for: %s", topic.title[:80])

    candidates: list[ScriptCandidate] = []

    if settings.gemini_api_key:
        candidates = await _gemini_generate_script(topic)

    if not candidates and settings.gemini_api_key:
        # The template script reads as filler and won't pass review; failing lets
        # the next scheduled slot retry with Gemini instead of shipping it
        logger.error("Gemini script generation failed — not falling back to template")
        return None

    if not candidates:
        logger.warning("Using template fallback script for: %s", topic.title[:60])
        candidates = [_template_script(topic)]

    # Prefer scripts within duration window; accept best otherwise
    valid = [
        c for c in candidates
        if settings.video_min_duration <= c.estimated_duration_seconds <= settings.video_max_duration + 5
    ]
    best = (valid or candidates)[0]

    logger.info(
        "Selected script: %d words / ~%.0fs / score=%.2f\nPreview: %s",
        best.word_count,
        best.estimated_duration_seconds,
        best.total_score,
        best.full_script[:120],
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
