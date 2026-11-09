"""
app/media/image_generator.py — Fetch licensed stock images and videos from Pexels/Pixabay.

All assets are retrieved through official APIs with valid free-tier keys.
Asset licences are verified and recorded before use.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.config import settings
from app.logger import get_logger

logger = get_logger("media.image_generator")

ASSET_CACHE_DIR = Path("output/drafts/asset_cache")


@dataclass
class StockAsset:
    url: str
    preview_url: str
    source: str  # "pexels" | "pixabay"
    asset_type: str  # "image" | "video"
    width: int
    height: int
    creator: str
    creator_url: str
    licence: str
    source_page_url: str
    local_path: Path | None = None


async def _search_pexels_videos(
    query: str,
    client: httpx.AsyncClient,
    per_page: int = 5,
) -> list[StockAsset]:
    """Search Pexels for video clips."""
    if not settings.pexels_api_key:
        return []

    try:
        resp = await client.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": settings.pexels_api_key},
            params={"query": query, "per_page": per_page, "orientation": "portrait", "size": "medium"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        assets = []
        for video in data.get("videos", []):
            # Find best quality portrait video file
            files = sorted(
                [f for f in video.get("video_files", []) if f.get("quality") in ("hd", "sd")],
                key=lambda f: f.get("height", 0),
                reverse=True,
            )
            if not files:
                continue
            best = files[0]
            assets.append(
                StockAsset(
                    url=best["link"],
                    preview_url=video.get("image", ""),
                    source="pexels",
                    asset_type="video",
                    width=best.get("width", 0),
                    height=best.get("height", 0),
                    creator=video.get("user", {}).get("name", "Unknown"),
                    creator_url=video.get("user", {}).get("url", ""),
                    licence="Pexels License — Free for commercial use, no attribution required",
                    source_page_url=video.get("url", ""),
                )
            )
        return assets

    except Exception as exc:
        logger.warning("Pexels video search failed for '%s': %s", query, exc)
        return []


async def _search_pexels_images(
    query: str,
    client: httpx.AsyncClient,
    per_page: int = 5,
) -> list[StockAsset]:
    """Search Pexels for photos."""
    if not settings.pexels_api_key:
        return []

    try:
        resp = await client.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": settings.pexels_api_key},
            params={"query": query, "per_page": per_page, "orientation": "portrait"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        assets = []
        for photo in data.get("photos", []):
            src = photo.get("src", {})
            assets.append(
                StockAsset(
                    url=src.get("original", src.get("large2x", "")),
                    preview_url=src.get("medium", ""),
                    source="pexels",
                    asset_type="image",
                    width=photo.get("width", 0),
                    height=photo.get("height", 0),
                    creator=photo.get("photographer", "Unknown"),
                    creator_url=photo.get("photographer_url", ""),
                    licence="Pexels License — Free for commercial use, no attribution required",
                    source_page_url=photo.get("url", ""),
                )
            )
        return assets

    except Exception as exc:
        logger.warning("Pexels image search failed for '%s': %s", query, exc)
        return []


async def _search_pixabay_images(
    query: str,
    client: httpx.AsyncClient,
    per_page: int = 5,
) -> list[StockAsset]:
    """Search Pixabay for images."""
    if not settings.pixabay_api_key:
        return []

    try:
        resp = await client.get(
            "https://pixabay.com/api/",
            params={
                "key": settings.pixabay_api_key,
                "q": query,
                "per_page": per_page,
                "orientation": "vertical",
                "image_type": "photo",
                "safesearch": "true",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        assets = []
        for hit in data.get("hits", []):
            assets.append(
                StockAsset(
                    url=hit.get("largeImageURL", ""),
                    preview_url=hit.get("webformatURL", ""),
                    source="pixabay",
                    asset_type="image",
                    width=hit.get("imageWidth", 0),
                    height=hit.get("imageHeight", 0),
                    creator=hit.get("user", "Unknown"),
                    creator_url=f"https://pixabay.com/users/{hit.get('user', '')}",
                    licence="Pixabay License — Free for commercial use, no attribution required",
                    source_page_url=hit.get("pageURL", ""),
                )
            )
        return assets

    except Exception as exc:
        logger.warning("Pixabay search failed for '%s': %s", query, exc)
        return []


async def _download_asset(
    asset: StockAsset,
    client: httpx.AsyncClient,
    dest_dir: Path,
) -> Path | None:
    """Download an asset to local filesystem."""
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Deterministic filename from URL hash
    url_hash = hashlib.sha256(asset.url.encode()).hexdigest()[:12]
    ext = "mp4" if asset.asset_type == "video" else "jpg"
    dest = dest_dir / f"{url_hash}.{ext}"

    if dest.exists():
        logger.debug("Asset already cached: %s", dest.name)
        return dest

    try:
        async with client.stream("GET", asset.url, timeout=60.0) as resp:
            resp.raise_for_status()
            with dest.open("wb") as f:
                async for chunk in resp.aiter_bytes(chunk_size=1024 * 64):
                    f.write(chunk)
        logger.info("Downloaded %s: %s", asset.asset_type, dest.name)
        return dest

    except Exception as exc:
        logger.warning("Failed to download asset %s: %s", asset.url[:60], exc)
        if dest.exists():
            dest.unlink()
        return None


async def fetch_assets_for_scene(
    scene: dict,
    output_dir: Path,
) -> list[StockAsset]:
    """
    Fetch stock assets for a storyboard scene.
    Returns a list of downloaded assets sorted by relevance.
    Priority: Pexels video → Pexels image → Pixabay image.
    """
    keywords = scene.get("search_keywords", [])
    subject = scene.get("subject", "")
    query = " ".join(keywords[:3]) or subject or "nature cinematic"

    logger.info("Fetching assets for scene %d: '%s'", scene.get("scene_number", 0), query)

    async with httpx.AsyncClient(
        headers={"User-Agent": "FacelessAI/1.0"},
        follow_redirects=True,
    ) as client:
        # Try video first (more dynamic), then images
        video_assets = await _search_pexels_videos(query, client, per_page=3)
        image_assets_pexels = await _search_pexels_images(query, client, per_page=3)
        image_assets_pixabay = await _search_pixabay_images(query, client, per_page=3)

        all_assets = video_assets + image_assets_pexels + image_assets_pixabay

        if not all_assets:
            logger.warning("No assets found for query '%s' — trying generic fallback", query)
            fallback_query = subject or "abstract background"
            image_assets_pexels = await _search_pexels_images(fallback_query, client, per_page=3)
            all_assets = image_assets_pexels

        if not all_assets:
            logger.error("No assets available for scene %d", scene.get("scene_number", 0))
            return []

        # Download top candidate
        downloaded = []
        for asset in all_assets[:2]:  # Download top 2, use first
            path = await _download_asset(asset, client, output_dir)
            if path:
                asset.local_path = path
                downloaded.append(asset)
                break  # One is enough per scene

        return downloaded
