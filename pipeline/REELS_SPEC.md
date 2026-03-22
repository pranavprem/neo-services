# Reels Compositor + MusicGen — Specification

## Overview

Add two new capabilities to the content pipeline:
1. **Ken Burns video compositor** — transforms static images into Instagram Reels with smooth zoom/pan motion
2. **MusicGen audio** — generates short background music tracks matched to the content mood

These run AFTER image generation completes for each post, producing a ready-to-post vertical video (9:16).

## Ken Burns Compositor (`pipeline/video_compositor.py`)

Takes a completed image and creates a short video with cinematic motion.

### Motion Types (randomly selected per image):
1. **Slow zoom in** — starts full image, slowly zooms to center (most common)
2. **Slow zoom out** — starts zoomed in, pulls out to reveal full image
3. **Pan left to right** — slow horizontal sweep
4. **Pan right to left** — reverse horizontal sweep
5. **Zoom in + slight pan** — combination for more dynamic feel

### Technical Requirements:
- **Input:** 1024x1024 PNG image
- **Output:** 1080x1920 vertical video (9:16 for Instagram Reels)
- **Duration:** 5-8 seconds per image
- **FPS:** 30
- **Codec:** H.264 (mp4)
- **Tool:** ffmpeg (already installed via brew)
- **Approach:** Use ffmpeg's zoompan filter

### Implementation:
```bash
# Example ffmpeg zoompan command for slow zoom in:
ffmpeg -loop 1 -i image.png -vf "zoompan=z='min(zoom+0.001,1.3)':d=180:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=30" -t 6 -c:v libx264 -pix_fmt yuv420p output.mp4
```

### For Carousels/Grids:
- Generate a video for each image in the carousel
- Optionally concatenate into one longer video with crossfade transitions
- Grid posts: make video of the grid image, then individual zooms of each cell

## MusicGen Service (`pipeline/music_gen.py`)

Generates short instrumental background tracks using Facebook's MusicGen model via Ollama... wait, MusicGen doesn't run on Ollama.

### Approach:
Use the `audiocraft` library (Meta's MusicGen) directly:
```python
from audiocraft.models import MusicGen
model = MusicGen.get_pretrained('facebook/musicgen-small')  # ~1.5GB, fits in 24GB
model.set_generation_params(duration=8)
wav = model.generate(['epic cinematic ambient electronic music, futuristic, atmospheric'])
```

### Music Prompt Mapping (category → music style):
| Category | Music Style |
|----------|------------|
| futuristic_concept | "epic cinematic ambient electronic, futuristic synths, atmospheric" |
| dream_space | "calm ambient music, ethereal pads, dreamy atmosphere, soft piano" |
| what_if | "upbeat electronic music, creative energy, tech vibes, inspiring" |
| pick_your | "energetic electronic beat, engaging, social media music, catchy" |
| then_vs_2040 | "evolving electronic music, transitioning from retro to futuristic" |
| gf_knows | "playful upbeat pop electronic, fun, relationship vibes" |
| custom | "cinematic ambient electronic, modern, atmospheric" |

### Technical Requirements:
- **Model:** musicgen-small (~1.5GB) or musicgen-medium (~3.5GB)
- **Duration:** Match video length (5-8 seconds)
- **Format:** WAV → merge with video via ffmpeg
- **When to run:** AFTER image generation (Flux not using GPU), BEFORE video compositing
- **Memory:** musicgen-small fits easily alongside other services when Flux is idle

## Pipeline Integration

### New post-completion flow:
```
Image Complete → Music Generation (8 sec) → Ken Burns Video → Merge Audio+Video → Post to Discord
```

### In renderer.py `_check_post_complete()`:
1. After all images are composited (existing flow)
2. Generate music track based on category
3. For each image: create Ken Burns video
4. Merge audio with video
5. For carousels: optionally create combined reel
6. Save .mp4 files alongside .png files in output directory
7. Post video to Discord via webhook (upload as attachment)

## File Structure Addition:
```
pipeline/
├── video_compositor.py    # Ken Burns video generation via ffmpeg
├── music_gen.py           # MusicGen wrapper for background music
└── output/
    └── 2026-03-22/
        └── post_001/
            ├── image_0.png
            ├── music.wav           # Generated background track
            ├── reel_0.mp4          # Image 0 with Ken Burns + music
            ├── reel_combined.mp4   # All images stitched (carousel)
            └── caption.txt
```

## Important Notes:
- MusicGen and Flux CANNOT run simultaneously (GPU memory). Generate music ONLY when Flux is between images.
- musicgen-small is preferred over medium for memory safety
- ffmpeg is already installed on the Mac mini
- Ken Burns processing is CPU-based (ffmpeg), can run while MusicGen loads/unloads
- Keep MusicGen loaded only during generation, then free memory for Flux
- Video files should also be posted to Discord via webhook
- The audiocraft library needs to be added to requirements.txt
