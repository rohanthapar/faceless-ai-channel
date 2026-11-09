"""
app/api/analytics.py — Analytics summary endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Analytics, Publication, get_db

router = APIRouter()


@router.get("/summary")
async def analytics_summary(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(
            func.sum(Analytics.views).label("total_views"),
            func.sum(Analytics.likes).label("total_likes"),
            func.sum(Analytics.comments).label("total_comments"),
            func.avg(Analytics.engagement_rate).label("avg_engagement"),
        )
    )
    row = result.one()
    return {
        "total_views": row.total_views or 0,
        "total_likes": row.total_likes or 0,
        "total_comments": row.total_comments or 0,
        "avg_engagement_rate": round(row.avg_engagement or 0, 4),
    }


@router.get("/publication/{publication_id}")
async def publication_analytics(
    publication_id: int, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(Analytics)
        .where(Analytics.publication_id == publication_id)
        .order_by(Analytics.captured_at.desc())
    )
    records = result.scalars().all()
    return [
        {
            "snapshot_type": r.snapshot_type,
            "views": r.views,
            "likes": r.likes,
            "comments": r.comments,
            "engagement_rate": r.engagement_rate,
            "captured_at": r.captured_at,
        }
        for r in records
    ]
