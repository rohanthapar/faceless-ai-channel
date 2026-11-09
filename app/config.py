"""
app/config.py — Centralised settings loaded from environment variables.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT_DIR = Path(__file__).parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ──────────────────────────────────────────
    app_env: Literal["development", "production"] = "development"
    app_secret_key: str = "insecure-dev-key-change-me"
    dashboard_username: str = "admin"
    dashboard_password: str = "admin"

    # ── Database ─────────────────────────────────────────────
    database_url: str = f"sqlite:///{ROOT_DIR}/data/faceless.db"

    # ── AI / LLM ─────────────────────────────────────────────
    gemini_api_key: str = ""

    # ── YouTube ──────────────────────────────────────────────
    youtube_client_secret_file: Path = ROOT_DIR / "credentials" / "youtube_client_secret.json"
    youtube_token_file: Path = ROOT_DIR / "credentials" / "youtube_token.json"
    youtube_channel_id: str = "UCaM6eKRWzVABL-mxuggJPTg"
    youtube_channel_url: str = "https://www.youtube.com/@MindGlitchedAI"
    youtube_default_privacy: Literal["private", "unlisted", "public"] = "private"
    youtube_production_mode: bool = False

    # ── Instagram ────────────────────────────────────────────
    instagram_access_token: str = ""
    instagram_user_id: str = ""
    instagram_media_base_url: str = ""
    instagram_production_mode: bool = False

    # ── Stock Media ──────────────────────────────────────────
    pexels_api_key: str = ""
    pixabay_api_key: str = ""

    # ── Reddit ───────────────────────────────────────────────
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "faceless-ai-channel/1.0"

    # ── Content Pillars ──────────────────────────────────────
    pillar_psychology_pct: int = 30
    pillar_history_pct: int = 25
    pillar_technology_pct: int = 25
    pillar_storytelling_pct: int = 20

    @field_validator("pillar_storytelling_pct")
    @classmethod
    def pillars_sum_100(cls, v: int, info) -> int:
        total = (
            info.data.get("pillar_psychology_pct", 30)
            + info.data.get("pillar_history_pct", 25)
            + info.data.get("pillar_technology_pct", 25)
            + v
        )
        if total != 100:
            raise ValueError(f"Pillar percentages must sum to 100, got {total}")
        return v

    # ── Scheduler ────────────────────────────────────────────
    daily_pipeline_time: str = "18:30"
    timezone: str = "Asia/Kolkata"

    # ── Video ────────────────────────────────────────────────
    video_width: int = 1080
    video_height: int = 1920
    video_fps: int = 30
    video_min_duration: int = 25
    video_max_duration: int = 40
    video_bitrate: str = "4000k"
    audio_bitrate: str = "192k"

    # ── TTS ──────────────────────────────────────────────────
    tts_primary_provider: Literal["piper", "edge", "none"] = "edge"
    edge_tts_voice: str = "en-US-AriaNeural"
    piper_model_path: Path = ROOT_DIR / "assets" / "tts_models" / "en_US-lessac-medium.onnx"

    # ── Brand ────────────────────────────────────────────────
    channel_name: str = "MindGlitched AI"
    channel_handle: str = "@MindGlitchedAI"
    channel_description: str = (
        "Your brain is lying to you. Daily.\n\n"
        "MindGlitched AI drops one mind-bending psychology fact every day — "
        "the kind that changes how you see people, decisions, and yourself.\n\n"
        "New Short every day at 6:30 PM. Subscribe before your brain talks you out of it."
    )
    brand_color: str = "#6C63FF"
    caption_font: str = "Montserrat-Bold"
    caption_size: int = 52
    watermark_enabled: bool = False

    # ── Notifications ────────────────────────────────────────
    notification_webhook_url: str = ""

    # ── Logging ──────────────────────────────────────────────
    log_level: str = "INFO"
    log_file: Path = ROOT_DIR / "logs" / "app.log"

    # ── Derived paths ────────────────────────────────────────
    @property
    def output_drafts(self) -> Path:
        return ROOT_DIR / "output" / "drafts"

    @property
    def output_rendered(self) -> Path:
        return ROOT_DIR / "output" / "rendered"

    @property
    def output_published(self) -> Path:
        return ROOT_DIR / "output" / "published"

    @property
    def assets_dir(self) -> Path:
        return ROOT_DIR / "assets"

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


settings = Settings()


def get_settings() -> Settings:
    """FastAPI dependency."""
    return settings
