"""
app/api/topics.py — Topic CRUD endpoints.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Topic, TopicStatus, get_db

router = APIRouter()


class TopicOut(BaseModel):
    id: int
    title: str
    pillar: str
    status: str
    reliability_score: float
    trend_score: float
    total_score: float

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[TopicOut])
async def list_topics(
    status: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    q = select(Topic).order_by(Topic.created_at.desc()).limit(limit)
    if status:
        try:
            s = TopicStatus(status)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
        q = q.where(Topic.status == s)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{topic_id}", response_model=TopicOut)
async def get_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    topic = await db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    return topic


@router.delete("/{topic_id}")
async def reject_topic(topic_id: int, db: AsyncSession = Depends(get_db)):
    topic = await db.get(Topic, topic_id)
    if not topic:
        raise HTTPException(status_code=404, detail="Topic not found")
    topic.status = TopicStatus.REJECTED
    await db.commit()
    return {"detail": "rejected"}
