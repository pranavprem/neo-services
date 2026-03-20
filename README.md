# Neo Services

Local AI services optimized for Apple Silicon (M4 Mac mini): speech-to-text, text-to-speech, image generation, and a web UI.

- **Whisper STT** — transcribe audio files to text (Docker)
- **Piper TTS** — convert text to natural-sounding speech (Docker)
- **ImageGen** — generate images from text prompts via Flux Schnell (native, Metal GPU)
- **Content Generator** — web UI for image generation with social-media templates & img2img

## Architecture

```
                        ┌──────────────────────────────┐
                        │        neo-services           │
                        ├──────────────────────────────┤
   Docker Compose       │  ┌─────────┐  ┌──────────┐  │
   ─────────────────────│  │ Whisper  │  │  Piper   │  │
                        │  │  :9500   │  │  :9501   │  │
                        │  └─────────┘  └──────────┘  │
                        ├──────────────────────────────┤
   Native (Metal GPU)   │  ┌──────────────────────┐   │
   ─────────────────────│  │  ImageGen (mflux)    │   │
                        │  │  :9502               │   │
                        │  └──────────────────────┘   │
                        ├──────────────────────────────┤
   Web UI               │  ┌──────────────────────┐   │
   ─────────────────────│  │  Content Generator   │   │
                        │  │  :9503               │   │
                        │  └──────────────────────┘   │
                        └──────────────────────────────┘
```

