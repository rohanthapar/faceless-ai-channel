"""
app/analytics/performance.py — Collect and analyse video performance analytics.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import Analytics, Publication, PublicationStatus
from app.logger import get_logger

logger = get_logger("analytics.performance")


async def collect_youtube_analytics(publication: Publication, db: AsyncSession) -> Analytics | None:
    """Collect YouTube video analytics using Data API v3."""
    if not publication.platform_video_id:
        return None

    try:
        import pickle
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        token_file = settings.youtube_token_file
        if not token_file.exists():
            logger.warning("YouTube token not found — cannot collect analytics")
            return None

        with token_file.open("rb") as f:
            creds = pickle.load(f)

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())

        loop = asyncio.get_event_loop()

        def _fetch():
            youtube = build("youtube", "v3", credentials=creds)
            resp = youtube.videos().list(
                part="statistics,contentDetails",
                id=publication.platform_video_id,
            ).execute()
            return resp

        response = await loop.run_in_executor(None, _fetch)
        items = response.get("items", [])
        if not items:
            return None

        stats = items[0].get("statistics", {})
        snapshot = "24h"  # We'll infer based on when we call this

        analytic = Analytics(
            publication_id=publication.id,
            snapshot_type=snapshot,
            views=int(stats.get("viewCount", 0)),
            likes=int(stats.get("likeCount", 0)),
            comments=int(stats.get("commentCount", 0)),
            shares=None,  # YouTube API doesn't expose share count
            watch_time_seconds=None,  # Requires YouTube Analytics API (extra OAuth scope)
            avg_view_duration=None,
            avg_view_percentage=None,
            engagement_rate=None,
        )

        # Calculate engagement rate if possible
        if analytic.views and analytic.views > 0:
            engagements = (analytic.likes or 0) + (analytic.comments or 0)
            analytic.engagement_rate = engagements / analytic.views

        db.add(analytic)
        await db.commit()
        logger.info(
            "YouTube analytics: %d views / %d likes / %d comments",
            analytic.views or 0,
            analytic.likes or 0,
            analytic.comments or 0,
        )
        return analytic

    except Exception as exc:
        logger.warning("YouTube analytics collection failed: %s", exc)
        return None


async def collect_instagram_analytics(publication: Publication, db: AsyncSession) -> Analytics | None:
    """Collect Instagram media insights via Meta Graph API."""
    if not publication.platform_video_id or not settings.instagram_access_token:
        return None

    try:
        async with httpx.AsyncClient() as client:
            # Basic media stats
            resp = await client.get(
                f"https://graph.instagram.com/v21.0/{publication.platform_video_id}",
                params={
                    "access_token": settings.instagram_access_token,
                    "fields": "like_count,comments_count,plays",
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                logger.warning("Instagram analytics API returned %d", resp.status_code)
                return None

            data = resp.json()

        analytic = Analytics(
            publication_id=publication.id,
            snapshot_type="24h",
            views=data.get("plays"),
            likes=data.get("like_count"),
            comments=data.get("comments_count"),
            shares=None,  # Not available via basic API
            watch_time_seconds=None,
            avg_view_duration=None,
            avg_view_percentage=None,
            engagement_rate=None,
        )

        if analytic.views and analytic.views > 0:
            engagements = (analytic.likes or 0) + (analytic.comments or 0)
            analytic.engagement_rate = engagements / analytic.views

        db.add(analytic)
        await db.commit()
        logger.info(
            "Instagram analytics: %s plays / %s likes",
            analytic.views,
            analytic.likes,
        )
        return analytic

    except Exception as exc:
        logger.warning("Instagram analytics collection failed: %s", exc)
        return None


async def collect_all_analytics(db: AsyncSession) -> None:
    """Collect analytics for all published videos."""
    result = await db.execute(
        select(Publication).where(
            Publication.status == PublicationStatus.PUBLISHED,
            Publication.platform_video_id.isnot(None),
        )
    )
    publications = result.scalars().all()

    logger.info("Collecting analytics for %d publications...", len(publications))

    for pub in publications:
        if pub.platform == "youtube":
            await collect_youtube_analytics(pub, db)
        elif pub.platform == "instagram":
            await collect_instagram_analytics(pub, db)

        await asyncio.sleep(1)  # Rate limit protection


async def generate_weekly_report(db: AsyncSession) -> dict:
    """Generate a weekly performance summary."""
    from datetime import timedelta
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    result = await db.execute(
        select(
            Analytics.publication_id,
            func.sum(Analytics.views).label("total_views"),
            func.sum(Analytics.likes).label("total_likes"),
            func.avg(Analytics.engagement_rate).label("avg_engagement"),
        )
        .where(Analytics.captured_at >= cutoff)
        .group_by(Analytics.publication_id)
    )

    rows = result.fetchall()
    total_views = sum(r.total_views or 0 for r in rows)
    avg_engagement = sum(r.avg_engagement or 0 for r in rows) / max(len(rows), 1)

    report = {
        "period": "7 days",
        "videos_published": len(rows),
        "total_views": total_views,
        "avg_views_per_video": total_views // max(len(rows), 1),
        "avg_engagement_rate": round(avg_engagement * 100, 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    logger.info("Weekly report: %s", report)
    return report
