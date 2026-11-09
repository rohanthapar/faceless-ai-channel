"""
app/publishing/instagram.py — Meta Instagram Graph API integration for Reels.

Publishing flow:
1. Upload video to a publicly accessible URL (temporary HTTP server or media host)
2. Create a media container via Instagram Graph API
3. Poll container status until FINISHED
4. Publish the container

IMPORTANT: Meta must be able to reach the video URL. For local deployments,
you need a publicly accessible URL (ngrok, Cloudflare Tunnel, or object storage).
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import settings
from app.database import Publication, VideoMetadata
from app.logger import get_logger

logger = get_logger("publishing.instagram")

GRAPH_API_BASE = "https://graph.instagram.com/v21.0"
CONTAINER_POLL_INTERVAL = 10  # seconds
CONTAINER_POLL_TIMEOUT = 300  # seconds


async def _create_reel_container(
    client: httpx.AsyncClient,
    media_url: str,
    caption: str,
) -> str | None:
    """Create a Reels media container. Returns container_id or None."""
    try:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{settings.instagram_user_id}/media",
            params={
                "access_token": settings.instagram_access_token,
                "media_type": "REELS",
                "video_url": media_url,
                "caption": caption[:2200],
                "share_to_feed": "true",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        container_id = data.get("id")
        logger.info("Created Instagram container: %s", container_id)
        return container_id

    except httpx.HTTPStatusError as exc:
        logger.error("Instagram container creation failed: %s — %s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:
        logger.error("Instagram container creation error: %s", exc)
        return None


async def _poll_container_status(
    client: httpx.AsyncClient,
    container_id: str,
) -> bool:
    """Poll until container status is FINISHED. Returns True if ready."""
    deadline = time.monotonic() + CONTAINER_POLL_TIMEOUT

    while time.monotonic() < deadline:
        try:
            resp = await client.get(
                f"{GRAPH_API_BASE}/{container_id}",
                params={
                    "access_token": settings.instagram_access_token,
                    "fields": "status_code,status",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            status_code = data.get("status_code", "")

            logger.info("Container %s status: %s", container_id, status_code)

            if status_code == "FINISHED":
                return True
            elif status_code in ("ERROR", "EXPIRED"):
                logger.error("Container processing failed: %s", data.get("status"))
                return False

        except Exception as exc:
            logger.warning("Container status poll error: %s", exc)

        await asyncio.sleep(CONTAINER_POLL_INTERVAL)

    logger.error("Container processing timed out after %ds", CONTAINER_POLL_TIMEOUT)
    return False


async def _publish_container(
    client: httpx.AsyncClient,
    container_id: str,
) -> str | None:
    """Publish a FINISHED container. Returns media_id or None."""
    try:
        resp = await client.post(
            f"{GRAPH_API_BASE}/{settings.instagram_user_id}/media_publish",
            params={
                "access_token": settings.instagram_access_token,
                "creation_id": container_id,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        media_id = data.get("id")
        logger.info("✅ Instagram Reel published: %s", media_id)
        return media_id

    except httpx.HTTPStatusError as exc:
        logger.error("Instagram publish failed: %s — %s", exc.response.status_code, exc.response.text[:300])
        return None
    except Exception as exc:
        logger.error("Instagram publish error: %s", exc)
        return None


async def publish_reel(
    video_path: Path,
    metadata: VideoMetadata,
    publication: Publication,
    media_url: str | None = None,
) -> dict[str, Any]:
    """
    Publish a Reel to Instagram via Meta Graph API.

    Args:
        video_path: Local path to MP4 file.
        metadata: Video metadata (caption, hashtags).
        publication: DB publication record.
        media_url: Publicly accessible URL to the video.
                   If None, checks INSTAGRAM_MEDIA_BASE_URL in settings.

    Returns dict with 'success', 'media_id', 'url', 'error'.
    """
    if not settings.instagram_access_token or not settings.instagram_user_id:
        return {
            "success": False,
            "error": "Instagram credentials not configured (INSTAGRAM_ACCESS_TOKEN, INSTAGRAM_USER_ID)",
        }

    if not settings.instagram_production_mode:
        logger.info("Instagram production mode disabled — skipping actual publish")
        return {
            "success": False,
            "error": "INSTAGRAM_PRODUCTION_MODE=false — set to true to enable publishing",
            "skipped": True,
        }

    if not video_path.exists():
        return {"success": False, "error": f"Video not found: {video_path}"}

    # Determine public media URL
    if not media_url:
        if not settings.instagram_media_base_url:
            return {
                "success": False,
                "error": (
                    "No public media URL available. "
                    "Set INSTAGRAM_MEDIA_BASE_URL to a publicly accessible server URL."
                ),
            }
        media_url = f"{settings.instagram_media_base_url.rstrip('/')}/videos/{video_path.name}"

    # Build caption
    hashtag_str = " ".join(metadata.instagram_hashtags or [])
    caption = f"{metadata.instagram_caption or ''}\n\n{hashtag_str}"[:2200]

    logger.info("Publishing Instagram Reel: %s", video_path.name)

    async with httpx.AsyncClient() as client:
        # Step 1: Create container
        container_id = await _create_reel_container(client, media_url, caption)
        if not container_id:
            return {"success": False, "error": "Failed to create media container"}

        # Step 2: Wait for processing
        ready = await _poll_container_status(client, container_id)
        if not ready:
            return {"success": False, "error": "Container processing failed or timed out"}

        # Step 3: Publish
        media_id = await _publish_container(client, container_id)
        if not media_id:
            return {"success": False, "error": "Failed to publish container"}

        url = f"https://www.instagram.com/p/{media_id}/"
        return {"success": True, "media_id": media_id, "url": url, "error": None}


async def check_credentials_configured() -> bool:
    """Return True if Instagram credentials are present."""
    return bool(settings.instagram_access_token and settings.instagram_user_id)


async def check_token_validity() -> bool:
    """Check if the current access token is valid."""
    if not settings.instagram_access_token:
        return False
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={
                    "access_token": settings.instagram_access_token,
                    "fields": "id,username",
                },
                timeout=10.0,
            )
            return resp.status_code == 200
    except Exception:
        return False