**Why the split?** Whisper and Piper run fine in Docker containers. ImageGen uses [mflux](https://github.com/filipstrand/mflux) (MLX-based Flux) which requires direct Metal/GPU access — Docker on macOS doesn't expose Metal, so it runs natively. The web UI is a static single-page app served via Python.

## Quick Start

```bash
# Start everything
cd ~/git/neo-services

# 1. Whisper + Piper (Docker)
docker compose up -d --build

# 2. ImageGen (native — needs Apple Silicon)
cd imagegen && ./run.sh &

# 3. Content Generator Web UI
python3 web/serve.py &

# Open http://localhost:9503 in your browser
```

## Content Generator (Web UI)

A sleek, dark-mode single-page app for generating images with social-media presets.

**Features:**
- **Template presets** — Instagram Post/Story, Twitter/X, YouTube Thumbnail, Meme, Product Photo, Abstract Art — auto-set dimensions and style hints
- **Image-to-image (img2img)** — upload a reference image + set strength to guide the generation
- **Settings** — width, height, steps, seed (collapsible panel)
- **History** — recent generations stored in localStorage with click-to-reuse
- **Download, Regenerate, Copy Prompt** actions on every result
- **Responsive** — works on desktop and mobile

**Start:**

```bash
python3 web/serve.py
# → http://localhost:9503
```

Or simply:

```bash
cd web && python3 -m http.server 9503
```

## API Endpoints

### Whisper STT (port 9500)

**POST /transcribe** — Upload an audio file, get text back.

```bash
curl -X POST http://localhost:9500/transcribe \
  -F "file=@recording.ogg"

# {
#   "text": "Hello, how are you?",
#   "language": "en",
#   "segments": [{"start": 0.0, "end": 1.5, "text": "Hello, how are you?"}]
# }
```

**GET /health**

```bash
curl http://localhost:9500/health
# {"status": "ok", "model": "base"}
```

### Piper TTS (port 9501)

**POST /speak** — Send text, receive a WAV audio file.

```bash
curl -X POST http://localhost:9501/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from Piper!"}' \
  -o output.wav

# With a specific voice
curl -X POST http://localhost:9501/speak \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello!", "voice": "en_US-ryan-medium"}' \
  -o output.wav
```

**GET /voices** — List available voices.

```bash
curl http://localhost:9501/voices
# {"voices": [{"name": "en_US-lessac-medium", ...}], "default": "en_US-lessac-medium"}
```

**GET /health**

```bash
curl http://localhost:9501/health
# {"status": "ok", "default_voice": "en_US-lessac-medium"}
```

### ImageGen (port 9502)

The ImageGen API supports both **text-to-image** and **image-to-image** generation.

#### Text-to-Image (JSON)

**POST /generate/json** — Simple JSON endpoint for text-to-image only.

```bash
curl -X POST http://localhost:9502/generate/json \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a cyberpunk cat in neon tokyo"}' \
  -o output.png

# With custom parameters
curl -X POST http://localhost:9502/generate/json \
  -H "Content-Type: application/json" \
  -d '{"prompt": "mountain landscape at sunset", "width": 1024, "height": 768, "steps": 4, "seed": 42}' \
  -o landscape.png
```

#### Text-to-Image or Image-to-Image (Multipart Form)

**POST /generate** — Multipart form data. Accepts an optional reference image for img2img.

```bash
# Text-to-image via form
curl -X POST http://localhost:9502/generate \
  -F "prompt=a cyberpunk cat in neon tokyo" \
  -F "width=1024" \
  -F "height=1024" \
  -o output.png

# Image-to-image
curl -X POST http://localhost:9502/generate \
  -F "prompt=transform into watercolor painting" \
  -F "image=@reference.png" \
  -F "strength=0.6" \
  -F "width=1024" \
  -F "height=1024" \
  -o result.png
```

**Parameters:**
| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `prompt` | string | required | Text description of the image |
| `width` | int | 1024 | Output width (256–2048) |
| `height` | int | 1024 | Output height (256–2048) |
| `steps` | int | 4 | Inference steps (Schnell works well with 2–4) |
| `seed` | int | random | Seed for reproducibility |
| `image` | file | — | Reference image for img2img (optional) |
| `strength` | float | 0.4 | How much the reference image influences output (0.0–1.0, img2img only) |

**Response headers:**
- `X-Seed` — the seed used for generation
- `X-Generation-Time` — generation time in seconds

**GET /health**

```bash
curl http://localhost:9502/health
# {"status": "ok", "model": "flux-schnell", "quantize": 4, "device": "mps"}
```

**GET /models** — List available models.

```bash
curl http://localhost:9502/models
# {"models": ["flux-schnell"], "active": "flux-schnell", "quantize": 4}
```

## Configuration

Copy `.env.example` to `.env` and customize:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_PORT` | `9500` | Whisper service port |
| `WHISPER_MODEL` | `base` | Whisper model: `tiny`, `base`, `small`, `medium`, `large` |
| `PIPER_PORT` | `9501` | Piper service port |
| `PIPER_VOICE` | `en_US-lessac-medium` | Default Piper voice ([browse](https://rhasspy.github.io/piper-samples/)) |
| `IMAGEGEN_PORT` | `9502` | ImageGen service port (set in shell env) |

### Whisper Model Sizes

| Model | Size | Speed | Notes |
|-------|------|-------|-------|
| `tiny` | ~39MB | Fastest | Good for clear audio |
| `base` | ~142MB | Fast | **Recommended** |
| `small` | ~466MB | Moderate | Better accuracy |
| `medium` | ~1.5GB | Slow | Near-best accuracy |
| `large` | ~2.9GB | Slowest | Best accuracy |

## Project Structure

```
neo-services/
├── docker-compose.yml       # Whisper + Piper (Docker)
├── .env.example             # Configuration template
├── README.md
├── whisper/
│   ├── Dockerfile
│   ├── app.py               # FastAPI — POST /transcribe, GET /health
│   └── requirements.txt
├── piper/
│   ├── Dockerfile
│   ├── app.py               # FastAPI — POST /speak, GET /voices, /health
│   └── requirements.txt
├── imagegen/
│   ├── app.py               # FastAPI — POST /generate, GET /health, /models
│   ├── requirements.txt
│   └── run.sh               # Creates venv, installs deps, starts uvicorn
└── web/
    ├── index.html            # Content Generator SPA (single-file, no deps)
    └── serve.py              # Simple HTTP server on :9503
```

## Management

```bash
# Docker services (Whisper + Piper)
docker compose up -d              # Start
docker compose logs -f            # Logs
docker compose down               # Stop
docker compose up -d --build      # Rebuild

# ImageGen (native)
cd imagegen && ./run.sh           # Start (foreground)
# Or background it:
cd imagegen && nohup ./run.sh &   # Start (background)

# Web UI
python3 web/serve.py              # Start (foreground)
python3 web/serve.py &            # Start (background)

# Start everything at once
docker compose up -d --build && \
  (cd imagegen && nohup ./run.sh &) && \
  python3 web/serve.py &

# Reset model caches
docker volume rm neo-services_whisper-models neo-services_piper-models
rm -rf imagegen/.venv             # Reset imagegen venv + cached models
```
