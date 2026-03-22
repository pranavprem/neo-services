"""Ken Burns video compositor — transforms static images into vertical reels via ffmpeg."""

import asyncio
import logging
import os
import random
import shutil
import tempfile

logger = logging.getLogger(__name__)

# Output specs
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
FPS = 30
MIN_DURATION = 5
MAX_DURATION = 8

MOTION_TYPES = ["zoom_in", "zoom_out", "pan_left", "pan_right", "zoom_pan"]


def _get_duration() -> int:
    """Random duration between MIN and MAX seconds."""
    return random.randint(MIN_DURATION, MAX_DURATION)


def _build_zoompan_filter(motion: str, duration: int) -> str:
    """Build the ffmpeg zoompan filter string for the given motion type.

    The zoompan filter operates on frames: d=duration*fps total frames.
    z = zoom level (1.0 = no zoom), x/y = top-left crop position.
    All expressions use 'on' (current output frame number) for animation.
    """
    total_frames = duration * FPS
    s = f"{OUTPUT_WIDTH}x{OUTPUT_HEIGHT}"

    if motion == "zoom_in":
        # Start at z=1.0, slowly zoom to 1.3 centered
        return (
            f"zoompan=z='min(1+0.3*on/{total_frames},1.3)'"
            f":d={total_frames}"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)'"
            f":s={s}:fps={FPS}"
        )

    if motion == "zoom_out":
        # Start zoomed in at 1.3, pull out to 1.0
        return (
            f"zoompan=z='max(1.3-0.3*on/{total_frames},1.0)'"
            f":d={total_frames}"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='ih/2-(ih/zoom/2)'"
            f":s={s}:fps={FPS}"
        )

    if motion == "pan_left":
        # Pan from right to left at constant zoom 1.2
        # max_x = iw - iw/zoom; start at max_x, end at 0
        return (
            f"zoompan=z='1.2'"
            f":d={total_frames}"
            f":x='(iw-iw/1.2)*({total_frames}-on)/{total_frames}'"
            f":y='ih/2-(ih/zoom/2)'"
            f":s={s}:fps={FPS}"
        )

    if motion == "pan_right":
        # Pan from left to right at constant zoom 1.2
        # max_x = iw - iw/zoom; start at 0, end at max_x
        return (
            f"zoompan=z='1.2'"
            f":d={total_frames}"
            f":x='(iw-iw/1.2)*on/{total_frames}'"
            f":y='ih/2-(ih/zoom/2)'"
            f":s={s}:fps={FPS}"
        )

    # zoom_pan — zoom in while panning slightly right and down
    return (
        f"zoompan=z='min(1+0.25*on/{total_frames},1.25)'"
        f":d={total_frames}"
        f":x='(iw-iw/zoom)/2+((iw-iw/zoom)/4)*on/{total_frames}'"
        f":y='(ih-ih/zoom)/2+((ih-ih/zoom)/6)*on/{total_frames}'"
        f":s={s}:fps={FPS}"
    )


async def create_ken_burns(
    image_path: str,
    output_path: str,
    motion: str | None = None,
    duration: int | None = None,
) -> str:
    """Create a Ken Burns effect video from a single image.

    Args:
        image_path: Path to input image (1024x1024 PNG).
        output_path: Path for output MP4.
        motion: Motion type or None for random selection.
        duration: Duration in seconds or None for random (5-8s).

    Returns:
        Path to the output MP4 file.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Input image not found: {image_path}")

    if motion is None:
        motion = random.choice(MOTION_TYPES)
    if motion not in MOTION_TYPES:
        raise ValueError(f"Unknown motion type: {motion}. Must be one of {MOTION_TYPES}")
    if duration is None:
        duration = _get_duration()

    zoompan = _build_zoompan_filter(motion, duration)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", zoompan,
        "-t", str(duration),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info("Ken Burns: %s motion=%s duration=%ds → %s", image_path, motion, duration, output_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}): {stderr.decode()[-500:]}")

    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        raise RuntimeError(f"ffmpeg produced no output at {output_path}")

    logger.info("Ken Burns complete: %s (%.1f KB)", output_path, os.path.getsize(output_path) / 1024)
    return output_path


async def merge_audio_video(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    """Merge an audio track with a video file. Audio is trimmed/padded to match video duration.

    Args:
        video_path: Path to input MP4 (video only).
        audio_path: Path to input WAV audio.
        output_path: Path for merged output MP4.

    Returns:
        Path to the merged MP4 file.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",  # resample to 44.1kHz (MusicGen outputs 32kHz which AAC doesn't like)
        "-ac", "2",      # stereo
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info("Merging audio+video → %s", output_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg merge failed (exit {proc.returncode}): {stderr.decode()[-500:]}")

    logger.info("Merge complete: %s (%.1f KB)", output_path, os.path.getsize(output_path) / 1024)
    return output_path


async def concatenate_videos(
    video_paths: list[str],
    output_path: str,
) -> str:
    """Concatenate multiple videos into one using ffmpeg concat demuxer.

    Args:
        video_paths: List of MP4 file paths to concatenate.
        output_path: Path for combined output MP4.

    Returns:
        Path to the combined MP4.
    """
    if len(video_paths) < 2:
        raise ValueError("Need at least 2 videos to concatenate")

    # Write concat list to temp file
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    try:
        for vp in video_paths:
            tmp.write(f"file '{os.path.abspath(vp)}'\n")
        tmp.close()

        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", tmp.name,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg concat failed: {stderr.decode()[-500:]}")

        logger.info("Concatenated %d videos → %s", len(video_paths), output_path)
        return output_path
    finally:
        os.unlink(tmp.name)
