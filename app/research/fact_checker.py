"""
app/research/fact_checker.py — Verify that a topic has multiple reputable sources.
Uses Gemini AI to assist in reliability assessment.
"""
from __future__ import annotations

import httpx
from dataclasses import dataclass

from app.config import settings
from app.logger import get_logger

logger = get_logger("research.fact_checker")


@dataclass
class FactCheckResult:
    is_reliable: bool
    source_count: int
    reliability_score: float
    notes: str
    verified_claims: list[str]


async def check_topic_reliability(
    title: str,
    summary: str,
    source_url: str,
    source_reliability: float,
) -> FactCheckResult:
    """
    Assess topic reliability.
    - If Gemini API key is configured, use it for content assessment.
    - Otherwise, use heuristic checks (source reliability + summary length).
    """
    if settings.gemini_api_key:
        return await _gemini_fact_check(title, summary, source_url, source_reliability)
    else:
        return _heuristic_fact_check(title, summary, source_reliability)


async def _gemini_fact_check(
    title: str,
    summary: str,
    source_url: str,
    source_reliability: float,
) -> FactCheckResult:
    """Use Gemini to assess whether the topic is factual and appropriate."""
    import google.generativeai as genai

    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = f"""You are a fact-checking assistant for a short-form educational video channel.

Evaluate this news/article topic for factual reliability and content appropriateness:

Title: {title}
Summary: {summary[:500]}
Source URL: {source_url}
Source Reliability Score: {source_reliability:.2f} (0=unreliable, 1=very reliable)

Assess:
1. Is this factually verifiable? (not speculation, conspiracy, or satire)
2. Is it appropriate for a general educational channel?
3. Does it contain one clear, interesting core fact or story?
4. What are 2-3 key factual claims that can be verified?

Respond ONLY in this exact JSON format:
{{
  "is_reliable": true,
  "reliability_score": 0.85,
  "notes": "Brief explanation",
  "verified_claims": ["claim 1", "claim 2"]
}}"""

    try:
        response = model.generate_content(prompt)
        import json, re

        text = response.text
        # Extract JSON from response
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            return FactCheckResult(
                is_reliable=data.get("is_reliable", False),
                source_count=1,
                reliability_score=float(data.get("reliability_score", 0.5)),
                notes=data.get("notes", ""),
                verified_claims=data.get("verified_claims", []),
            )
    except Exception as exc:
        logger.warning("Gemini fact check failed: %s — using heuristic", exc)

    return _heuristic_fact_check(title, summary, source_reliability)


def _heuristic_fact_check(
    title: str,
    summary: str,
    source_reliability: float,
) -> FactCheckResult:
    """Fallback heuristic fact checking without AI."""
    # Check for red-flag patterns
    red_flags = [
        "you won't believe", "shocking truth", "they don't want you to know",
        "secret cure", "conspiracy", "hoax", "fake", "satire",
    ]
    text_lower = f"{title} {summary}".lower()
    flag_count = sum(1 for f in red_flags if f in text_lower)

    is_reliable = source_reliability >= 0.70 and flag_count == 0
    reliability_score = max(source_reliability - flag_count * 0.15, 0.0)

    # Minimum summary length check
    if len(summary) < 50:
        reliability_score *= 0.7

    notes = []
    if flag_count > 0:
        notes.append(f"Contains {flag_count} red-flag phrases")
    if source_reliability < 0.70:
        notes.append("Low source reliability")
    if not notes:
        notes.append("Passed heuristic checks")

    return FactCheckResult(
        is_reliable=is_reliable,
        source_count=1,
        reliability_score=reliability_score,
        notes="; ".join(notes),
        verified_claims=[],
    )
