"""
app/api/videos.py — Video listing and download endpoints.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Video, get_db

router = APIRouter()


class VideoOut(BaseModel):
    id: int
    topic_id: int
    render_path: str | None
    thumbnail_path: str | None
    duration: float | None
    quality_status: str

    model_config = {"from_attributes": True}


@router.get("/", response_model=list[VideoOut])
async def list_videos(limit: int = 20, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Video).order_by(Video.created_at.desc()).limit(limit)
    )
    return result.scalars().all()


@router.get("/{video_id}", response_model=VideoOut)
async def get_video(video_id: int, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, video_id)
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.get("/{video_id}/download")
async def download_video(video_id: int, db: AsyncSession = Depends(get_db)):
    video = await db.get(Video, video_id)
    if not video or not video.render_path:
        raise HTTPException(status_code=404, detail="Video file not found")
    path = Path(video.render_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")
    return FileResponse(str(path), media_type="video/mp4", filename=path.name)
