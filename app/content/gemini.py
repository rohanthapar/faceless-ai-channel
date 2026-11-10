"""
app/content/gemini.py — Shared Gemini call with retry + model fallback.

Gemini frequently returns 503 UNAVAILABLE ("model is currently overloaded").
Without retries every content stage silently drops to its template fallback,
which produced headline-as-title and template scripts in production.
"""
from __future__ import annotations

import asyncio

from app.config import settings
from app.logger import get_logger

logger = get_logger("content.gemini")

# Primary first; fallbacks are only used when the one before stays overloaded.
# Newest models get the demand spikes — older ones often still answer (seen
# 2026-11-10: 3.8 and 3.7 both 503 while 3.6 and 3.5 replied in seconds).
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-3.7-flash", "gemini-3.6-flash", "gemini-3.5-flash"]

_TRANSIENT = ("503", "UNAVAILABLE", "overloaded", "429", "RESOURCE_EXHAUSTED")

# Gemini sometimes accepts a request and never answers; without a cap one hung
# call stalls the whole daily run (seen: 13+ minutes on a single script call).
CALL_TIMEOUT_SECONDS = 180


async def generate_text(prompt, attempts_per_model: int = 3) -> str:
    """Return Gemini's text response, retrying transient errors across models.

    `prompt` is a string, or a list of parts (e.g. an uploaded video file + text).
    """
    from google import genai

    client = genai.Client(api_key=settings.gemini_api_key)
    last_exc: Exception | None = None

    for model in GEMINI_MODELS:
        for attempt in range(attempts_per_model):
            try:
                response = await asyncio.wait_for(
                    asyncio.to_thread(client.models.generate_content, model=model, contents=prompt),
                    timeout=CALL_TIMEOUT_SECONDS,
                )
                return response.text.strip()
            except asyncio.TimeoutError as exc:
                last_exc = exc
                logger.warning("Gemini %s no response after %ds (attempt %d) — retrying",
                               model, CALL_TIMEOUT_SECONDS, attempt + 1)
                continue
            except Exception as exc:
                if not any(t in str(exc) for t in _TRANSIENT):
                    raise
                last_exc = exc
                delay = 20 * (attempt + 1)
                logger.warning("Gemini %s busy (attempt %d) — retrying in %ds", model, attempt + 1, delay)
                await asyncio.sleep(delay)
        logger.warning("Gemini %s still unavailable — trying next model", model)

    raise last_exc or RuntimeError("Gemini unavailable")
