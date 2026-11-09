"""
tests/test_api.py — FastAPI endpoint integration tests.
Scheduler and pipeline are mocked so tests run without heavy optional deps.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Patch start_scheduler so APScheduler doesn't actually start during lifespan
_scheduler_patch = patch("app.scheduler.job_manager.start_scheduler", return_value=None)
_scheduler_stop_patch = patch("app.scheduler.job_manager.stop_scheduler", return_value=None)


@pytest.fixture(scope="module")
async def test_app():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with Session() as session:
            yield session

    with _scheduler_patch, _scheduler_stop_patch:
        # Import app AFTER patches are applied
        from app.main import app
        app.dependency_overrides[get_db] = override_get_db

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client

        app.dependency_overrides.clear()

    await engine.dispose()


@pytest.mark.asyncio
async def test_health(test_app):
    resp = await test_app.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_list_topics_empty(test_app):
    resp = await test_app.get("/api/topics/")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_list_videos_empty(test_app):
    resp = await test_app.get("/api/videos/")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_analytics_summary_empty(test_app):
    resp = await test_app.get("/api/analytics/summary")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_views" in data
    assert data["total_views"] == 0


@pytest.mark.asyncio
async def test_topic_not_found(test_app):
    resp = await test_app.get("/api/topics/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_video_not_found(test_app):
    resp = await test_app.get("/api/videos/9999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_pipeline_trigger(test_app):
    with patch("app.api.pipeline.run_pipeline_once"):
        resp = await test_app.post("/api/pipeline/run")
    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_pipeline_status_not_found(test_app):
    resp = await test_app.get("/api/pipeline/status/nonexistent-job-id")
    assert resp.status_code == 404
