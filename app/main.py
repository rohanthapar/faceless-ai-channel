"""
app/main.py — FastAPI application entry-point.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.logger import setup_logging
from app.api import dashboard, pipeline, topics, videos, analytics

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle."""
    logger.info("🚀 Starting Faceless AI Channel – %s", settings.channel_name)

    # Initialise database tables
    await init_db()
    logger.info("✅ Database initialised")

    # Start scheduler
    from app.scheduler.job_manager import start_scheduler, stop_scheduler
    start_scheduler()
    logger.info("⏰ Scheduler started (daily at %s %s)", settings.daily_pipeline_time, settings.timezone)

    yield

    # Graceful shutdown
    stop_scheduler()
    logger.info("👋 Shutdown complete")


app = FastAPI(
    title="Faceless AI Channel",
    description="Autonomous AI-powered short-form video pipeline",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files (dashboard assets)
app.mount("/static", StaticFiles(directory="app/templates/static", check_dir=False), name="static")

# API routers
app.include_router(dashboard.router, prefix="", tags=["Dashboard"])
app.include_router(pipeline.router, prefix="/api/pipeline", tags=["Pipeline"])
app.include_router(topics.router, prefix="/api/topics", tags=["Topics"])
app.include_router(videos.router, prefix="/api/videos", tags=["Videos"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["Analytics"])


@app.get("/health")
async def health():
    return {"status": "ok", "channel": settings.channel_name}
