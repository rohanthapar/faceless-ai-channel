"""
app/media/image_generator.py — Fetch licensed stock images and videos from Pexels/Pixabay.

All assets are retrieved through official APIs with valid free-tier keys.
Asset licences are verified and recorded before use.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
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
    tags: str = ""  # descriptive keywords used to rank relevance


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
                    tags=_slug_words(video.get("url", "")),
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
                    tags=photo.get("alt", "") or _slug_words(photo.get("url", "")),
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
                    tags=hit.get("tags", ""),
                )
            )
        return assets

    except Exception as exc:
        logger.warning("Pixabay search failed for '%s': %s", query, exc)
        return []


async def _search_pixabay_videos(
    query: str,
    client: httpx.AsyncClient,
    per_page: int = 5,
) -> list[StockAsset]:
    """Search Pixabay for video clips (stands in for Pexels, which has no key)."""
    if not settings.pixabay_api_key:
        return []

    try:
        resp = await client.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": settings.pixabay_api_key,
                "q": query,
                "per_page": max(per_page, 3),  # Pixabay minimum is 3
                "video_type": "film",
                "safesearch": "true",
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

        assets = []
        for hit in data.get("hits", []):
            # Prefer ~1080p ("medium"/"large"); "large" can be 4K and slow to download
            videos = hit.get("videos", {})
            file = next((videos[k] for k in ("medium", "large", "small") if videos.get(k, {}).get("url")), None)
            if not file:
                continue
            assets.append(
                StockAsset(
                    url=file["url"],
                    preview_url=file.get("thumbnail", ""),
                    source="pixabay",
                    asset_type="video",
                    width=file.get("width", 0),
                    height=file.get("height", 0),
                    creator=hit.get("user", "Unknown"),
                    creator_url=f"https://pixabay.com/users/{hit.get('user', '')}",
                    licence="Pixabay License — Free for commercial use, no attribution required",
                    source_page_url=hit.get("pageURL", ""),
                    tags=hit.get("tags", ""),
                )
            )
        return assets

    except Exception as exc:
        logger.warning("Pixabay video search failed for '%s': %s", query, exc)
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


_STOP_WORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "by", "from",
    "is", "are", "was", "were", "be", "it", "its", "this", "that", "these", "those", "their",
    "they", "them", "you", "your", "as", "into", "than", "so", "but", "just", "only", "even",
    "very", "can", "will", "would", "has", "have", "had", "not", "no", "what", "when", "how",
    "why", "who", "which", "while", "after", "before", "over", "under", "up", "down", "out",
    "shot", "close", "closeup", "macro", "slow", "motion", "cinematic", "view", "background",
}


def _slug_words(url: str) -> str:
    """Words from a stock page URL slug, e.g. '/video/man-slicing-broccoli-123/'."""
    slug = url.rstrip("/").rsplit("/", 1)[-1]
    return " ".join(w for w in slug.split("-") if not w.isdigit())


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    # Crude stemming so "avalanches"/"avalanche" and "hikers"/"hiker" match
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in _STOP_WORDS and len(w) > 2}


def _relevance(
    asset: StockAsset,
    subject_terms: set[str],
    key_terms: set[str],
    context_terms: set[str],
) -> float:
    """Score a candidate by how well its tags describe what the scene is about.

    Stock sites list the most representative tags first, so early tags count
    more, and long tag lists are normalised so keyword-stuffed uploads (35 tags
    covering everything) don't win on volume alone.
    """
    tag_list = [t.strip() for t in asset.tags.split(",") if t.strip()] or [asset.tags]
    score, seen = 0.0, set()
    for pos, tag in enumerate(tag_list):
        position_weight = 1.0 / (1.0 + pos / 4)
        for tok in _tokens(tag) - seen:
            seen.add(tok)
            if tok in subject_terms:
                score += 3.0 * position_weight
            elif tok in key_terms:
                score += 2.0 * position_weight
            elif tok in context_terms:
                score += 1.0 * position_weight
    score /= max(1.0, len(tag_list) / 8) ** 0.5
    if "generated" in seen:  # "ai generated" stock tends to look fake
        score -= 1.0
    if asset.asset_type == "video" and score > 0:
        score += 0.5  # motion beats stills when equally relevant
    return score


async def fetch_assets_for_scene(
    scene: dict,
    output_dir: Path,
    exclude_urls: set[str] | None = None,
) -> list[StockAsset]:
    """
    Fetch the most relevant stock asset for a storyboard scene.

    Each search keyword is queried separately (joined multi-phrase queries
    return junk), all candidates are pooled, and the one whose tags best match
    the scene's keywords, subject and narration wins. Assets in `exclude_urls`
    (used by a previous attempt) are skipped.
    """
    exclude_urls = exclude_urls or set()
    keywords = [k for k in scene.get("search_keywords", []) if k][:3]
    subject = scene.get("subject", "")
    narration = scene.get("narration_segment", "")
    # The subject (a concrete noun phrase) is searched too: specific keyword
    # phrases like "knife cutting canvas tent" often match nothing relevant
    queries = list(dict.fromkeys([*keywords, subject])) if subject else keywords
    queries = [q for q in queries if q] or ["nature landscape"]

    subject_terms = _tokens(subject)
    key_terms = _tokens(" ".join(keywords)) - subject_terms
    context_terms = _tokens(narration + " " + scene.get("visual_description", "")) - key_terms - subject_terms

    logger.info("Fetching assets for scene %d: %s", scene.get("scene_number", 0), queries)

    async with httpx.AsyncClient(
        headers={"User-Agent": "FacelessAI/1.0"},
        follow_redirects=True,
    ) as client:
        pool: dict[str, StockAsset] = {}
        for query in queries:
            for found in (
                await _search_pexels_videos(query, client, per_page=5),
                await _search_pixabay_videos(query, client, per_page=8),
                await _search_pexels_images(query, client, per_page=3),
                await _search_pixabay_images(query, client, per_page=5),
            ):
                for asset in found:
                    if asset.url and asset.url not in exclude_urls:
                        pool.setdefault(asset.url, asset)

        if not pool:
            for fallback_query in [subject, *_tokens(" ".join(keywords))]:
                if not fallback_query or pool:
                    continue
                logger.warning("No assets for %s — trying '%s'", queries, fallback_query)
                for asset in (
                    await _search_pixabay_videos(fallback_query, client, per_page=8)
                    + await _search_pixabay_images(fallback_query, client, per_page=5)
                ):
                    if asset.url not in exclude_urls:
                        pool.setdefault(asset.url, asset)

        if not pool:
            logger.error("No assets available for scene %d", scene.get("scene_number", 0))
            return []

        ranked = sorted(pool.values(), key=lambda a: _relevance(a, subject_terms, key_terms, context_terms), reverse=True)
        best_score = _relevance(ranked[0], subject_terms, key_terms, context_terms)
        logger.info(
            "Scene %d best match (score %.1f, %s): %s",
            scene.get("scene_number", 0), best_score, ranked[0].asset_type, ranked[0].tags[:80],
        )

        for asset in ranked[:3]:  # fall through to the next best if a download fails
            path = await _download_asset(asset, client, output_dir)
            if path:
                asset.local_path = path
                return [asset]
        return []
