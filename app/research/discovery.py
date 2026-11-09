"""
app/research/discovery.py — RSS feed collection and trend detection.

Collects candidate topics from reputable RSS feeds organised by content pillar.
No scraping; only official feeds and APIs.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import feedparser
import httpx

from app.config import settings
from app.database import ContentPillar
from app.logger import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger("research.discovery")

# ─── RSS Feed Registry ────────────────────────────────────────────────────────

FEED_REGISTRY: dict[ContentPillar, list[dict]] = {
    ContentPillar.PSYCHOLOGY: [
        {"url": "https://www.apa.org/news/feeds/index.aspx", "name": "APA News", "reliability": 0.95},
        {"url": "https://www.psychologytoday.com/us/articles/rss", "name": "Psychology Today", "reliability": 0.80},
        {"url": "https://feeds.feedburner.com/PsychCentral", "name": "PsychCentral", "reliability": 0.75},
    ],
    ContentPillar.HISTORY: [
        {"url": "https://www.smithsonianmag.com/rss/history/", "name": "Smithsonian History", "reliability": 0.90},
        {"url": "https://www.history.com/rss", "name": "History Channel", "reliability": 0.80},
        {"url": "https://www.archaeology.org/feed", "name": "Archaeology", "reliability": 0.90},
        {"url": "https://www.nationalgeographic.com/history/rss", "name": "NatGeo History", "reliability": 0.90},
        {"url": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "name": "BBC Science", "reliability": 0.90},
        {"url": "https://www.ancient-origins.net/rss.xml", "name": "Ancient Origins", "reliability": 0.70},
        {"url": "https://historydaily.org/feed", "name": "History Daily", "reliability": 0.70},
    ],
    ContentPillar.TECHNOLOGY: [
        {"url": "https://feeds.arstechnica.com/arstechnica/index/", "name": "Ars Technica", "reliability": 0.90},
        {"url": "https://www.wired.com/feed/rss", "name": "Wired", "reliability": 0.85},
        {"url": "https://techcrunch.com/feed/", "name": "TechCrunch", "reliability": 0.82},
        {"url": "https://www.technologyreview.com/stories.rss", "name": "MIT Tech Review", "reliability": 0.95},
        {"url": "https://feeds.a.dj.com/rss/RSSWSJD.xml", "name": "WSJ Tech", "reliability": 0.88},
        {"url": "https://spectrum.ieee.org/feeds/feed.rss", "name": "IEEE Spectrum", "reliability": 0.95},
        {"url": "https://venturebeat.com/feed/", "name": "VentureBeat", "reliability": 0.78},
    ],
    # General-science feeds live here, not under PSYCHOLOGY — otherwise physics
    # stories get psychology-framed scripts with invented "brain" angles.
    ContentPillar.SCIENCE: [
        {"url": "https://www.scientificamerican.com/rss/", "name": "Scientific American", "reliability": 0.90},
        {"url": "https://www.newscientist.com/feed/home/", "name": "New Scientist", "reliability": 0.88},
        {"url": "https://feeds.nature.com/NatureMostRead", "name": "Nature", "reliability": 0.95},
        {"url": "https://rss.sciencemag.org/rss/current.xml", "name": "Science Magazine", "reliability": 0.95},
    ],
    ContentPillar.STORYTELLING: [
        {"url": "https://feeds.bbci.co.uk/news/world/rss.xml", "name": "BBC World", "reliability": 0.90},
        {"url": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "name": "NYT World", "reliability": 0.90},
        {"url": "https://www.theguardian.com/world/rss", "name": "Guardian World", "reliability": 0.88},
        {"url": "https://www.atlasobscura.com/feeds/latest", "name": "Atlas Obscura", "reliability": 0.82},
        {"url": "https://www.mentalfloss.com/feeds/all", "name": "Mental Floss", "reliability": 0.78},
    ],
}


@dataclass
class RawTopic:
    """A candidate topic collected from a feed before scoring."""
    title: str
    summary: str
    url: str
    source_name: str
    published_at: datetime
    pillar: ContentPillar
    source_reliability: float
    content_hash: str = field(init=False)

    def __post_init__(self):
        self.content_hash = hashlib.sha256(
            f"{self.title}{self.url}".encode()
        ).hexdigest()


async def fetch_feed(
    feed_url: str,
    source_name: str,
    pillar: ContentPillar,
    reliability: float,
    client: httpx.AsyncClient,
    max_entries: int = 10,
) -> list[RawTopic]:
    """Download and parse a single RSS feed."""
    topics: list[RawTopic] = []
    try:
        resp = await client.get(feed_url, timeout=15.0)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)

        for entry in feed.entries[:max_entries]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            url = entry.get("link", "").strip()

            if not title or not url:
                continue

            # Parse date
            published_at = datetime.now(timezone.utc)
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                import time
                published_at = datetime.fromtimestamp(
                    time.mktime(entry.published_parsed), tz=timezone.utc
                )

            topics.append(
                RawTopic(
                    title=title,
                    summary=summary[:1000],
                    url=url,
                    source_name=source_name,
                    published_at=published_at,
                    pillar=pillar,
                    source_reliability=reliability,
                )
            )
    except Exception as exc:
        logger.warning("Feed %s failed: %s", feed_url, exc)

    return topics


async def collect_all_topics(entries_per_feed: int = 10) -> list[RawTopic]:
    """Collect topics from all registered feeds."""
    all_topics: list[RawTopic] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "FacelessAI/1.0 (+research-bot)"},
        follow_redirects=True,
    ) as client:
        for pillar, feeds in FEED_REGISTRY.items():
            for feed_cfg in feeds:
                topics = await fetch_feed(
                    feed_url=feed_cfg["url"],
                    source_name=feed_cfg["name"],
                    pillar=pillar,
                    reliability=feed_cfg["reliability"],
                    client=client,
                    max_entries=entries_per_feed,
                )
                logger.info(
                    "Collected %d topics from %s", len(topics), feed_cfg["name"]
                )
                all_topics.extend(topics)

    logger.info("Total raw candidates collected: %d", len(all_topics))
    return all_topics
