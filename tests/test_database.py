"""
tests/test_database.py — ORM model smoke-tests.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.database import ContentPillar, Job, JobStatus, Topic, TopicStatus


@pytest.mark.asyncio
async def test_create_topic(db_session):
    topic = Topic(
        title="10 Mind-Blowing Psychology Facts",
        pillar=ContentPillar.PSYCHOLOGY,
        reliability_score=0.8,
        trend_score=0.7,
        total_score=0.75,
        source_urls=["https://example.com"],
    )
    db_session.add(topic)
    await db_session.commit()
    await db_session.refresh(topic)

    assert topic.id is not None
    assert topic.status == TopicStatus.CANDIDATE


@pytest.mark.asyncio
async def test_create_job(db_session):
    job = Job(
        job_id="test-job-001",
        job_type="daily_pipeline",
        status=JobStatus.PENDING,
    )
    db_session.add(job)
    await db_session.commit()

    result = await db_session.execute(select(Job).where(Job.job_id == "test-job-001"))
    fetched = result.scalar_one()
    assert fetched.status == JobStatus.PENDING
    assert fetched.retry_count == 0
