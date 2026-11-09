"""
app/research/trend_analyzer.py — Score topics for audience appeal and freshness.

Scoring weights (all configurable):
  curiosity_factor     20%
  storytelling_potential 20%
  audience_relevance   15%
  visual_potential     15%
  source_reliability   20%
  freshness            10%
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from dataclasses import dataclass

from app.research.discovery import RawTopic
from app.logger import get_logger

logger = get_logger("research.trend_analyzer")

# Default scoring weights
SCORING_WEIGHTS = {
    "curiosity_factor": 0.20,
    "storytelling_potential": 0.20,
    "audience_relevance": 0.15,
    "visual_potential": 0.15,
    "source_reliability": 0.20,
    "freshness": 0.10,
}

# Keywords that boost curiosity score
CURIOSITY_KEYWORDS = [
    "mystery", "surprising", "shocking", "secret", "hidden", "discovered",
    "revealed", "unknown", "unprecedented", "bizarre", "strange", "weird",
    "counterintuitive", "unexpected", "fact", "truth", "actually", "myth",
    "first time", "never before", "breakthrough", "ancient", "forbidden",
    "lost", "forgotten", "rare", "impossible", "scientists found",
    "study shows", "research reveals", "experts say", "new evidence",
]

# Keywords that boost visual potential
VISUAL_KEYWORDS = [
    "image", "photo", "footage", "video", "artifact", "discovery",
    "place", "site", "structure", "creature", "animal", "space",
    "planet", "explosion", "event", "scene", "landscape", "building",
    "experiment", "animation", "brain", "data",
]

# Keywords that boost storytelling potential
STORY_KEYWORDS = [
    "story", "case", "incident", "accident", "mystery", "crime",
    "survival", "hero", "villain", "journey", "adventure", "tragedy",
    "triumph", "escape", "rescue", "battle", "war", "revolution",
    "discovery", "invention", "person", "man", "woman", "child",
]


@dataclass
class ScoredTopic:
    raw: RawTopic
    curiosity_score: float = 0.0
    storytelling_score: float = 0.0
    relevance_score: float = 0.0
    visual_score: float = 0.0
    reliability_score: float = 0.0
    freshness_score: float = 0.0
    total_score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "title": self.raw.title,
            "url": self.raw.url,
            "pillar": self.raw.pillar.value,
            "source": self.raw.source_name,
            "total_score": round(self.total_score, 3),
            "curiosity": round(self.curiosity_score, 3),
            "storytelling": round(self.storytelling_score, 3),
            "relevance": round(self.relevance_score, 3),
            "visual": round(self.visual_score, 3),
            "reliability": round(self.reliability_score, 3),
            "freshness": round(self.freshness_score, 3),
        }


def _keyword_density(text: str, keywords: list[str]) -> float:
    """Return 0–1 score based on how many keywords appear in text."""
    text_lower = text.lower()
    hits = sum(1 for kw in keywords if kw in text_lower)
    return min(hits / max(len(keywords) * 0.15, 1), 1.0)


def _freshness_score(published_at: datetime) -> float:
    """Score 1.0 if published today, decaying to ~0.3 after 30 days."""
    now = datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    age_hours = (now - published_at).total_seconds() / 3600
    # Exponential decay: score = e^(-age_hours/168) where 168h = 1 week
    import math
    return max(math.exp(-age_hours / 168), 0.1)


def _title_length_score(title: str) -> float:
    """Titles between 40–90 characters score highest."""
    length = len(title)
    if 40 <= length <= 90:
        return 1.0
    elif length < 40:
        return length / 40
    else:
        return max(0.5, 1 - (length - 90) / 100)


def score_topic(topic: RawTopic, weights: dict | None = None) -> ScoredTopic:
    """Compute composite score for a single raw topic."""
    if weights is None:
        weights = SCORING_WEIGHTS

    full_text = f"{topic.title} {topic.summary}"

    scored = ScoredTopic(raw=topic)

    # Curiosity (keyword density in title)
    curiosity_base = _keyword_density(topic.title, CURIOSITY_KEYWORDS)
    title_score = _title_length_score(topic.title)
    scored.curiosity_score = (curiosity_base * 0.7 + title_score * 0.3)

    # Storytelling (presence of narrative elements)
    scored.storytelling_score = _keyword_density(full_text, STORY_KEYWORDS)

    # Audience relevance (non-technical, broad interest topics score higher)
    jargon_re = re.compile(
        r"\b(algorithm|dataset|API|SDK|quarterly|earnings|stock|IPO|merger|FDA|regulatory)\b",
        re.IGNORECASE,
    )
    jargon_hits = len(jargon_re.findall(topic.title))
    scored.relevance_score = max(0.0, 1.0 - jargon_hits * 0.2)

    # Visual potential
    scored.visual_score = min(
        _keyword_density(full_text, VISUAL_KEYWORDS) * 1.5, 1.0
    )

    # Source reliability (direct from feed registry)
    scored.reliability_score = topic.source_reliability

    # Freshness
    scored.freshness_score = _freshness_score(topic.published_at)

    # Weighted total
    scored.total_score = (
        scored.curiosity_score * weights["curiosity_factor"]
        + scored.storytelling_score * weights["storytelling_potential"]
        + scored.relevance_score * weights["audience_relevance"]
        + scored.visual_score * weights["visual_potential"]
        + scored.reliability_score * weights["source_reliability"]
        + scored.freshness_score * weights["freshness"]
    )

    return scored


def rank_topics(
    topics: list[RawTopic],
    weights: dict | None = None,
    min_score: float = 0.30,
) -> list[ScoredTopic]:
    """Score and rank all topics, filtering below min_score."""
    scored = [score_topic(t, weights) for t in topics]
    scored = [s for s in scored if s.total_score >= min_score]
    scored.sort(key=lambda s: s.total_score, reverse=True)
    return scored
