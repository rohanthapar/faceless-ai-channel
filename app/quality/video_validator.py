"""
app/quality/video_validator.py — Technical and content quality checks.

A video must pass ALL mandatory checks before publication.
Failures trigger up to 2 regeneration attempts, then operator notification.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.logger import get_logger

logger = get_logger("quality.validator")


@dataclass
class QualityReport:
    passed: bool = True
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def fail(self, check_name: str, reason: str) -> None:
        self.passed = False
        self.checks[check_name] = False
        self.details[check_name] = reason

    def ok(self, check_name: str, detail: str = "") -> None:
        self.checks[check_name] = True
        if detail:
            self.details[check_name] = detail

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "checks": self.checks,
            "details": self.details,
            "warnings": self.warnings,
        }


async def _probe_video(video_path: Path) -> dict | None:
    """Run ffprobe and return JSON stream info."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode != 0:
        return None
    try:
        return json.loads(stdout.decode())
    except json.JSONDecodeError:
        return None


async def _check_no_black_frames(video_path: Path, threshold: float = 0.98) -> bool:
    """
    Detect if the video has significant black frame runs.
    Returns True (passes) if no problematic black frames found.
    """
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", f"blackdetect=d=0.5:pic_th={threshold}",
        "-an", "-f", "null", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    output = stderr.decode()
    # If blackdetect fires, it prints "black_start" lines
    return "black_start" not in output


async def _check_audio_levels(video_path: Path) -> tuple[float, float]:
    """Return (mean_volume_dBFS, max_volume_dBFS) of the audio track."""
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-af", "volumedetect",
        "-vn", "-f", "null", "-",
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    output = stderr.decode()

    mean_vol = -99.0
    max_vol = -99.0

    for line in output.splitlines():
        if "mean_volume:" in line:
            try:
                mean_vol = float(line.split("mean_volume:")[1].split()[0])
            except (IndexError, ValueError):
                pass
        if "max_volume:" in line:
            try:
                max_vol = float(line.split("max_volume:")[1].split()[0])
            except (IndexError, ValueError):
                pass

    return mean_vol, max_vol


async def validate_video(
    video_path: Path,
    script_text: str = "",
    min_duration: float | None = None,
    max_duration: float | None = None,
) -> QualityReport:
    """
    Run all technical quality checks on the rendered video.

    Args:
        video_path: Path to the MP4 file.
        script_text: Expected narration (for content checks).
        min_duration: Override minimum duration in seconds.
        max_duration: Override maximum duration in seconds.

    Returns QualityReport with pass/fail status for each check.
    """
    report = QualityReport()
    min_dur = min_duration or settings.video_min_duration
    max_dur = max_duration or settings.video_max_duration

    # ── Check 1: File exists ────────────────────────────────────────
    if not video_path.exists():
        report.fail("file_exists", f"File not found: {video_path}")
        return report
    report.ok("file_exists")

    # ── Check 2: Valid MP4 (ffprobe) ────────────────────────────────
    probe = await _probe_video(video_path)
    if probe is None:
        report.fail("valid_mp4", "ffprobe could not read the file")
        return report
    report.ok("valid_mp4")

    streams = probe.get("streams", [])
    fmt = probe.get("format", {})

    # ── Check 3: Video stream present ───────────────────────────────
    video_streams = [s for s in streams if s.get("codec_type") == "video"]
    if not video_streams:
        report.fail("video_stream", "No video stream found")
    else:
        vs = video_streams[0]
        report.ok("video_stream", f"codec={vs.get('codec_name')} {vs.get('width')}x{vs.get('height')}")

        # ── Check 4: Resolution ─────────────────────────────────────
        width = vs.get("width", 0)
        height = vs.get("height", 0)
        if width != settings.video_width or height != settings.video_height:
            report.fail(
                "resolution",
                f"Expected {settings.video_width}x{settings.video_height}, got {width}x{height}",
            )
        else:
            report.ok("resolution", f"{width}x{height}")

        # ── Check 5: Aspect ratio (9:16) ────────────────────────────
        if height > 0:
            aspect = width / height
            expected = 9 / 16
            if abs(aspect - expected) > 0.02:
                report.fail("aspect_ratio", f"Expected 9:16 ({expected:.3f}), got {aspect:.3f}")
            else:
                report.ok("aspect_ratio")

    # ── Check 6: Audio stream ────────────────────────────────────────
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        report.fail("audio_stream", "No audio stream found")
    else:
        aus = audio_streams[0]
        report.ok("audio_stream", f"codec={aus.get('codec_name')} sr={aus.get('sample_rate')}")

    # ── Check 7: Duration ────────────────────────────────────────────
    try:
        duration = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0

    if duration < min_dur:
        report.fail("duration", f"Too short: {duration:.1f}s < {min_dur}s")
    elif duration > max_dur + 5:
        report.fail("duration", f"Too long: {duration:.1f}s > {max_dur}s")
    else:
        report.ok("duration", f"{duration:.1f}s")

    # ── Check 8: File size (> 500KB means it's not empty) ───────────
    file_size = video_path.stat().st_size
    if file_size < 500_000:
        report.fail("file_size", f"File suspiciously small: {file_size} bytes")
    else:
        report.ok("file_size", f"{file_size / 1_000_000:.1f} MB")

    # ── Check 9: No black frames ─────────────────────────────────────
    try:
        no_black = await _check_no_black_frames(video_path)
        if not no_black:
            report.warn("Detected black frame runs (may be normal for dark scenes)")
            report.ok("no_black_frames", "warning only")
        else:
            report.ok("no_black_frames")
    except Exception as exc:
        report.warn(f"Black frame check failed: {exc}")

    # ── Check 10: Audio levels ───────────────────────────────────────
    if audio_streams:
        try:
            mean_vol, max_vol = await _check_audio_levels(video_path)
            if mean_vol < -50:
                report.fail("audio_levels", f"Audio too quiet: mean={mean_vol:.1f} dBFS")
            elif max_vol > -0.5:
                report.fail("audio_levels", f"Audio clipping detected: max={max_vol:.1f} dBFS")
            else:
                report.ok("audio_levels", f"mean={mean_vol:.1f} max={max_vol:.1f} dBFS")
        except Exception as exc:
            report.warn(f"Audio level check failed: {exc}")

    # ── Check 11: Container is MP4 ───────────────────────────────────
    format_name = fmt.get("format_name", "")
    if "mp4" not in format_name and "mov" not in format_name:
        report.fail("container_format", f"Not MP4: {format_name}")
    else:
        report.ok("container_format", "mp4")

    logger.info(
        "Quality check %s: %d/%d checks passed",
        "✅ PASSED" if report.passed else "❌ FAILED",
        sum(1 for v in report.checks.values() if v),
        len(report.checks),
    )

    return report
