# Content Generation Pipeline — Specification

## Overview

An automated content generation pipeline that continuously produces Instagram-ready images and captions for a futuristic tech/design account. It runs 24/7, generating one image approximately every 25-30 minutes using a local Flux Krea Dev model on Apple Silicon.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Pipeline Server                       │
│                    (localhost:9504)                       │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─────────────┐    ┌──────────────┐    ┌────────────┐ │
│  │   Briefer    │───▶│  Image Queue  │───▶│  Renderer  │ │
│  │  (Ollama)    │    │              │    │ (Flux API)  │ │
│  └─────────────┘    └──────────────┘    └────────────┘ │
│         │                                      │        │
│         ▼                                      ▼        │
│  ┌─────────────┐                      ┌────────────┐   │
│  │   Trends     │                      │ Compositor  │   │
│  │  Scraper     │                      │  (Pillow)   │   │
│  └─────────────┘                      └────────────┘   │
│                                                │        │
│                                                ▼        │
│                                       ┌────────────┐   │
│                                       │   Output    │   │
│                                       │   /output   │   │
│                                       └────────────┘   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │              Dashboard Web UI                    │   │
│  │  - Live Flux progress (step X/40, ETA)          │   │
│  │  - Queue view (upcoming images)                 │   │
│  │  - Completed posts gallery                      │   │
│  │  - Trending topics feed                         │   │
│  │  - Meme templates library                       │   │
│  │  - Edit prompts for queued items                │   │
│  └─────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Content Categories

### 1. Futuristic Concepts (primary)
- Product categories: cars, watches, yachts, sneakers, headphones, phones, gaming setups, motorcycles, drones
- Format: single image or 5-image carousel (front, interior, rear, detail, profile)
- Caption style: specs + "Would you [buy/drive/wear] this?"

### 2. Dream Spaces
- Luxury interiors, penthouses, underground lairs, home offices, cyberpunk apartments
- Format: single image or 3-image carousel
- Caption style: "Would you live here?"

### 3. "What If" Crossovers
- Brand X designs unexpected Product Y
- Format: single image
- Caption style: "What if [Brand] made a [Product]?"
- Examples: "What if Rolex made a motorcycle?", "What if Nike designed a spaceship?"

### 4. "Pick Your" Grids
- 4 concept variants in a 2x2 grid with labels (1, 2, 3, 4)
- Format: single composite image
- Caption style: "Pick your 2040 daily driver 👇"

### 5. "Then vs 2040" Side-by-Side
- Real product photo (left) vs AI futuristic version (right)
- Format: single composite image with divider
- Caption style: "[Product] today vs 2040"
- NOTE: For MVP, skip the real photo sourcing — just generate both versions with AI (one "current realistic" and one "futuristic")

### 6. "GF Knows" Grids
- Same as Pick Your but with relationship bait caption
- Format: 2x2 grid
- Caption style: "Real ones know which one he'd pick 😏"

### 7. Memes (semi-automated)
- Gather trending topics from Reddit/Twitter
- Match with popular meme templates
- Generate caption suggestions
- NOTE: For MVP, just gather topics and suggest concepts — actual meme image generation can be phase 2

## Technical Components

### 1. Pipeline Server (`pipeline/server.py`)
- FastAPI app on port 9504
- Manages the queue, orchestrates generation
- REST API for the dashboard
- WebSocket for live progress updates

### 2. Briefer (`pipeline/briefer.py`)
- Uses Ollama (llama3.2 or similar) to generate content briefs
- Input: category weights, trending topics (optional)
- Output: JSON brief with post_type, category, theme, image prompts, caption, hashtags
- Generates briefs in batches (10+ at a time)
- Each image prompt should be detailed and optimized for Flux Krea Dev

### 3. Image Queue (`pipeline/queue.py`)
- SQLite database for persistence (survives restarts)
- States: pending → rendering → compositing → complete → posted
- Tracks: prompt, category, post_id (groups carousel images), progress, timing
- Allows prompt editing for pending items

### 4. Renderer (`pipeline/renderer.py`)
- Calls the existing ImageGen API at localhost:9502
- Polls for progress (or reads from ImageGen stdout somehow)
- Saves output to /output/{date}/{post_id}/
- Default settings: 40 steps, guidance 4.5, 1024x1024

