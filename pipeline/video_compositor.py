"""Ken Burns video compositor — transforms static images into vertical reels via ffmpeg.

Uses scale + animated crop (NOT zoompan) to avoid aspect ratio distortion.
Source images are upscaled to 2400px, then a 1080x1920 window slides/zooms across them.
"""

import asyncio
import logging
import os
import random

logger = logging.getLogger(__name__)

# Output specs
OUTPUT_WIDTH = 1080
OUTPUT_HEIGHT = 1920
FPS = 30
DEFAULT_DURATION = 16

# Scale target — 2400 gives 45% crop visibility and smooth pan
PAN_SCALE = 2400
ZOOM_SCALE = 2800  # larger for zoom effects to have range

MOTION_TYPES = ["pan_bounce", "pan_left", "pan_right", "zoom_in", "zoom_out", "diagonal_drift"]


def _build_filter(motion: str, duration: int, src_w: int, src_h: int) -> str:
    """Build ffmpeg filter chain: scale up → animate crop window → 9:16 output."""
    total_frames = duration * FPS
    half = total_frames // 2

    if motion in ("zoom_in", "zoom_out"):
        sw = ZOOM_SCALE
        sh = int(src_h * (ZOOM_SCALE / src_w))
        if sh < OUTPUT_HEIGHT:
            sh = ZOOM_SCALE
            sw = int(src_w * (ZOOM_SCALE / src_h))
        range_x = sw - OUTPUT_WIDTH
        range_y = sh - OUTPUT_HEIGHT

        if motion == "zoom_in":
            return (
                f"scale={sw}:{sh}:flags=lanczos,"
                f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
                f"x='{range_x}/2*({total_frames}-n)/{total_frames}':"
                f"y='{range_y}/2*({total_frames}-n)/{total_frames}'"
            )
        else:
            return (
                f"scale={sw}:{sh}:flags=lanczos,"
                f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
                f"x='{range_x}/2*n/{total_frames}':"
                f"y='{range_y}/2*n/{total_frames}'"
            )

    # Pan effects — scale to PAN_SCALE
    sw = PAN_SCALE
    sh = int(src_h * (PAN_SCALE / src_w))
    if sh < OUTPUT_HEIGHT:
        sh = PAN_SCALE
        sw = int(src_w * (PAN_SCALE / src_h))

    range_x = max(sw - OUTPUT_WIDTH, 0)
    range_y = max(sh - OUTPUT_HEIGHT, 0)
    cy = range_y // 2

    if motion == "pan_left":
        return (
            f"scale={sw}:{sh}:flags=lanczos,"
            f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
            f"x='{range_x}-{range_x}*n/{total_frames}':"
            f"y={cy}"
        )

    if motion == "pan_right":
        return (
            f"scale={sw}:{sh}:flags=lanczos,"
            f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
            f"x='{range_x}*n/{total_frames}':"
            f"y={cy}"
        )

    if motion == "diagonal_drift":
        # Pan right + slight downward drift
        return (
            f"scale={sw}:{sh}:flags=lanczos,"
            f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
            f"x='{range_x}*n/{total_frames}':"
            f"y='{cy}//2+{cy}*n/{total_frames}'"
        )

    # pan_bounce (default) — sweep right then back left
    return (
        f"scale={sw}:{sh}:flags=lanczos,"
        f"crop={OUTPUT_WIDTH}:{OUTPUT_HEIGHT}:"
        f"x='if(lt(n,{half}),{range_x}*n/{half},{range_x}*({total_frames}-n)/{half})':"
        f"y={cy}"
    )


async def create_ken_burns(
    image_path: str,
    output_path: str,
    motion: str | None = None,
    duration: int | None = None,
) -> str:
    """Create a Ken Burns effect video from a single image.

    No stretching — uses scale + animated crop for proper aspect ratio.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Input image not found: {image_path}")

    if motion is None:
        motion = random.choice(MOTION_TYPES)
    if motion not in MOTION_TYPES:
        raise ValueError(f"Unknown motion type: {motion}. Must be one of {MOTION_TYPES}")
    if duration is None:
        duration = DEFAULT_DURATION

    # Get source dimensions
    probe_cmd = [
        "ffprobe", "-v", "quiet", "-show_entries", "stream=width,height",
        "-of", "csv=p=0:s=x", image_path,
    ]
    probe = await asyncio.create_subprocess_exec(
        *probe_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await probe.communicate()
    dims = stdout.decode().strip().split("x")
    src_w, src_h = int(dims[0]), int(dims[1])

    vf = _build_filter(motion, duration, src_w, src_h)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf,
        "-t", str(duration),
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info("Ken Burns [%s] %ds → %s", motion, duration, output_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        logger.error("ffmpeg Ken Burns failed: %s", stderr.decode()[-500:])
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode})")

    return output_path


async def merge_audio_video(
    video_path: str,
    audio_path: str,
    output_path: str,
) -> str:
    """Merge an audio track with a video file."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-ac", "2",
        "-shortest",
        "-movflags", "+faststart",
        output_path,
    ]

    logger.info("Merging audio+video → %s", output_path)

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg merge failed (exit {proc.returncode}): {stderr.decode()[-500:]}")

    return output_path


async def concatenate_videos(
    video_paths: list[str],
    output_path: str,
) -> str:
    """Concatenate multiple videos into one."""
    if len(video_paths) == 1:
        import shutil
        shutil.copy2(video_paths[0], output_path)
        return output_path

    concat_file = output_path + ".concat.txt"
    with open(concat_file, "w") as f:
        for vp in video_paths:
            f.write(f"file '{os.path.abspath(vp)}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_file,
        "-c:v", "libx264",
        "-preset", "slow",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_path,
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    os.unlink(concat_file)

    if proc.returncode != 0:
        raise RuntimeError(f"Concat failed: {stderr.decode()[-300:]}")

    return output_path
