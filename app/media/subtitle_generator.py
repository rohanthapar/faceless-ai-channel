"""
app/media/subtitle_generator.py — Generate word-level subtitle SRT/ASS files.

Uses faster-whisper to transcribe the generated narration audio and produce
precisely timed subtitle segments.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from app.logger import get_logger

logger = get_logger("media.subtitle_generator")


def _format_srt_time(seconds: float) -> str:
    """Convert seconds to SRT timestamp format."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS timestamp format (H:MM:SS.cc)."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


async def transcribe_audio(audio_path: Path) -> list[dict]:
    """
    Transcribe audio using faster-whisper and return word-level segments.
    Returns list of {text, start, end} dicts.
    """
    try:
        from faster_whisper import WhisperModel

        logger.info("Transcribing audio with faster-whisper...")
        # base.en: accurate enough for clean TTS audio, ~1s per 10s of audio on an M1
        model = WhisperModel("base.en", device="cpu", compute_type="int8")

        # Decode with ffmpeg rather than faster-whisper's PyAV path, which breaks
        # on PyAV >= 19 (removed the metadata_errors argument)
        import numpy as np

        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-i", str(audio_path),
            "-f", "f32le", "-ac", "1", "-ar", "16000", "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pcm, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg decode failed: {stderr.decode()[-300:]}")
        audio = np.frombuffer(pcm, dtype=np.float32)

        segments, _ = model.transcribe(
            audio,
            word_timestamps=True,
            language="en",
        )

        words = []
        for segment in segments:
            if hasattr(segment, "words") and segment.words:
                for word in segment.words:
                    words.append({
                        "text": word.word.strip(),
                        "start": word.start,
                        "end": word.end,
                    })
            else:
                words.append({
                    "text": segment.text.strip(),
                    "start": segment.start,
                    "end": segment.end,
                })

        logger.info("Transcribed %d word segments", len(words))
        return words

    except ImportError:
        logger.warning("faster-whisper not available — using script-based subtitle timing")
        return []
    except Exception as exc:
        logger.warning("Transcription failed: %s", exc)
        return []


def _group_words_into_lines(
    words: list[dict],
    max_chars_per_line: int = 32,
    max_words_per_group: int = 5,
) -> list[dict]:
    """Group word-level timestamps into readable subtitle lines."""
    if not words:
        return []

    groups = []
    current_group: list[dict] = []
    current_chars = 0

    for word in words:
        word_text = word["text"]
        word_chars = len(word_text) + 1  # +1 for space

        if (
            current_group
            and (
                current_chars + word_chars > max_chars_per_line
                or len(current_group) >= max_words_per_group
            )
        ):
            # Flush current group
            groups.append({
                "text": " ".join(w["text"] for w in current_group),
                "start": current_group[0]["start"],
                "end": current_group[-1]["end"],
                "words": current_group,
            })
            current_group = []
            current_chars = 0

        current_group.append(word)
        current_chars += word_chars

        # Never let a caption run across a sentence boundary
        if word_text.endswith((".", "!", "?")):
            groups.append({
                "text": " ".join(w["text"] for w in current_group),
                "start": current_group[0]["start"],
                "end": current_group[-1]["end"],
                "words": current_group,
            })
            current_group = []
            current_chars = 0

    if current_group:
        groups.append({
            "text": " ".join(w["text"] for w in current_group),
            "start": current_group[0]["start"],
            "end": current_group[-1]["end"],
            "words": current_group,
        })

    return groups


def _script_to_timed_segments(
    script_text: str,
    total_duration: float,
) -> list[dict]:
    """
    Fallback: distribute script text evenly across the total duration.
    Used when faster-whisper is unavailable.
    """
    sentences = re.split(r'(?<=[.!?])\s+', script_text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]

    if not sentences:
        return []

    total_words = sum(len(s.split()) for s in sentences)
    segments = []
    elapsed = 0.0

    # Short ~5-word chunks (never crossing a sentence) read better on Shorts
    for sentence in sentences:
        words = sentence.split()
        n_chunks = max(1, round(len(words) / 5))
        size = -(-len(words) // n_chunks)
        for i in range(0, len(words), size):
            chunk = words[i:i + size]
            per_word = total_duration / total_words
            segments.append({
                "text": " ".join(chunk),
                "start": elapsed,
                "end": elapsed + per_word * len(chunk),
                "words": [
                    {"text": w, "start": elapsed + j * per_word, "end": elapsed + (j + 1) * per_word}
                    for j, w in enumerate(chunk)
                ],
            })
            elapsed += per_word * len(chunk)

    return segments


def generate_srt(segments: list[dict], output_path: Path) -> None:
    """Write SRT subtitle file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, seg in enumerate(segments, 1):
        lines.append(str(i))
        lines.append(f"{_format_srt_time(seg['start'])} --> {_format_srt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("SRT written: %s (%d entries)", output_path.name, len(segments))


def generate_ass(
    segments: list[dict],
    output_path: Path,
    font: str = "Montserrat",
    font_size: int = 52,
    brand_color: str = "#6C63FF",
    video_width: int = 1080,
    video_height: int = 1920,
) -> None:
    """Write ASS subtitle file with styled captions for 9:16 video."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert hex colour to ASS BGR format
    r = int(brand_color[1:3], 16)
    g = int(brand_color[3:5], 16)
    b = int(brand_color[5:7], 16)
    ass_primary = f"&H00{b:02X}{g:02X}{r:02X}"
    ass_outline = "&H00000000"  # black

    # Vertical safe zone: position captions at 70% from top (bottom third)
    margin_v = int(video_height * 0.65)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 1

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{ass_primary},&H000000FF,{ass_outline},&H80000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events = []
    for seg in segments:
        start = _format_ass_time(seg["start"])
        end = _format_ass_time(seg["end"])
        text = seg["text"].replace("\n", "\\N")
        events.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info("ASS subtitles written: %s", output_path.name)


async def generate_subtitles(
    audio_path: Path,
    script_text: str,
    total_duration: float,
    output_dir: Path,
) -> dict[str, Path]:
    """
    Generate both SRT and ASS subtitle files for a video.

    Returns dict with 'srt', 'ass' and 'words' (word-timed caption JSON) paths.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / "subtitles.srt"
    ass_path = output_dir / "subtitles.ass"
    words_path = output_dir / "captions_words.json"

    # Try accurate transcription first
    words = await transcribe_audio(audio_path)

    if words:
        segments = _group_words_into_lines(words)
        logger.info("Using whisper-timed subtitles")
    else:
        segments = _script_to_timed_segments(script_text, total_duration)
        logger.info("Using script-estimated subtitles (%d segments)", len(segments))

    generate_srt(segments, srt_path)
    generate_ass(
        segments,
        ass_path,
        font=settings.caption_font,
        font_size=settings.caption_size,
        brand_color=settings.brand_color,
        video_width=settings.video_width,
        video_height=settings.video_height,
    )

    words_path.write_text(json.dumps(segments), encoding="utf-8")

    return {"srt": srt_path, "ass": ass_path, "words": words_path}


from app.config import settings  # noqa: E402 (avoid circular imports)
