# Neo Services

Local AI services optimized for Apple Silicon (M4 Mac mini): speech-to-text, text-to-speech, and image generation.

- **Whisper STT** — transcribe audio files to text (Docker)
- **Piper TTS** — convert text to natural-sounding speech (Docker)
- **ImageGen** — generate images from text prompts via Flux Schnell (native, Metal GPU)

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
                        └──────────────────────────────┘
```

**Why the split?** Whisper and Piper run fine in Docker containers. ImageGen uses [mflux](https://github.com/filipstrand/mflux) (MLX-based Flux) which requires direct Metal/GPU access — Docker on macOS doesn't expose Metal, so it runs natively.

## Quick Start

```bash
# Start Whisper + Piper (Docker)
cd ~/git/neo-services
docker compose up -d --build

# Start ImageGen (native — needs Apple Silicon)
cd imagegen && ./run.sh

# First startup downloads models:
#   Whisper ~140MB (base), Piper voice ~60MB, Flux Schnell ~3.5GB (4-bit)
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

**POST /generate** — Generate an image from a text prompt. Returns PNG binary.

```bash
# Generate an image (saves to output.png)
curl -X POST http://localhost:9502/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "a cyberpunk cat in neon tokyo"}' \
  -o output.png

# With custom parameters
curl -X POST http://localhost:9502/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "mountain landscape at sunset", "width": 1024, "height": 768, "steps": 4, "seed": 42}' \
  -o landscape.png
```

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
└── imagegen/
    ├── app.py               # FastAPI — POST /generate, GET /health, /models
    ├── requirements.txt
    └── run.sh               # Creates venv, installs deps, starts uvicorn
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

# Reset model caches
docker volume rm neo-services_whisper-models neo-services_piper-models
rm -rf imagegen/.venv             # Reset imagegen venv + cached models
```