### 5. Compositor (`pipeline/compositor.py`)
- Pillow-based image compositing
- Grid layout: 2x2 with numbered labels, padding, dark background
- Side-by-side: two images with divider and "vs" text
- Carousel: just the individual images grouped by post_id

### 6. Trends Scraper (`pipeline/trends.py`)
- Scrape Google Trends, Reddit popular, Twitter/X trending
- No API keys needed for Google Trends (use pytrends or scraping)
- Reddit: use public JSON endpoints (no auth needed for popular)
- Run periodically (every few hours)
- Store results in SQLite

### 7. Dashboard UI (`pipeline/static/index.html`)
- Single-page app (vanilla JS, no framework)
- Dark theme (consistent with existing web UI)
- Sections:
  - **Status Bar**: Current rendering status, step progress, ETA
  - **Queue**: List of upcoming images with edit buttons for prompts
  - **Completed**: Gallery of finished posts with captions
  - **Trends**: Latest trending topics from various sources
  - **Meme Templates**: Configured templates (phase 2)
  - **Settings**: Category weights, generation parameters
- WebSocket connection for live progress updates

## API Endpoints

### Pipeline Server (port 9504)

```
GET  /api/status          — Current pipeline status (rendering, idle, queue length)
GET  /api/queue           — List all queued items
PUT  /api/queue/{id}      — Edit a queued item's prompt
DELETE /api/queue/{id}    — Remove a queued item
POST /api/generate-briefs — Trigger brief generation (optional: count, category)
GET  /api/completed       — List completed posts with images
GET  /api/trends          — Latest trending topics
GET  /api/progress        — Current render progress (step, total, eta)
POST /api/pause           — Pause the pipeline
POST /api/resume          — Resume the pipeline
WS   /ws/progress         — WebSocket for live progress updates
```

## ImageGen API Enhancement Needed

The current ImageGen API at :9502 returns the final image but doesn't expose progress. We need to add:
- A `/progress` endpoint that returns current step/total/eta
- OR a WebSocket endpoint for streaming progress
- This requires modifying `imagegen/app.py` to track generation state

## File Structure

```
neo-services/
├── pipeline/
│   ├── server.py          # FastAPI main app
│   ├── briefer.py         # Ollama content brief generator
│   ├── queue.py           # SQLite queue manager
│   ├── renderer.py        # ImageGen API client
│   ├── compositor.py      # Image compositing (grids, side-by-side)
│   ├── trends.py          # Trending topics scraper
│   ├── config.py          # Configuration (ports, defaults, category weights)
│   ├── requirements.txt   # Dependencies
│   ├── run.sh             # Start script
│   ├── static/
│   │   └── index.html     # Dashboard SPA
│   └── output/            # Generated content (gitignored)
│       └── 2026-03-21/
│           └── post_001/
│               ├── brief.json
│               ├── image_1.png
│               ├── image_2.png
│               ├── grid.png (if applicable)
│               └── caption.txt
├── imagegen/              # (existing, needs progress endpoint)
├── web/                   # (existing content gen UI)
├── whisper/               # (existing)
└── piper/                 # (existing)
```

## Configuration Defaults

```python
IMAGEGEN_URL = "http://localhost:9502"
OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = "llama3.2"
PIPELINE_PORT = 9504
DEFAULT_STEPS = 40
DEFAULT_GUIDANCE = 4.5
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024
QUEUE_REFILL_THRESHOLD = 5  # Generate more briefs when queue drops below this
BRIEFS_PER_BATCH = 10
```

## Category Weights (configurable via UI)

```python
CATEGORY_WEIGHTS = {
    "futuristic_concept": 0.30,
    "dream_space": 0.15,
    "what_if": 0.15,
    "pick_your": 0.15,
    "then_vs_2040": 0.10,
    "gf_knows": 0.10,
    "meme": 0.05,  # phase 2
}
```

## Important Notes

- Everything runs locally — no cloud APIs, no Opus tokens
- ImageGen is already running on :9502 with Flux Krea Dev 8-bit
- Ollama is already running with llama3.2
- The pipeline should be resilient to restarts (SQLite persistence)
- Images are ~24-30 min each at 40 steps on M4 24GB
- The pipeline server should NOT start ImageGen — it assumes it's already running
- Meme generation is phase 2 — for now, just collect trending topics
