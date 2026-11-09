"""
app/api/pipeline.py — Pipeline trigger & status endpoints.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Job, JobStatus, SessionLocal, get_db

router = APIRouter()


async def run_pipeline_once(job_id: str) -> None:
    """Background task wrapper that creates its own DB session."""
    from app.scheduler.daily_pipeline import DailyPipeline
    async with SessionLocal() as db:
        job = await db.get(Job, job_id)  # won't work with string pk — use query
        from sqlalchemy import select
        result = await db.execute(select(Job).where(Job.job_id == job_id))
        job = result.scalar_one_or_none()
        if job:
            pipeline = DailyPipeline(db, job)
            await pipeline.run()


@router.post("/run")
async def trigger_pipeline(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger the full daily pipeline."""
    job_id = str(uuid.uuid4())
    job = Job(job_id=job_id, job_type="daily_pipeline", status=JobStatus.PENDING, stage="SELECT_TOPIC", context={})
    db.add(job)
    await db.commit()

    background_tasks.add_task(run_pipeline_once, job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/status/{job_id}")
async def pipeline_status(job_id: str, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    result = await db.execute(select(Job).where(Job.job_id == job_id))
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job.job_id,
        "status": job.status,
        "stage": job.stage,
        "retry_count": job.retry_count,
        "error_message": job.error_message,
        "started_at": job.started_at,
        "completed_at": job.completed_at,
    }


@router.get("/jobs")
async def list_jobs(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    result = await db.execute(
        select(Job).order_by(Job.created_at.desc()).limit(20)
    )
    jobs = result.scalars().all()
    return [
        {
            "job_id": j.job_id,
            "type": j.job_type,
            "status": j.status,
            "stage": j.stage,
            "created_at": j.created_at,
        }
        for j in jobs
    ]
