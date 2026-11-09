"""
app/database.py — SQLAlchemy async engine, session factory, base model.
"""
from __future__ import annotations

import enum
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.config import settings

# ─── Engine ──────────────────────────────────────────────────────────────────

# Ensure data directory exists
db_path = Path(settings.database_url.replace("sqlite:///", ""))
db_path.parent.mkdir(parents=True, exist_ok=True)

# Convert sync sqlite URL to async
_async_url = settings.database_url.replace("sqlite:///", "sqlite+aiosqlite:///")

engine = create_async_engine(
    _async_url,
    echo=settings.is_development,
    connect_args={"check_same_thread": False},
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for database sessions."""
    async with SessionLocal() as session:
        yield session


# ─── Base ────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


# ─── Enums ───────────────────────────────────────────────────────────────────

class ContentPillar(str, enum.Enum):
    PSYCHOLOGY = "psychology"
    HISTORY = "history"
    TECHNOLOGY = "technology"
    STORYTELLING = "storytelling"
    SCIENCE = "science"


class TopicStatus(str, enum.Enum):
    CANDIDATE = "candidate"
    SELECTED = "selected"
    SCRIPTED = "scripted"
    PRODUCED = "produced"
    PUBLISHED = "published"
    REJECTED = "rejected"
    USED = "used"


class JobStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYING = "retrying"


class PublicationStatus(str, enum.Enum):
    PENDING = "pending"
    UPLOADING = "uploading"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    SKIPPED = "skipped"


class QualityStatus(str, enum.Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


# ─── Models ──────────────────────────────────────────────────────────────────

class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    pillar: Mapped[ContentPillar] = mapped_column(Enum(ContentPillar), nullable=False)
    source_urls: Mapped[list] = mapped_column(JSON, default=list)
    source_dates: Mapped[list] = mapped_column(JSON, default=list)
    factual_claims: Mapped[list] = mapped_column(JSON, default=list)
    verification_notes: Mapped[str | None] = mapped_column(Text)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.0)
    trend_score: Mapped[float] = mapped_column(Float, default=0.0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[TopicStatus] = mapped_column(Enum(TopicStatus), default=TopicStatus.CANDIDATE)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    scripts: Mapped[list["Script"]] = relationship(back_populates="topic")
    videos: Mapped[list["Video"]] = relationship(back_populates="topic")


class Script(Base):
    __tablename__ = "scripts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False)
    hook: Mapped[str] = mapped_column(Text, nullable=False)
    script: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_duration: Mapped[float] = mapped_column(Float, default=0.0)
    language: Mapped[str] = mapped_column(String(10), default="en")
    clarity_score: Mapped[float] = mapped_column(Float, default=0.0)
    originality_score: Mapped[float] = mapped_column(Float, default=0.0)
    retention_score: Mapped[float] = mapped_column(Float, default=0.0)
    verification_status: Mapped[str] = mapped_column(String(50), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    topic: Mapped["Topic"] = relationship(back_populates="scripts")
    storyboards: Mapped[list["Storyboard"]] = relationship(back_populates="script")


class Storyboard(Base):
    __tablename__ = "storyboards"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    script_id: Mapped[int] = mapped_column(ForeignKey("scripts.id"), nullable=False)
    scene_data: Mapped[list] = mapped_column(JSON, default=list)
    visual_style: Mapped[dict] = mapped_column(JSON, default=dict)
    total_scenes: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    script: Mapped["Script"] = relationship(back_populates="storyboards")
    assets: Mapped[list["Asset"]] = relationship(back_populates="storyboard")


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    storyboard_id: Mapped[int | None] = mapped_column(ForeignKey("storyboards.id"))
    file_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2000))
    creator: Mapped[str | None] = mapped_column(String(500))
    licence: Mapped[str | None] = mapped_column(String(200))
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)  # image|video|audio|font
    checksum: Mapped[str | None] = mapped_column(String(64))
    attribution_required: Mapped[bool] = mapped_column(Boolean, default=False)
    attribution_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    storyboard: Mapped["Storyboard | None"] = relationship(back_populates="assets")


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), nullable=False)
    script_id: Mapped[int | None] = mapped_column(ForeignKey("scripts.id"))
    job_id: Mapped[str | None] = mapped_column(String(100))
    render_path: Mapped[str | None] = mapped_column(String(1000))
    thumbnail_path: Mapped[str | None] = mapped_column(String(1000))
    duration: Mapped[float | None] = mapped_column(Float)
    file_size_bytes: Mapped[int | None] = mapped_column(Integer)
    quality_status: Mapped[QualityStatus] = mapped_column(
        Enum(QualityStatus), default=QualityStatus.PENDING
    )
    quality_report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    topic: Mapped["Topic"] = relationship(back_populates="videos")
    publications: Mapped[list["Publication"]] = relationship(back_populates="video")
    metadata_record: Mapped["VideoMetadata | None"] = relationship(back_populates="video")


class VideoMetadata(Base):
    __tablename__ = "video_metadata"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), unique=True, nullable=False)
    youtube_title: Mapped[str | None] = mapped_column(String(100))
    youtube_description: Mapped[str | None] = mapped_column(Text)
    youtube_tags: Mapped[list] = mapped_column(JSON, default=list)
    youtube_category_id: Mapped[str] = mapped_column(String(10), default="27")  # Education
    instagram_caption: Mapped[str | None] = mapped_column(Text)
    instagram_hashtags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    video: Mapped["Video"] = relationship(back_populates="metadata_record")


class Publication(Base):
    __tablename__ = "publications"
    __table_args__ = (UniqueConstraint("video_id", "platform", name="uq_video_platform"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("videos.id"), nullable=False)
    platform: Mapped[str] = mapped_column(String(50), nullable=False)  # youtube | instagram
    platform_video_id: Mapped[str | None] = mapped_column(String(200))
    platform_url: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(PublicationStatus), default=PublicationStatus.PENDING
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    video: Mapped["Video"] = relationship(back_populates="publications")
    analytics_records: Mapped[list["Analytics"]] = relationship(back_populates="publication")


class Analytics(Base):
    __tablename__ = "analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    publication_id: Mapped[int] = mapped_column(ForeignKey("publications.id"), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(String(20))  # 1h | 24h | 7d | 30d
    views: Mapped[int | None] = mapped_column(Integer)
    likes: Mapped[int | None] = mapped_column(Integer)
    comments: Mapped[int | None] = mapped_column(Integer)
    shares: Mapped[int | None] = mapped_column(Integer)
    watch_time_seconds: Mapped[float | None] = mapped_column(Float)
    avg_view_duration: Mapped[float | None] = mapped_column(Float)
    avg_view_percentage: Mapped[float | None] = mapped_column(Float)
    engagement_rate: Mapped[float | None] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    publication: Mapped["Publication"] = relationship(back_populates="analytics_records")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    stage: Mapped[str | None] = mapped_column(String(100))  # current pipeline stage
    status: Mapped[JobStatus] = mapped_column(Enum(JobStatus), default=JobStatus.PENDING)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    context: Mapped[dict] = mapped_column(JSON, default=dict)  # stage outputs for resumption
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ─── Init ────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables (called on startup)."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
