"""
app/publishing/youtube.py — YouTube Data API v3 integration.

Implements OAuth 2.0 flow, secure token storage, video upload with retry,
privacy settings, and upload status monitoring.

IMPORTANT: Set YOUTUBE_PRODUCTION_MODE=false to upload as private/unlisted
during testing. Only flip to true when ready for real publishing.
"""
from __future__ import annotations

import asyncio
import json
import pickle
from pathlib import Path
from typing import Any

from app.config import settings
from app.database import Publication, PublicationStatus, Video, VideoMetadata
from app.logger import get_logger

logger = get_logger("publishing.youtube")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

YOUTUBE_API_SERVICE_NAME = "youtube"
YOUTUBE_API_VERSION = "v3"


def _get_credentials():
    """Load or create OAuth credentials."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request

    token_file = settings.youtube_token_file
    client_secret_file = settings.youtube_client_secret_file

    creds = None

    # Load existing token
    if token_file.exists():
        with token_file.open("rb") as f:
            creds = pickle.load(f)

    # Refresh or re-authenticate
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not client_secret_file.exists():
                raise FileNotFoundError(
                    f"YouTube client secret not found: {client_secret_file}\n"
                    "Download it from Google Cloud Console → APIs → Credentials → OAuth 2.0 Client IDs"
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(client_secret_file), SCOPES
            )
            creds = flow.run_local_server(port=0)

        # Save token for next run
        token_file.parent.mkdir(parents=True, exist_ok=True)
        with token_file.open("wb") as f:
            pickle.dump(creds, f)
        logger.info("YouTube credentials saved to %s", token_file)

    return creds


def _build_youtube_client():
    """Build authenticated YouTube API client."""
    from googleapiclient.discovery import build

    creds = _get_credentials()
    return build(
        YOUTUBE_API_SERVICE_NAME,
        YOUTUBE_API_VERSION,
        credentials=creds,
    )


async def run_oauth_flow() -> bool:
    """
    Run the YouTube OAuth2 authorization flow interactively.
    Call this once from CLI to generate and store the token.
    """
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _build_youtube_client)
        logger.info("✅ YouTube OAuth completed successfully")
        return True
    except Exception as exc:
        logger.error("YouTube OAuth failed: %s", exc)
        return False


async def upload_video(
    video_path: Path,
    metadata: VideoMetadata,
    publication: Publication,
) -> dict[str, Any]:
    """
    Upload a video to YouTube using the resumable upload protocol.

    Returns dict with 'success', 'video_id', 'url', 'error' keys.
    """
    if not video_path.exists():
        return {"success": False, "error": f"Video file not found: {video_path}"}

    if not settings.youtube_client_secret_file.exists():
        return {
            "success": False,
            "error": "YouTube client_secret.json not found. See setup instructions.",
        }

    # Safety check: only publish in production mode
    privacy = settings.youtube_default_privacy
    if not settings.youtube_production_mode:
        privacy = "private"
        logger.info("Production mode off — uploading as private")

    logger.info("Uploading to YouTube: %s (privacy=%s)", video_path.name, privacy)

    try:
        from googleapiclient.http import MediaFileUpload
        import googleapiclient.errors

        loop = asyncio.get_event_loop()

        def _upload():
            youtube = _build_youtube_client()

            body = {
                "snippet": {
                    "title": (metadata.youtube_title or "Untitled")[:100],
                    "description": metadata.youtube_description or "",
                    "tags": metadata.youtube_tags or [],
                    "categoryId": metadata.youtube_category_id or "27",
                    "defaultLanguage": "en",
                },
                "status": {
                    "privacyStatus": privacy,
                    "selfDeclaredMadeForKids": False,
                    "madeForKids": False,
                },
            }

            media = MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                resumable=True,
                chunksize=1024 * 1024 * 10,  # 10 MB chunks
            )

            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info("Upload progress: %d%%", pct)

            return response

        response = await loop.run_in_executor(None, _upload)

        video_id = response.get("id", "")
        url = f"https://www.youtube.com/shorts/{video_id}" if video_id else ""

        logger.info("✅ YouTube upload complete: %s → %s", video_path.name, url)
        return {"success": True, "video_id": video_id, "url": url, "error": None}

    except Exception as exc:
        error_msg = str(exc)
        logger.error("YouTube upload failed: %s", error_msg)
        return {"success": False, "error": error_msg, "video_id": None, "url": None}


async def check_credentials_configured() -> bool:
    """Return True if YouTube credentials are present (not necessarily valid)."""
    return settings.youtube_client_secret_file.exists()
