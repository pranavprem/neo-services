# Voice Services

Local speech-to-text and text-to-speech as Docker services, optimized for Apple Silicon (M4 Mac mini, CPU-only).

- **Whisper STT** — transcribe audio files to text
- **Piper TTS** — convert text to natural-sounding speech

## Quick Start

```bash
# Clone and start
cd ~/git/voice-services
docker compose up -d --build

# First startup downloads models — Whisper ~140MB (base), Piper voice ~60MB
# Subsequent starts are instant thanks to volume caching
```

## API Endpoints

### Whisper STT (port 9500)

**POST /transcribe** — Upload an audio file, get text back.

```bash
# Transcribe an audio file
curl -X POST http://localhost:9500/transcribe \
  -F "file=@recording.ogg"

# Response:
# {
#   "text": "Hello, how are you?",
#   "language": "en",
#   "segments": [{"start": 0.0, "end": 1.5, "text": "Hello, how are you?"}]
# }
```

**GET /health** — Check service status.

```bash
curl http://localhost:9500/health
# {"status": "ok", "model": "base"}
```

### Piper TTS (port 9501)

**POST /speak** — Send text, receive a WAV audio file.

```bash
# Generate speech (saves to output.wav)
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

**GET /voices** — List locally available voices.

```bash
curl http://localhost:9501/voices
# {"voices": [{"name": "en_US-lessac-medium", ...}], "default": "en_US-lessac-medium"}
```

**GET /health** — Check service status.

```bash
curl http://localhost:9501/health
# {"status": "ok", "default_voice": "en_US-lessac-medium"}
```

## Configuration

Copy `.env.example` to `.env` and customize:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `WHISPER_PORT` | `9500` | Whisper service port |
| `WHISPER_MODEL` | `base` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` |
| `PIPER_PORT` | `9501` | Piper service port |
| `PIPER_VOICE` | `en_US-lessac-medium` | Default Piper voice ([browse voices](https://rhasspy.github.io/piper-samples/)) |

### Whisper Model Sizes

| Model | Size | Relative Speed | Notes |
|-------|------|-----------------|-------|
| `tiny` | ~39MB | Fastest | Good enough for clear audio |
| `base` | ~142MB | Fast | **Recommended** — good balance |
| `small` | ~466MB | Moderate | Better accuracy |
| `medium` | ~1.5GB | Slow | Near-best accuracy |
| `large` | ~2.9GB | Slowest | Best accuracy, heavy on CPU |

## Architecture

```
voice-services/
├── docker-compose.yml      # Orchestrates both services
├── .env.example             # Configuration template
├── README.md
├── whisper/
│   ├── Dockerfile           # Python 3.11 + ffmpeg + openai-whisper
│   ├── app.py               # FastAPI app
│   └── requirements.txt
└── piper/
    ├── Dockerfile           # Python 3.11 + piper binary (aarch64)
    ├── app.py               # FastAPI app
    └── requirements.txt
```

Models are stored in Docker volumes (`whisper-models`, `piper-models`) and persist across container restarts.

## Management

```bash
# Start services
docker compose up -d

# View logs
docker compose logs -f

# Stop services
docker compose down

# Rebuild after changes
docker compose up -d --build

# Reset model cache
docker volume rm voice-services_whisper-models voice-services_piper-models
```
