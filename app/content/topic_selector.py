"""
app/content/topic_selector.py — Selects the best eligible topic for today's video.

Considers:
- Pillar distribution targets vs recent history
- Deduplication against previously used topics
- Minimum score threshold
- Pillar rebalancing based on analytics performance
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import ContentPillar, Topic, TopicStatus
from app.logger import get_logger
from app.research.discovery import RawTopic, collect_all_topics
from app.research.fact_checker import check_topic_reliability
from app.research.trend_analyzer import ScoredTopic, rank_topics

logger = get_logger("content.topic_selector")


def _pillar_targets(settings_obj=None) -> dict[ContentPillar, float]:
    """Return pillar distribution targets from configuration."""
    s = settings_obj or settings
    return {
        ContentPillar.PSYCHOLOGY: s.pillar_psychology_pct / 100,
        ContentPillar.HISTORY: s.pillar_history_pct / 100,
        ContentPillar.TECHNOLOGY: s.pillar_technology_pct / 100,
        ContentPillar.STORYTELLING: s.pillar_storytelling_pct / 100,
    }


async def _get_recent_pillar_counts(
    db: AsyncSession,
    days: int = 30,
) -> dict[ContentPillar, int]:
    """Count videos published per pillar in the past N days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(Topic.pillar, func.count(Topic.id))
        .where(Topic.status.in_([TopicStatus.PUBLISHED, TopicStatus.USED]))
        .where(Topic.created_at >= cutoff)
        .group_by(Topic.pillar)
    )
    counts = {ContentPillar(row[0]): row[1] for row in result.fetchall()}
    # Ensure all pillars present
    for pillar in ContentPillar:
        counts.setdefault(pillar, 0)
    return counts


async def _get_used_titles(db: AsyncSession) -> set[str]:
    """Return lowercase titles of previously used topics for dedup."""
    result = await db.execute(
        select(Topic.title).where(
            Topic.status.in_([TopicStatus.USED, TopicStatus.PUBLISHED, TopicStatus.PRODUCED])
        )
    )
    return {row[0].lower() for row in result.fetchall()}


def _choose_target_pillar(
    targets: dict[ContentPillar, float],
    recent_counts: dict[ContentPillar, int],
) -> ContentPillar:
    """
    Choose the pillar most underrepresented relative to its target.
    Uses softmax weighting so we don't always pick the same pillar.
    """
    total = sum(recent_counts.values()) or 1
    deficit = {
        pillar: targets[pillar] - (recent_counts[pillar] / total)
        for pillar in ContentPillar
    }
    # Weighted random selection biased toward largest deficit
    import math
    weights = {p: math.exp(d * 5) for p, d in deficit.items()}
    total_weight = sum(weights.values())
    r = random.random() * total_weight
    cumulative = 0.0
    for pillar, weight in weights.items():
        cumulative += weight
        if r <= cumulative:
            return pillar
    return max(deficit, key=lambda p: deficit[p])


async def _save_topic_to_db(
    db: AsyncSession,
    scored: ScoredTopic,
    reliability_score: float,
    verification_notes: str,
    verified_claims: list[str],
) -> Topic:
    """Persist a scored topic candidate to the database."""
    topic = Topic(
        title=scored.raw.title,
        summary=scored.raw.summary,
        pillar=scored.raw.pillar,
        source_urls=[scored.raw.url],
        source_dates=[scored.raw.published_at.isoformat()],
        factual_claims=verified_claims,
        verification_notes=verification_notes,
        reliability_score=reliability_score,
        trend_score=scored.total_score,
        total_score=(scored.total_score + reliability_score) / 2,
        status=TopicStatus.CANDIDATE,
    )
    db.add(topic)
    await db.commit()
    await db.refresh(topic)
    return topic


async def select_daily_topic(db: AsyncSession) -> Topic | None:
    """
    Main entry point: collect, score, verify and select today's topic.
    Returns the selected Topic ORM instance, or None if none qualify.
    """
    logger.info("Starting topic discovery and selection...")

    # Collect raw topics from feeds
    raw_topics = await collect_all_topics(entries_per_feed=10)
    if not raw_topics:
        logger.error("No topics collected from any feed")
        return None

    # Score and filter
    scored_topics = rank_topics(raw_topics, min_score=0.30)
    logger.info("Ranked %d topics (from %d raw)", len(scored_topics), len(raw_topics))

    if not scored_topics:
        logger.error("No topics passed minimum score threshold")
        return None

    # Get dedup set
    used_titles = await _get_used_titles(db)

    # Get pillar targets and recent distribution
    targets = _pillar_targets()
    recent_counts = await _get_recent_pillar_counts(db)
    target_pillar = _choose_target_pillar(targets, recent_counts)
    logger.info("Target pillar for today: %s", target_pillar.value)

    # Filter: prefer target pillar, but fall back to best overall
    pillar_topics = [s for s in scored_topics if s.raw.pillar == target_pillar]
    candidates = pillar_topics if pillar_topics else scored_topics

    # Remove already-used topics (fuzzy dedup by title similarity)
    def _is_duplicate(title: str) -> bool:
        t_lower = title.lower()
        if t_lower in used_titles:
            return True
        # Simple substring dedup for near-duplicates
        for used in used_titles:
            if len(used) > 10 and (used in t_lower or t_lower in used):
                return True
        return False

    candidates = [c for c in candidates if not _is_duplicate(c.raw.title)]

    if not candidates:
        logger.warning("All candidates were duplicates; expanding to other pillars")
        candidates = [s for s in scored_topics if not _is_duplicate(s.raw.title)]

    if not candidates:
        logger.error("No fresh candidates available after deduplication")
        return None

    # Fact-check top 5 candidates, pick first that passes
    for scored in candidates[:5]:
        logger.info("Fact-checking: %s", scored.raw.title[:80])
        result = await check_topic_reliability(
            title=scored.raw.title,
            summary=scored.raw.summary,
            source_url=scored.raw.url,
            source_reliability=scored.raw.source_reliability,
        )
        if result.is_reliable:
            topic = await _save_topic_to_db(
                db=db,
                scored=scored,
                reliability_score=result.reliability_score,
                verification_notes=result.notes,
                verified_claims=result.verified_claims,
            )
            # Mark selected
            topic.status = TopicStatus.SELECTED
            await db.commit()
            logger.info("✅ Selected topic [id=%d]: %s", topic.id, topic.title)
            return topic
        else:
            logger.info("Topic rejected by fact check: %s", result.notes)

    logger.error("No topics passed fact-checking")
    return None
