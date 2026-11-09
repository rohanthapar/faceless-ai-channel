"""
app/api/pipeline.py — Pipeline trigger & status endpoints.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Job, JobStatus, get_db
from app.scheduler.job_manager import JobManager

router = APIRouter()


@router.post("/run")
async def trigger_pipeline(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger the full daily pipeline."""
    job_id = str(uuid.uuid4())
    job = Job(job_id=job_id, job_type="daily_pipeline", status=JobStatus.PENDING)
    db.add(job)
    await db.commit()

    from app.scheduler.daily_pipeline import run_pipeline_once
    background_tasks.add_task(run_pipeline_once, job_id)
    return {"job_id": job_id, "status": "queued"}


@router.get("/status/{job_id}")
async def pipeline_status(job_id: str, db: AsyncSession = Depends(get_db)):
    job = await JobManager.get_job(db, job_id)
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
    jobs = await JobManager.list_recent(db, limit=20)
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
