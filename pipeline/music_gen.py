"""MusicGen wrapper — generates short background music tracks for reels.

Uses MusicGPT CLI (brew install gabotechs/taps/musicgpt) which runs MusicGen
via ONNX runtime. No Python ML deps needed — just a binary.
"""

import asyncio
import logging
import os
import shutil

logger = logging.getLogger(__name__)

# Category → music prompt mapping
MUSIC_STYLES: dict[str, str] = {
    "futuristic_concept": "epic cinematic ambient electronic, futuristic synths, atmospheric, dark",
    "dream_space": "calm ambient music, ethereal pads, dreamy atmosphere, soft piano, relaxing",
    "what_if": "upbeat electronic music, creative energy, tech vibes, inspiring, modern",
    "pick_your": "energetic electronic beat, engaging, social media music, catchy, upbeat",
    "then_vs_2040": "evolving electronic music, transitioning from retro to futuristic synths",
    "gf_knows": "playful upbeat pop electronic, fun, trendy, social media vibe",
    "custom": "cinematic ambient electronic, modern, atmospheric, inspiring",
    "meme": "catchy quirky electronic beat, playful, meme energy, fun",
}

DEFAULT_STYLE = "cinematic ambient electronic, modern, atmospheric"

# MusicGPT binary
MUSICGPT_BIN = shutil.which("musicgpt") or "/opt/homebrew/bin/musicgpt"


async def generate_music(
    category: str,
    duration: float,
    output_path: str,
    custom_prompt: str | None = None,
) -> str:
    """Generate background music for a reel using MusicGPT CLI.

    Args:
        category: Content category for automatic style selection.
        duration: Duration in seconds (will be rounded up).
        output_path: Where to save the WAV file.
        custom_prompt: Override the automatic style prompt.

    Returns:
        Path to WAV file, or empty string on failure.
    """
    if not os.path.exists(MUSICGPT_BIN):
        logger.error(f"MusicGPT not found at {MUSICGPT_BIN}. Install with: brew install gabotechs/taps/musicgpt")
        return ""

    prompt = custom_prompt or MUSIC_STYLES.get(category, DEFAULT_STYLE)
    secs = max(int(duration), 3)

    cmd = [
        MUSICGPT_BIN,
        prompt,
        "--secs", str(secs),
        "--output", output_path,
        "--no-playback",
        "--no-interactive",
        "--model", "medium",
    ]

    logger.info(f"Generating {secs}s music: '{prompt[:60]}...'")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=900)  # 15 min for medium model

        if proc.returncode == 0 and os.path.exists(output_path):
            size_kb = os.path.getsize(output_path) / 1024
            logger.info(f"Music saved to {output_path} ({size_kb:.0f} KB)")
            return output_path
        else:
            logger.error(f"MusicGPT failed (code {proc.returncode}): {stderr.decode()[:200]}")
            return ""

    except asyncio.TimeoutError:
        logger.error("MusicGPT timed out after 300s")
        return ""
    except Exception as e:
        logger.error(f"Music generation failed: {e}")
        return ""
