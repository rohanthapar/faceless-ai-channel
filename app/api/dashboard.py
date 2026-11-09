"""
app/api/dashboard.py — HTML dashboard router (Jinja2).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    Analytics,
    Job,
    JobStatus,
    Publication,
    PublicationStatus,
    Topic,
    TopicStatus,
    Video,
    get_db,
)

router = APIRouter()

_TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATE_DIR))


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    # Counts
    total_topics = (await db.scalar(select(func.count(Topic.id)))) or 0
    total_videos = (await db.scalar(select(func.count(Video.id)))) or 0
    published = (
        await db.scalar(
            select(func.count(Publication.id)).where(
                Publication.status == PublicationStatus.PUBLISHED
            )
        )
    ) or 0
    running_jobs = (
        await db.scalar(
            select(func.count(Job.id)).where(Job.status == JobStatus.RUNNING)
        )
    ) or 0

    # Recent topics
    result = await db.execute(
        select(Topic).order_by(Topic.created_at.desc()).limit(10)
    )
    recent_topics = result.scalars().all()

    # Recent videos
    result = await db.execute(
        select(Video).order_by(Video.created_at.desc()).limit(10)
    )
    recent_videos = result.scalars().all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "total_topics": total_topics,
            "total_videos": total_videos,
            "published": published,
            "running_jobs": running_jobs,
            "recent_topics": recent_topics,
            "recent_videos": recent_videos,
        },
    )
