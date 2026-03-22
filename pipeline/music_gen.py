"""MusicGen wrapper — generates short background music tracks for reels.

Loads the model on demand and unloads after generation to free GPU memory for Flux.
Uses Meta's audiocraft library with the musicgen-small model (~1.5GB).
"""

import asyncio
import gc
import logging
import os

import torch
import torchaudio

logger = logging.getLogger(__name__)

# Category → music prompt mapping
MUSIC_STYLES: dict[str, str] = {
    "futuristic_concept": "epic cinematic ambient electronic, futuristic synths, atmospheric",
    "dream_space": "calm ambient music, ethereal pads, dreamy atmosphere, soft piano",
    "what_if": "upbeat electronic music, creative energy, tech vibes, inspiring",
    "pick_your": "energetic electronic beat, engaging, social media music, catchy",
    "then_vs_2040": "evolving electronic music, transitioning from retro to futuristic",
    "gf_knows": "playful upbeat pop electronic, fun, relationship vibes",
    "custom": "cinematic ambient electronic, modern, atmospheric",
    "meme": "catchy quirky electronic beat, playful, meme energy",
}

DEFAULT_STYLE = "cinematic ambient electronic, modern, atmospheric"

# Model singleton — loaded on demand, unloaded after generation
_model = None


def _load_model():
    """Load MusicGen model into GPU memory."""
    global _model
    if _model is not None:
        return _model

    logger.info("Loading MusicGen model (musicgen-small)...")
    from audiocraft.models import MusicGen

    _model = MusicGen.get_pretrained("facebook/musicgen-small")
    logger.info("MusicGen model loaded")
    return _model


def _unload_model():
    """Unload MusicGen model and free GPU memory for Flux."""
    global _model
    if _model is None:
        return

    logger.info("Unloading MusicGen model to free GPU memory...")
    del _model
    _model = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    elif hasattr(torch, "mps") and torch.backends.mps.is_available():
        # MPS (Apple Silicon) — clear cache
        torch.mps.empty_cache()
    logger.info("MusicGen model unloaded, GPU memory freed")


def _generate_sync(prompt: str, duration: float, output_path: str) -> str:
    """Synchronous generation — runs in a thread to avoid blocking the event loop."""
    model = _load_model()
    try:
        model.set_generation_params(duration=duration)
        wav = model.generate([prompt])
        # wav shape: (1, channels, samples) — squeeze batch dim
        audio = wav[0].cpu()
        torchaudio.save(output_path, audio, sample_rate=32000)
        logger.info("Generated music: %s (%.1fs, %.1f KB)",
                     output_path, duration, os.path.getsize(output_path) / 1024)
        return output_path
    finally:
        _unload_model()


async def generate_music(
    category: str,
    duration: float,
    output_path: str,
    custom_prompt: str | None = None,
) -> str:
    """Generate a background music track for a reel.

    Args:
        category: Content category (maps to a music style).
        duration: Track duration in seconds (should match video length).
        output_path: Where to save the WAV file.
        custom_prompt: Override the category-based prompt.

    Returns:
        Path to the generated WAV file.
    """
    prompt = custom_prompt or MUSIC_STYLES.get(category, DEFAULT_STYLE)
    logger.info("Generating music: category=%s duration=%.1fs prompt='%s'", category, duration, prompt)

    return await asyncio.to_thread(_generate_sync, prompt, duration, output_path)
