"""
tests/test_api.py — FastAPI endpoint integration tests.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="module")
async def test_app():
    engine = create_async_engine(TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_db():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
