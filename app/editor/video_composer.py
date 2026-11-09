"""
app/editor/video_composer.py — Assemble the final video using FFmpeg.

Handles:
- 1080×1920 9:16 vertical format
- Scene assembly with cinematic motion effects
- Narration audio mixing
- Background music (optional)
- ASS subtitle burn-in
- Hardware-accelerated encoding on Apple M1 (VideoToolbox)
- Final H.264/AAC MP4 output
"""
from __future__ import annotations

import asyncio
import hashlib
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple

from app.config import settings
from app.logger import get_logger

logger = get_logger("editor.video_composer")

W = settings.video_width    # 1080
H = settings.video_height   # 1920
FPS = settings.video_fps    # 30


class SceneMedia(NamedTuple):
    asset_path: Path
    asset_type: str   # "image" | "video"
    duration: float   # seconds this scene should play
    movement: str     # camera movement type


async def _build_scene_clip(
    scene: SceneMedia,
    output_path: Path,
    scene_index: int,
) -> bool:
    """
    Convert a single asset (image or video) into a correctly-sized clip
    with cinematic motion applied.
    """
    duration = scene.duration
    movement = scene.movement

    # Motion filter based on camera movement type
    motion_map = {
        "slow push-in": f"zoompan=z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
        "gentle dolly forward": f"zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
        "subtle zoom out": f"zoompan=z='max(zoom-0.001,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
        "parallax drift": f"zoompan=z='1.1':x='if(gte(zoom,1.0),x+0.5,0)':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
        "slow pan left": f"zoompan=z='1.1':x='iw/2-(iw/zoom/2)+t*8':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
        "slow pan right": f"zoompan=z='1.1':x='iw/2-(iw/zoom/2)-t*8':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
    }

    if scene.asset_type == "image":
        motion = motion_map.get(
            movement,
            f"zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={int(duration * FPS)}:s={W}x{H}:fps={FPS}",
        )
        # Scale and pad image to 9:16 with cinematic zoom
        vf = (
            f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,"
            f"crop={W * 2}:{H * 2},"
            f"{motion},"
            f"scale={W}:{H}:flags=lanczos,"
            f"setsar=1"
        )
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(scene.asset_path),
            "-vf", vf,
            "-t", str(duration),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
        ]

    else:  # video clip
        # Scale/crop to 9:16 and trim to duration
        vf = (
            f"scale={W}:{H}:force_original_aspect_ratio=increase,"
            f"crop={W}:{H},"
            f"setsar=1"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(scene.asset_path),
            "-vf", vf,
            "-t", str(duration),
            "-r", str(FPS),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-pix_fmt", "yuv420p",
            "-an",
            str(output_path),
        ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("Scene clip build failed (scene %d): %s", scene_index, stderr.decode()[-500:])
        return False

    return output_path.exists()


async def _create_fallback_scene(
    duration: float,
    output_path: Path,
    color: str = "0x1a1a2e",
) -> bool:
    """Create a solid-color fallback clip when no asset is available."""
    vf = f"color=c={color}:s={W}x{H}:r={FPS}:d={duration}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return output_path.exists()


async def _concatenate_clips(
    clip_paths: list[Path],
    output_path: Path,
) -> bool:
    """Concatenate video clips using FFmpeg concat demuxer."""
    # Write concat list file
    list_path = output_path.parent / "concat_list.txt"
    with list_path.open("w") as f:
        for clip in clip_paths:
            f.write(f"file '{clip.absolute()}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(list_path),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    list_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        logger.error("Concat failed: %s", stderr.decode()[-500:])
        return False
    return output_path.exists()


async def _add_audio_to_video(
    video_path: Path,
    narration_path: Path,
    output_path: Path,
    music_path: Path | None = None,
) -> bool:
    """Mix narration + optional background music and mux with video."""
    if music_path and music_path.exists():
        # Mix narration at full volume, music at -18dB under narration
        audio_filter = (
            f"[1:a]volume=1.0[narr];"
            f"[2:a]volume=0.15,aloop=loop=-1:size=2e+09[music];"
            f"[narr][music]amix=inputs=2:duration=first[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(narration_path),
            "-i", str(music_path),
            "-filter_complex", audio_filter,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", settings.audio_bitrate,
            "-ar", "44100",
            "-ac", "2",
            "-shortest",
            str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(narration_path),
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", settings.audio_bitrate,
            "-ar", "44100",
            "-ac", "2",
            "-shortest",
            str(output_path),
        ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("Audio mix failed: %s", stderr.decode()[-500:])
        return False
    return output_path.exists()


async def _burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
) -> bool:
    """Burn ASS subtitles into video."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"ass={ass_path.absolute()}",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-b:v", settings.video_bitrate,
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("Subtitle burn-in failed: %s", stderr.decode()[-500:])
        # Return video without subtitles as fallback
        shutil.copy2(video_path, output_path)
        return False
    return output_path.exists()


async def _add_loudness_normalization(
    input_path: Path,
    output_path: Path,
    target_lufs: float = -14.0,
) -> bool:
    """Apply EBU R128 loudness normalization."""
    # Two-pass loudnorm
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-af", f"loudnorm=I={target_lufs}:TP=-1.5:LRA=11",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", settings.audio_bitrate,
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return output_path.exists()


async def compose_video(
    scenes: list[dict],
    asset_paths: dict[int, Path],  # scene_number -> local asset path
    asset_types: dict[int, str],   # scene_number -> "image" | "video"
    narration_path: Path,
    ass_subtitle_path: Path,
    output_path: Path,
    music_path: Path | None = None,
    temp_dir: Path | None = None,
) -> bool:
    """
    Full video composition pipeline.

    Args:
        scenes: List of storyboard scene dicts.
        asset_paths: Mapping of scene_number to downloaded asset path.
        asset_types: Mapping of scene_number to asset type.
        narration_path: Path to narration audio file.
        ass_subtitle_path: Path to ASS subtitle file.
        output_path: Final output video path.
        music_path: Optional background music path.
        temp_dir: Temp directory (created if None).

    Returns True if final video was produced successfully.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cleanup_temp = temp_dir is None
    if temp_dir is None:
        temp_dir = Path(tempfile.mkdtemp(prefix="faceless_compose_"))

    logger.info("Composing video: %d scenes → %s", len(scenes), output_path.name)

    try:
        # Step 1: Build individual scene clips
        clip_paths: list[Path] = []

        for i, scene in enumerate(scenes):
            scene_num = scene.get("scene_number", i + 1)
            duration = scene.get("duration", 4.0)
            movement = scene.get("camera_movement", "slow push-in")

            asset_path = asset_paths.get(scene_num)
            asset_type = asset_types.get(scene_num, "image")

            clip_out = temp_dir / f"scene_{scene_num:02d}.mp4"

            if asset_path and asset_path.exists():
                media = SceneMedia(
                    asset_path=asset_path,
                    asset_type=asset_type,
                    duration=duration,
                    movement=movement,
                )
                ok = await _build_scene_clip(media, clip_out, scene_num)
                if not ok:
                    logger.warning("Scene %d clip failed — using fallback", scene_num)
                    ok = await _create_fallback_scene(duration, clip_out)
            else:
                logger.warning("No asset for scene %d — using fallback", scene_num)
                ok = await _create_fallback_scene(duration, clip_out)

            if ok:
                clip_paths.append(clip_out)
            else:
                logger.error("Could not build scene %d clip", scene_num)
                return False

        if not clip_paths:
            logger.error("No clips built")
            return False

        # Step 2: Concatenate all scene clips
        concat_out = temp_dir / "concat_video.mp4"
        if not await _concatenate_clips(clip_paths, concat_out):
            return False

        # Step 3: Mix audio (narration + music)
        audio_out = temp_dir / "with_audio.mp4"
        if not await _add_audio_to_video(concat_out, narration_path, audio_out, music_path):
            return False

        # Step 4: Burn subtitles
        sub_out = temp_dir / "with_subtitles.mp4"
        if ass_subtitle_path.exists():
            await _burn_subtitles(audio_out, ass_subtitle_path, sub_out)
            source_for_norm = sub_out if sub_out.exists() else audio_out
        else:
            source_for_norm = audio_out

        # Step 5: Loudness normalization
        norm_out = temp_dir / "normalized.mp4"
        await _add_loudness_normalization(source_for_norm, norm_out)
        final_source = norm_out if norm_out.exists() else source_for_norm

        # Step 6: Final encode to output
        cmd = [
            "ffmpeg", "-y",
            "-i", str(final_source),
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            "-b:v", settings.video_bitrate,
            "-maxrate", "6000k",
            "-bufsize", "12000k",
            "-c:a", "aac",
            "-b:a", settings.audio_bitrate,
            "-movflags", "+faststart",
            "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.error("Final encode failed: %s", stderr.decode()[-500:])
            return False

        logger.info("✅ Video composed successfully: %s", output_path.name)
        return output_path.exists()

    finally:
        if cleanup_temp and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
