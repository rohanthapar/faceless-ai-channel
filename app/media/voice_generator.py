"""
app/media/voice_generator.py — Generate narration audio using TTS providers.

Provider priority:
  1. Edge TTS (free, high quality Microsoft neural voices)
  2. Piper TTS (local, fully offline)
  3. Fallback: silence placeholder with warning

Saves output as WAV (and converts to MP3 for final mix).
"""
from __future__ import annotations

import asyncio
import hashlib
import tempfile
from pathlib import Path

from app.config import settings
from app.logger import get_logger

logger = get_logger("media.voice_generator")


def _get_audio_cache_path(text: str, voice: str) -> Path:
    """Return a deterministic cache path for a given text+voice combo."""
    hash_key = hashlib.sha256(f"{voice}::{settings.edge_tts_rate}::{text}".encode()).hexdigest()[:16]
    cache_dir = settings.output_drafts / "audio_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{hash_key}.mp3"


async def _edge_tts_generate(text: str, voice: str, output_path: Path) -> bool:
    """Generate audio using edge-tts (free Microsoft neural voices)."""
    try:
        import edge_tts

        # Edge TTS sporadically returns NoAudioReceived; a quick retry usually succeeds
        for attempt in range(3):
            try:
                communicate = edge_tts.Communicate(text, voice, rate=settings.edge_tts_rate)
                await communicate.save(str(output_path))
                break
            except edge_tts.exceptions.NoAudioReceived:
                if attempt == 2:
                    raise
                await asyncio.sleep(2)
        logger.info("Edge TTS generated: %s", output_path.name)
        return True
    except ImportError:
        logger.warning("edge-tts not installed")
        return False
    except Exception as exc:
        logger.warning("Edge TTS failed: %s", exc)
        return False


async def _piper_tts_generate(text: str, model_path: Path, output_path: Path) -> bool:
    """Generate audio using local Piper TTS model."""
    if not model_path.exists():
        logger.warning("Piper model not found at %s", model_path)
        return False

    try:
        # Piper reads from stdin and writes WAV to stdout
        wav_path = output_path.with_suffix(".wav")
        cmd = [
            "piper",
            "--model", str(model_path),
            "--output_file", str(wav_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate(input=text.encode())

        if proc.returncode != 0:
            logger.warning("Piper failed: %s", stderr.decode()[:200])
            return False

        # Convert WAV to MP3
        if wav_path.exists():
            await _convert_wav_to_mp3(wav_path, output_path)
            wav_path.unlink(missing_ok=True)
            return True
        return False

    except FileNotFoundError:
        logger.warning("Piper binary not found in PATH")
        return False
    except Exception as exc:
        logger.warning("Piper TTS failed: %s", exc)
        return False


async def _convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Convert WAV to MP3 using FFmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav_path),
        "-codec:a", "libmp3lame",
        "-b:a", "192k",
        str(mp3_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()


async def _create_silent_placeholder(duration: float, output_path: Path) -> bool:
    """Create a silent audio track as last-resort fallback."""
    logger.error("All TTS providers failed — creating silent placeholder")
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=r=44100:cl=mono",
        "-t", str(duration),
        "-c:a", "libmp3lame",
        "-b:a", "128k",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return output_path.exists()


async def generate_narration(
    text: str,
    output_path: Path,
    use_cache: bool = True,
) -> bool:
    """
    Generate narration audio for the given text.

    Returns True if audio was successfully generated, False otherwise.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Check cache
    if use_cache:
        cache_path = _get_audio_cache_path(text, settings.edge_tts_voice)
        if cache_path.exists():
            import shutil
            shutil.copy2(cache_path, output_path)
            logger.info("Narration loaded from cache")
            return True

    success = False

    # Try providers in priority order
    if settings.tts_primary_provider == "edge" or not success:
        success = await _edge_tts_generate(text, settings.edge_tts_voice, output_path)

    if not success and settings.piper_model_path.exists():
        success = await _piper_tts_generate(text, settings.piper_model_path, output_path)

    if not success:
        # Last resort
        success = await _create_silent_placeholder(30.0, output_path)
        logger.error("Using silent placeholder — TTS providers unavailable")

    # Update cache
    if success and use_cache and output_path.exists():
        import shutil
        cache_path = _get_audio_cache_path(text, settings.edge_tts_voice)
        shutil.copy2(output_path, cache_path)

    return success


async def get_audio_duration(audio_path: Path) -> float:
    """Return duration of audio file in seconds using FFprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    try:
        return float(stdout.decode().strip())
    except (ValueError, AttributeError):
        return 0.0
