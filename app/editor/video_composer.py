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
import json
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
    duration = max(scene.duration, 1.0)   # guard against sub-second leftovers
    movement = scene.movement
    n_frames = max(int(duration * FPS), FPS)  # at least 1 second of frames

    # Motion filter based on camera movement type
    motion_map = {
        "slow push-in": f"zoompan=z='min(zoom+0.0015,1.5)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
        "gentle dolly forward": f"zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
        "subtle zoom out": f"zoompan=z='max(zoom-0.001,1.0)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
        "parallax drift": f"zoompan=z='1.1':x='if(gte(zoom,1.0),x+0.5,0)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
        "slow pan left": f"zoompan=z='1.1':x='iw/2-(iw/zoom/2)+t*8':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
        "slow pan right": f"zoompan=z='1.1':x='iw/2-(iw/zoom/2)-t*8':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
    }

    if scene.asset_type == "image":
        motion = motion_map.get(
            movement,
            f"zoompan=z='min(zoom+0.001,1.3)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={n_frames}:s={W}x{H}:fps={FPS}",
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
            # normalize=0: amix otherwise halves the narration when music is present
            f"[narr][music]amix=inputs=2:duration=first:normalize=0[aout]"
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


def _srt_to_seconds(ts: str) -> float:
    """Convert SRT timestamp (HH:MM:SS,mmm) to float seconds."""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def _parse_srt(srt_path: Path) -> list[dict]:
    """Parse an SRT file into a list of {start, end, text} dicts."""
    segments: list[dict] = []
    text = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = [b.strip() for b in text.strip().split("\n\n") if b.strip()]
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        # Line 0: index, Line 1: timestamps, Line 2+: text
        try:
            start_ts, end_ts = lines[1].split(" --> ")
            start = _srt_to_seconds(start_ts.strip())
            end = _srt_to_seconds(end_ts.strip())
            caption = " ".join(lines[2:]).replace("'", "\\'").replace(":", "\\:")
            # Wrap long captions
            words = caption.split()
            if len(words) > 6:
                mid = len(words) // 2
                caption = " ".join(words[:mid]) + "\\n" + " ".join(words[mid:])
            segments.append({"start": start, "end": end, "text": caption})
        except Exception:
            continue
    return segments


def _load_font(size: int):
    """Load a bold system TTF; fall back to PIL's small default font."""
    from PIL import ImageFont

    for font_path in [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]:
        try:
            return ImageFont.truetype(font_path, size)
        except (IOError, OSError):
            continue
    logger.warning("No TTF font found — using PIL default (smaller)")
    return ImageFont.load_default()


async def _burn_karaoke_captions(
    video_path: Path,
    words_path: Path,
    output_path: Path,
) -> bool:
    """Burn word-by-word highlighted captions (the current word turns yellow).

    One PNG is rendered per spoken word, the PNGs are sequenced with ffmpeg's
    concat demuxer using word timings, and the result is overlaid in a single
    pass. Needs no libass/freetype, which Homebrew's ffmpeg lacks.
    """
    from PIL import Image, ImageDraw

    segments = json.loads(words_path.read_text(encoding="utf-8"))
    if not segments:
        return False

    font = _load_font(76)
    strip_w, strip_h, line_gap, max_w = W, 300, 14, W - 140
    space = ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(" ", font=font)

    png_dir = output_path.parent / "karaoke_pngs"
    png_dir.mkdir(exist_ok=True)
    blank = png_dir / "blank.png"
    Image.new("RGBA", (strip_w, strip_h), (0, 0, 0, 0)).save(blank)

    def render(words: list[str], active: int, dest: Path) -> None:
        img = Image.new("RGBA", (strip_w, strip_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Lay words out into centred lines
        lines: list[list[int]] = [[]]
        for i, word in enumerate(words):
            trial = " ".join(words[j] for j in lines[-1] + [i])
            if lines[-1] and draw.textlength(trial, font=font) > max_w:
                lines.append([])
            lines[-1].append(i)
        line_h = font.size + line_gap
        y = (strip_h - line_h * len(lines)) // 2
        for line in lines:
            line_w = draw.textlength(" ".join(words[j] for j in line), font=font)
            x = (strip_w - line_w) / 2
            for j in line:
                colour = (255, 221, 0, 255) if j == active else (255, 255, 255, 255)
                draw.text((x, y), words[j], font=font, fill=colour,
                          stroke_width=6, stroke_fill=(0, 0, 0, 255))
                x += draw.textlength(words[j], font=font) + space
            y += line_h
        img.save(dest)

    # Build concat list: one entry per word, blanks for silent gaps
    entries: list[tuple[Path, float]] = []
    t = 0.0
    n = 0
    for seg in segments:
        words = [w["text"].upper() for w in seg["words"]]
        for k, w in enumerate(seg["words"]):
            start = max(w["start"], t)
            if start - t > 0.05:
                entries.append((blank, start - t))
            end = seg["words"][k + 1]["start"] if k + 1 < len(seg["words"]) else w["end"]
            end = max(end, start + 0.05)
            png = png_dir / f"w{n:04d}.png"
            render(words, k, png)
            entries.append((png, end - start))
            t, n = end, n + 1
    entries.append((blank, 1.0))

    list_path = png_dir / "list.txt"
    lines = [f"file '{p}'\nduration {d:.3f}" for p, d in entries]
    lines.append(f"file '{entries[-1][0]}'")  # concat demuxer needs the last file repeated
    list_path.write_text("\n".join(lines), encoding="utf-8")

    y = int(H * 0.62)  # above the Shorts UI that covers the bottom of the frame
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-filter_complex",
        f"[1:v]fps={FPS},format=rgba[cap];[0:v][cap]overlay=0:{y}:eof_action=pass[v]",
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    shutil.rmtree(png_dir, ignore_errors=True)

    if proc.returncode != 0:
        logger.error("Karaoke caption overlay failed: %s", stderr.decode()[-800:])
        return False

    logger.info("✅ Karaoke captions burned (%d words)", n)
    return output_path.exists()


async def _burn_hook_text(
    video_path: Path,
    hook_text: str,
    output_path: Path,
    seconds: float = 2.5,
) -> bool:
    """Overlay the hook as big text on the opening seconds — the swipe-or-stay moment."""
    from PIL import Image, ImageDraw

    font = _load_font(92)
    draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    lines: list[str] = []
    for word in hook_text.upper().split():
        trial = f"{lines[-1]} {word}" if lines else word
        if lines and draw.textlength(trial, font=font) <= W - 160:
            lines[-1] = trial
        else:
            lines.append(word)
    text = "\n".join(lines)

    bbox = draw.multiline_textbbox((0, 0), text, font=font, align="center", spacing=14)
    tw, th = int(bbox[2] - bbox[0]), int(bbox[3] - bbox[1])  # Pillow may return floats
    pad = 36
    img = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, img.width, img.height), radius=28, fill=(0, 0, 0, 170))
    draw.multiline_text(
        (pad - bbox[0], pad - bbox[1]), text, font=font, align="center", spacing=14,
        fill=(255, 221, 0, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255),
    )
    png_path = output_path.with_suffix(".hook.png")
    img.save(str(png_path), "PNG")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(png_path),
        "-filter_complex",
        f"[0:v][1:v]overlay=(W-w)/2:H*0.22:enable='between(t,0,{seconds})'[v]",
        "-map", "[v]",
        "-map", "0:a",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "copy",
        "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    png_path.unlink(missing_ok=True)

    if proc.returncode != 0:
        logger.error("Hook text overlay failed: %s", stderr.decode()[-500:])
        return False

    logger.info("✅ Hook text burned: %s", hook_text)
    return output_path.exists()


async def _burn_subtitles_pillow(
    video_path: Path,
    srt_path: Path,
    output_path: Path,
) -> bool:
    """Burn captions using Pillow PNG overlays + FFmpeg overlay filter.

    Works on ANY FFmpeg build — no libass, no libfreetype needed.
    For each SRT segment:
      1. Render white text with black stroke on transparent PNG (Pillow)
      2. Use FFmpeg overlay filter to composite PNG onto video at [start,end]
    """
    from PIL import Image, ImageDraw, ImageFont

    segments = _parse_srt(srt_path)
    if not segments:
        logger.warning("No subtitle segments — shipping without captions")
        shutil.copy2(video_path, output_path)
        return False

    png_dir = output_path.parent / "caption_pngs"
    png_dir.mkdir(exist_ok=True)

    font = _load_font(56)

    overlay_specs: list[dict] = []

    for idx, seg in enumerate(segments):
        text = seg["text"].replace("\\n", " ").replace("\n", " ").replace("\\'", "'").replace("\\:", ":")
        png_path = png_dir / f"cap_{idx:03d}.png"

        # Measure text size
        dummy = Image.new("RGBA", (1, 1))
        draw = ImageDraw.Draw(dummy)

        # Word-wrap so long captions don't run off the frame edges
        lines: list[str] = []
        for word in text.split():
            trial = f"{lines[-1]} {word}" if lines else word
            if lines and draw.textlength(trial, font=font) <= W - 120:
                lines[-1] = trial
            else:
                lines.append(word)
        text = "\n".join(lines)

        bbox = draw.textbbox((0, 0), text, font=font, align="center")
        tw = bbox[2] - bbox[0] + 40   # padding
        th = bbox[3] - bbox[1] + 24

        # Create transparent canvas matching video width, tall enough for text
        img = Image.new("RGBA", (W, th + 20), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        tx = (W - tw) // 2 + 20
        ty = 10

        # Draw black stroke (8-direction outline)
        stroke = 4
        for dx in range(-stroke, stroke + 1):
            for dy in range(-stroke, stroke + 1):
                if dx == 0 and dy == 0:
                    continue
                draw.text((tx + dx, ty + dy), text, font=font,
                          fill=(0, 0, 0, 230), align="center")
        # Draw white text
        draw.text((tx, ty), text, font=font, fill=(255, 255, 255, 255), align="center")

        img.save(str(png_path), "PNG")
        overlay_specs.append({
            "png": png_path,
            "start": seg["start"],
            "end": seg["end"],
            "y": int(H * 0.88) - img.height,   # lower third, grows upward when wrapped
        })

    # Build FFmpeg filter_complex with one overlay per segment
    # Input 0 = video, Inputs 1..N = caption PNGs
    inputs: list[str] = ["-i", str(video_path)]
    for spec in overlay_specs:
        inputs += ["-i", str(spec["png"])]

    # Chain overlays: [v0][1]overlay → [v1], [v1][2]overlay → [v2], ...
    filter_parts: list[str] = []
    n = len(overlay_specs)
    for i, spec in enumerate(overlay_specs):
        in_label = f"[v{i}]" if i > 0 else "[0:v]"
        out_label = f"[v{i+1}]"
        x = f"(W-w)/2"
        y = spec["y"]
        t0, t1 = spec["start"], spec["end"]
        filter_parts.append(
            f"{in_label}[{i+1}:v]overlay={x}:{y}:enable='between(t,{t0:.3f},{t1:.3f})'{out_label}"
        )

    filter_complex = ";".join(filter_parts)
    final_label = f"[v{n}]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", final_label,
        "-map", "0:a",
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

    # Cleanup PNGs
    shutil.rmtree(png_dir, ignore_errors=True)

    if proc.returncode != 0:
        logger.error("Pillow subtitle overlay failed: %s", stderr.decode()[-800:])
        shutil.copy2(video_path, output_path)
        return False

    logger.info("✅ Captions burned via Pillow overlay (%d segments)", len(segments))
    return output_path.exists()


async def _burn_subtitles(
    video_path: Path,
    ass_path: Path,
    output_path: Path,
    srt_path: Path | None = None,
) -> bool:
    """Burn subtitles — tries ASS first, falls back to SRT drawtext."""
    # Try ASS (requires libass in FFmpeg build)
    safe_ass = output_path.parent / "subs.ass"
    shutil.copy2(ass_path, safe_ass)
    safe_path_str = str(safe_ass.absolute()).replace("\\", "/").replace(":", "\\:")

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", f"ass={safe_path_str}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-c:a", "copy", "-b:v", settings.video_bitrate, "-pix_fmt", "yuv420p",
        str(output_path),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode == 0 and output_path.exists():
        logger.info("✅ Subtitles burned via ass= filter")
        return True

    logger.warning("ass= filter unavailable (libass not compiled in) — trying drawtext fallback")

    # Fallback: drawtext from SRT
    if srt_path and srt_path.exists():
        return await _burn_subtitles_pillow(video_path, srt_path, output_path)

    # Last resort: video without captions
    logger.warning("No SRT fallback available — shipping video without captions")
    shutil.copy2(video_path, output_path)
    return False


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
    srt_subtitle_path: Path | None = None,
    hook_text: str | None = None,
    caption_words_path: Path | None = None,
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

        # Step 4: Burn subtitles (ASS with libass fallback → SRT drawtext → no captions)
        sub_out = temp_dir / "with_subtitles.mp4"
        if caption_words_path and caption_words_path.exists() and await _burn_karaoke_captions(
            audio_out, caption_words_path, sub_out,
        ):
            source_for_norm = sub_out
        elif ass_subtitle_path.exists():
            await _burn_subtitles(
                audio_out, ass_subtitle_path, sub_out,
                srt_path=srt_subtitle_path,
            )
            source_for_norm = sub_out if sub_out.exists() else audio_out
        elif srt_subtitle_path and srt_subtitle_path.exists():
            await _burn_subtitles_pillow(audio_out, srt_subtitle_path, sub_out)
            source_for_norm = sub_out if sub_out.exists() else audio_out
        else:
            source_for_norm = audio_out

        # Step 4b: Hook text card over the opening seconds
        if hook_text:
            hook_out = temp_dir / "with_hook.mp4"
            if await _burn_hook_text(source_for_norm, hook_text, hook_out):
                source_for_norm = hook_out

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
