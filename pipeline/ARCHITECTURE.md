# Pipeline Architecture Document

## 1. Problem Statement

Build an automated content generation pipeline for Instagram that continuously produces images and captions using a local Flux Krea Dev model on Apple Silicon. The pipeline runs 24/7, orchestrating content briefing (via Ollama), image rendering (via existing ImageGen API), and image compositing (via Pillow), with a live dashboard for monitoring and queue management.

### Constraints

- All local — no cloud APIs
- ImageGen already running on :9502, must not be restarted or re-deployed
- Ollama already running on :11434 with llama3.2
- ~25-30 min per image at 40 steps on M4 24GB
- Pipeline must survive restarts (SQLite persistence)
- Single-page vanilla JS dashboard (no framework)

### Non-Goals

- Actual meme image generation (phase 2)
- Real photo sourcing for "Then vs 2040" (generate both sides with AI)
- Social media posting automation (out of scope)
- Multi-GPU or distributed rendering

---

## 2. ImageGen Progress Tracking (`imagegen/app.py`)

### The Problem

The `_generate_image` function calls `flux_model.generate_image()` which is synchronous and blocking. The pipeline dashboard needs to show live progress (step X/40, ETA, percentage).

### Solution: mflux Callback System

The mflux library has a built-in callback system. The `Flux1` model instance exposes `self.callbacks` (a `CallbackRegistry`) with a `.register(handler)` method that uses duck typing. Any object implementing `call_in_loop(self, t, seed, prompt, latents, config, time_steps)` will be invoked at each denoising step.

**Approach:**

1. Define a `ProgressTracker` class that implements `call_in_loop` and `call_before_loop`.
2. Register it with `flux_model.callbacks.register(tracker)` after model load.
3. The tracker updates a thread-safe global dict with current step, total steps, start time, and ETA.
4. Expose this dict via `GET /progress`.

### Progress State Dict

```python
import threading

# Thread-safe progress state
_progress_lock = threading.Lock()
_progress: dict = {
    "active": False,
    "step": 0,
    "total_steps": 0,
    "start_time": None,
    "elapsed": 0.0,
    "eta_seconds": None,
    "prompt": None,
}
```

### ProgressTracker Callback

```python
class ProgressTracker:
    """Registered with mflux to track generation progress per step."""

    def call_before_loop(self, seed, prompt, latents, config, **kwargs):
        with _progress_lock:
            _progress["active"] = True
            _progress["step"] = 0
            _progress["total_steps"] = len(config.time_steps)
            _progress["start_time"] = time.time()
            _progress["elapsed"] = 0.0
            _progress["eta_seconds"] = None
            _progress["prompt"] = prompt

    def call_in_loop(self, t, seed, prompt, latents, config, time_steps, **kwargs):
        now = time.time()
        # t is the current timestep value; step number = index in time_steps
        step_index = time_steps.index(t) + 1 if t in time_steps else _progress["step"] + 1
        with _progress_lock:
            _progress["step"] = step_index
            _progress["total_steps"] = len(time_steps)
            elapsed = now - (_progress["start_time"] or now)
            _progress["elapsed"] = elapsed
            if step_index > 0:
                per_step = elapsed / step_index
                remaining = (len(time_steps) - step_index) * per_step
                _progress["eta_seconds"] = remaining

    def call_after_loop(self, seed, prompt, latents, config, **kwargs):
        with _progress_lock:
            _progress["active"] = False
            _progress["step"] = _progress["total_steps"]
            _progress["eta_seconds"] = 0
```

**Note on `time_steps`:** The `call_in_loop` receives `time_steps` as a list. The parameter `t` is the current timestep value (not an index). We find its position in the list to determine the step number. We use `**kwargs` to future-proof against new parameters.

### Registration (in `load_model()` after model creation)

```python
tracker = ProgressTracker()
flux_model.callbacks.register(tracker)
```

### GET /progress Endpoint

```python
@app.get("/progress")
async def progress():
    with _progress_lock:
        return dict(_progress)
```

Returns:
```json
{
    "active": true,
    "step": 12,
    "total_steps": 40,
    "start_time": 1711036800.0,
    "elapsed": 432.5,
    "eta_seconds": 576.7,
    "prompt": "A futuristic carbon-fiber..."
}
```

### Why This Approach

| Alternative | Why Not |
|---|---|
| Parse stdout/logs | Fragile, depends on print format, no structured data |
| Monkey-patch generate_image | Breaks on library updates, fragile |
| Separate process monitoring | Over-engineered, hard to get step-level data |
| **mflux callback system** | **Official API, clean, future-proof, minimal code** |

---

## 3. SQLite Schema (`queue.py`)

### Tables

```sql
-- Content briefs and their rendering state
CREATE TABLE IF NOT EXISTS queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT NOT NULL,           -- Groups images in a carousel (e.g., "20260321_001")
    category    TEXT NOT NULL,           -- "futuristic_concept", "dream_space", etc.
    post_type   TEXT NOT NULL,           -- "single", "carousel", "grid", "side_by_side"
    theme       TEXT NOT NULL,           -- Brief description of the concept
    prompt      TEXT NOT NULL,           -- Flux image generation prompt
    caption     TEXT,                    -- Instagram caption
    hashtags    TEXT,                    -- Comma-separated hashtags
    image_index INTEGER DEFAULT 0,      -- Position within a carousel (0 for single images)
    status      TEXT DEFAULT 'pending',  -- pending | rendering | compositing | complete | failed
    priority    INTEGER DEFAULT 0,      -- Higher = rendered first
    error       TEXT,                    -- Error message if failed
    output_path TEXT,                    -- Path to generated image file
    seed        INTEGER,                -- Seed used for generation (for reproducibility)
    steps       INTEGER DEFAULT 40,
    guidance    REAL DEFAULT 4.5,
    width       INTEGER DEFAULT 1024,
    height      INTEGER DEFAULT 1024,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at  TIMESTAMP,
    completed_at TIMESTAMP,
    brief_json  TEXT                     -- Full original brief JSON from Ollama
);

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_post_id ON queue(post_id);
CREATE INDEX IF NOT EXISTS idx_queue_priority_created ON queue(priority DESC, created_at ASC);

-- Trending topics cache
CREATE TABLE IF NOT EXISTS trends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,           -- "reddit", "google", "twitter"
    topic       TEXT NOT NULL,
    description TEXT,
    score       REAL,                    -- Relevance/popularity score
    fetched_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_trends_source ON trends(source);

-- Generation statistics
CREATE TABLE IF NOT EXISTS stats (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id     TEXT NOT NULL,
    category    TEXT NOT NULL,
    gen_time    REAL,                    -- Seconds to generate
    steps       INTEGER,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Queue Manager Class

```python
class QueueManager:
    def __init__(self, db_path: str = "pipeline/pipeline.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self): ...
    def add_brief(self, brief: dict) -> list[int]: ...
    def get_next_pending(self) -> dict | None: ...     # ORDER BY priority DESC, created_at ASC
    def update_status(self, id: int, status: str, **kwargs): ...
    def get_queue(self, status: str = None) -> list[dict]: ...
    def update_prompt(self, id: int, prompt: str): ...  # Only if status == 'pending'
    def delete_item(self, id: int): ...                  # Only if status == 'pending'
    def get_completed(self, limit: int = 50) -> list[dict]: ...
    def get_post_items(self, post_id: str) -> list[dict]: ...
    def pending_count(self) -> int: ...
    def reorder(self, id: int, new_priority: int): ...
```

### Design Decisions

- **`post_id`** groups carousel images together. Format: `YYYYMMDD_NNN` (e.g., `20260321_001`).
- **One row per image**, not per post — a 5-image carousel produces 5 rows sharing the same `post_id`.
- **`priority`** allows reordering from the dashboard without complex position math.
- **`brief_json`** preserves the full Ollama output for debugging and re-generation.
- **SQLite with WAL mode** for concurrent read access from the dashboard while the pipeline writes.

---

## 4. Ollama Prompt Structure (`briefer.py`)

### System Prompt

```
You are a creative director for a futuristic tech/design Instagram account. You generate content briefs as structured JSON.

Your briefs must contain image prompts optimized for the Flux Krea Dev AI image generator. Write prompts that are:
- Highly detailed and visually specific (materials, lighting, camera angle, environment)
- Focused on photorealistic or cinematic rendering style
- 1-3 sentences, no more than 80 words per prompt
- Include specific details: "brushed titanium with blue LED accents" not just "futuristic metal"

Each brief must specify the exact post format and include an Instagram caption with engagement hook.

IMPORTANT: Output valid JSON only. No markdown, no commentary. Output a JSON array of brief objects.
```

### User Prompt Template

```
Generate {count} content briefs for Instagram. Use these category weights to guide variety:
- futuristic_concept (30%): Cars, watches, yachts, sneakers, headphones, phones, gaming setups, motorcycles, drones
- dream_space (15%): Luxury interiors, penthouses, underground lairs, cyberpunk apartments
- what_if (15%): "What if [Brand X] designed [Product Y]?" crossovers
- pick_your (15%): 4 variants of a concept for a 2x2 grid labeled 1-4
- then_vs_2040 (10%): Current-style product vs futuristic 2040 version (generate both with AI)
- gf_knows (10%): Same as pick_your but with relationship bait caption
- meme (5%): Just suggest a trending topic + concept (no image prompt needed)

{trending_context}

Output a JSON array where each object has:
{{
  "category": "futuristic_concept",
  "post_type": "single" | "carousel" | "grid" | "side_by_side",
  "theme": "Short concept description",
  "image_prompts": ["Detailed Flux prompt for image 1", ...],
  "caption": "Instagram caption with engagement hook and emojis",
  "hashtags": ["tag1", "tag2", ...]
}}

Rules:
- carousel: 3-5 image_prompts showing different angles/views of the same concept
- grid (pick_your, gf_knows): exactly 4 image_prompts, one per grid cell
- side_by_side (then_vs_2040): exactly 2 image_prompts — [current_realistic, futuristic_2040]
- single: exactly 1 image_prompt
- meme: empty image_prompts, add a "meme_concept" field instead
```

### `{trending_context}` Injection

When trends are available:
```
Currently trending topics (use 1-2 if they inspire good concepts):
- Reddit: {topics}
- Google Trends: {topics}
```

When no trends: omit the section entirely.

### Ollama API Call

```python
async def generate_briefs(count: int = 10, trending: list[str] | None = None) -> list[dict]:
    prompt = USER_PROMPT_TEMPLATE.format(
        count=count,
        trending_context=_format_trending(trending) if trending else ""
    )
    response = await httpx.AsyncClient().post(
        f"{OLLAMA_URL}/api/generate",
        json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
            "format": "json",  # Force JSON output
            "options": {
                "temperature": 0.9,   # High creativity
                "num_predict": 4096,  # Enough tokens for 10 briefs
            }
        },
        timeout=120.0,
    )
    # Parse and validate the JSON array
    briefs = json.loads(response.json()["response"])
    return _validate_briefs(briefs)
```

### Validation

`_validate_briefs` ensures:
- Each brief has required fields (category, post_type, theme, image_prompts, caption)
- `image_prompts` length matches `post_type` (1 for single, 2 for side_by_side, 4 for grid, 3-5 for carousel)
- Category is valid
- Drops malformed briefs rather than failing the batch

---

## 5. WebSocket Message Protocol

### Connection

```
ws://localhost:9504/ws/progress
```

The server maintains a set of connected WebSocket clients and broadcasts state changes.

### Message Types (Server -> Client)

All messages are JSON with a `type` field.

#### 1. `render_progress` — Sent every step (~30-45 seconds)

```json
{
    "type": "render_progress",
    "queue_item_id": 42,
    "post_id": "20260321_001",
    "step": 12,
    "total_steps": 40,
    "percentage": 30.0,
    "elapsed_seconds": 432.5,
    "eta_seconds": 576.7,
    "prompt": "A futuristic carbon-fiber hypercar..."
}
```

#### 2. `status_change` — When pipeline state changes

```json
{
    "type": "status_change",
    "status": "rendering",
    "queue_item_id": 42,
    "post_id": "20260321_001",
    "queue_length": 14
}
```

Valid statuses: `idle`, `rendering`, `compositing`, `briefing`, `paused`.

#### 3. `queue_update` — When queue changes (add, edit, delete, complete)

```json
{
    "type": "queue_update",
    "action": "added",
    "items": [{"id": 42, "prompt": "...", "category": "...", "status": "pending"}]
}
```

Actions: `added`, `updated`, `deleted`, `completed`.

#### 4. `image_complete` — When a single image finishes rendering

```json
{
    "type": "image_complete",
    "queue_item_id": 42,
    "post_id": "20260321_001",
    "image_index": 0,
    "output_path": "/output/2026-03-21/20260321_001/image_0.png",
    "gen_time": 1680.5
}
```

#### 5. `post_complete` — When all images for a post are done (including compositing)

```json
{
    "type": "post_complete",
    "post_id": "20260321_001",
    "category": "pick_your",
    "post_type": "grid",
    "output_path": "/output/2026-03-21/20260321_001/",
    "caption": "Pick your 2040 daily driver...",
    "image_count": 4
}
```

#### 6. `error` — When something goes wrong

```json
{
    "type": "error",
    "queue_item_id": 42,
    "message": "ImageGen API returned 500: Generation failed"
}
```

### Broadcast Implementation

```python
connected_clients: set[WebSocket] = set()

async def broadcast(message: dict):
    dead = set()
    for ws in connected_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    connected_clients -= dead
```

---

## 6. Rendering Pipeline Flow (`renderer.py`)

### Overview

`renderer.py` is the core loop that processes the queue. It runs as a background asyncio task started by the server.

### Flow

```
┌─────────────────────────────────────────────────────────────┐
│                    Rendering Loop                            │
│                                                             │
│  1. Check queue  ──────────────  No pending? Sleep 30s ───┐ │
│       │                                                    │ │
│       ▼ (has pending)                                      │ │
│  2. Pick next item (priority DESC, created_at ASC)         │ │
│       │                                                    │ │
│       ▼                                                    │ │
│  3. Set status = "rendering"                               │ │
│       │                                                    │ │
│       ▼                                                    │ │
│  4. POST to ImageGen /generate/json                        │ │
│     ┌─── (runs ~25 min) ───┐                               │ │
│     │ Poll GET /progress    │ every 5 seconds               │ │
│     │ Broadcast via WS      │                               │ │
│     └───────────────────────┘                               │ │
│       │                                                    │ │
│       ▼ (image bytes returned)                             │ │
│  5. Save image to /output/{date}/{post_id}/image_{N}.png   │ │
│       │                                                    │ │
│       ▼                                                    │ │
│  6. Set status = "complete", update output_path            │ │
│       │                                                    │ │
│       ▼                                                    │ │
│  7. Check: all images for this post_id complete?           │ │
│       │ No ──── Loop back to 1                             │ │
│       │ Yes ──▼                                            │ │
│  8. Run compositor (if grid/side_by_side)                  │ │
│       │                                                    │ │
│       ▼                                                    │ │
│  9. Broadcast post_complete, save caption.txt              │ │
│       │                                                    │ │
│       └──── Loop back to 1 ◄───────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### Progress Polling Detail

The ImageGen `/generate/json` call is blocking — it won't return until the image is done. So `renderer.py` must poll progress concurrently:

```python
async def render_item(item: dict, queue: QueueManager, broadcast_fn):
    """Render a single queue item."""
    # Fire off the generation request (this will block for ~25 min)
    gen_task = asyncio.create_task(_call_imagegen(item))

    # Poll progress while generation is running
    poll_task = asyncio.create_task(_poll_progress(item, broadcast_fn))

    try:
        png_bytes = await gen_task
    finally:
        poll_task.cancel()

    # Save image, update queue, broadcast completion
    ...
```

```python
async def _call_imagegen(item: dict) -> bytes:
    """POST to ImageGen and return PNG bytes."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0)) as client:
        response = await client.post(
            f"{IMAGEGEN_URL}/generate/json",
            json={
                "prompt": item["prompt"],
                "width": item["width"],
                "height": item["height"],
                "steps": item["steps"],
                "guidance": item["guidance"],
                "seed": item.get("seed"),
            },
        )
        response.raise_for_status()
        return response.content
```

```python
async def _poll_progress(item: dict, broadcast_fn):
    """Poll ImageGen /progress every 5 seconds and broadcast updates."""
    async with httpx.AsyncClient() as client:
        while True:
            await asyncio.sleep(5)
            try:
                resp = await client.get(f"{IMAGEGEN_URL}/progress")
                data = resp.json()
                if data.get("active"):
                    await broadcast_fn({
                        "type": "render_progress",
                        "queue_item_id": item["id"],
                        "post_id": item["post_id"],
                        "step": data["step"],
                        "total_steps": data["total_steps"],
                        "percentage": round(data["step"] / data["total_steps"] * 100, 1),
                        "elapsed_seconds": data["elapsed"],
                        "eta_seconds": data["eta_seconds"],
                        "prompt": item["prompt"][:100],
                    })
            except Exception:
                pass  # ImageGen may be between requests
```

### Queue Refill

The renderer also monitors queue depth:

```python
async def _maybe_refill_queue(queue: QueueManager, briefer):
    if queue.pending_count() < QUEUE_REFILL_THRESHOLD:
        briefs = await briefer.generate_briefs(count=BRIEFS_PER_BATCH)
        for brief in briefs:
            queue.add_brief(brief)
```

### Error Handling

- If ImageGen returns non-200: set item status to `failed` with error message, move to next item.
- If ImageGen is unreachable: retry 3 times with exponential backoff (10s, 30s, 90s), then mark failed.
- If compositing fails: individual images are still saved. Composite is skipped, error logged.
- All errors broadcast as `error` WebSocket messages.

---

## 7. Compositor Specs (`compositor.py`)

### Grid Layout (pick_your, gf_knows)

```
┌───────────────────────────────────┐
│  padding (40px)                   │
│  ┌──────────┐ gap ┌──────────┐   │
│  │          │(20px)│          │   │
│  │  Image 1 │     │  Image 2 │   │
│  │  "1"     │     │  "2"     │   │
│  └──────────┘     └──────────┘   │
│  gap (20px)                       │
│  ┌──────────┐     ┌──────────┐   │
│  │          │     │          │   │
│  │  Image 3 │     │  Image 4 │   │
│  │  "3"     │     │  "4"     │   │
│  └──────────┘     └──────────┘   │
│  padding (40px)                   │
└───────────────────────────────────┘
```

**Specifications:**
- Each cell: 512x512 (images resized/cropped from 1024x1024)
- Grid padding: 40px on all sides
- Gap between cells: 20px
- Total canvas: (512*2 + 20 + 40*2) x (512*2 + 20 + 40*2) = **1124 x 1124**
- Background: `#0f0f1a` (matches dashboard dark theme)
- Number labels: white circle (48px diameter) with black number, positioned bottom-right of each cell, 12px inset
- Font: Pillow's default font at 28px (or system Helvetica/Arial if available)
- Labels have a subtle drop shadow for visibility

### Side-by-Side Layout (then_vs_2040)

```
┌───────────────────────────────────────────┐
│  padding (30px)                           │
│  ┌──────────┐ divider ┌──────────┐       │
│  │          │  (4px)  │          │       │
│  │  "Today" │ + "VS"  │  "2040"  │       │
│  │          │         │          │       │
│  └──────────┘         └──────────┘       │
│  padding (30px)                           │
└───────────────────────────────────────────┘
```

**Specifications:**
- Each image: 512x1024 (cropped to portrait aspect if needed) or 512x512 for square
- Divider: 4px wide, color `#00d4ff` (accent color)
- "VS" text: centered on divider, white with accent glow, 24px bold
- Labels "Today" and "2040": bottom of each image, semi-transparent black bar (rgba 0,0,0,0.6), white text 20px
- Total canvas for square: (512*2 + 4 + 30*2) = **1088 x (512 + 30*2) = 1088 x 572**
- Background: `#0f0f1a`

### Carousel (futuristic_concept, dream_space)

No compositing needed — individual images are kept as-is. Just group by `post_id`.

### API

```python
class Compositor:
    @staticmethod
    def create_grid(image_paths: list[str], output_path: str, labels: list[str] = None) -> str: ...

    @staticmethod
    def create_side_by_side(left_path: str, right_path: str, output_path: str,
                           left_label: str = "Today", right_label: str = "2040") -> str: ...
```

---

## 8. API Endpoint Details

### Pipeline Server (port 9504)

#### `GET /api/status`

Returns current pipeline state.

```json
{
    "status": "rendering",
    "current_item": {
        "id": 42,
        "post_id": "20260321_001",
        "category": "futuristic_concept",
        "prompt": "A futuristic carbon-fiber hypercar...",
        "step": 12,
        "total_steps": 40,
        "percentage": 30.0,
        "eta_seconds": 576.7
    },
    "queue_length": 14,
    "completed_today": 3,
    "paused": false
}
```

#### `GET /api/queue?status=pending`

Returns queue items. Optional `status` filter.

```json
{
    "items": [
        {
            "id": 43,
            "post_id": "20260321_002",
            "category": "dream_space",
            "post_type": "single",
            "theme": "Cyberpunk penthouse with Tokyo skyline",
            "prompt": "A sprawling cyberpunk penthouse...",
            "caption": "Would you live here? ...",
            "status": "pending",
            "priority": 0,
            "image_index": 0,
            "created_at": "2026-03-21T10:30:00"
        }
    ],
    "total": 14
}
```

#### `PUT /api/queue/{id}`

Edit a pending queue item. Only `pending` items can be edited.

Request:
```json
{
    "prompt": "Updated Flux prompt...",
    "priority": 5,
    "caption": "Updated caption..."
}
```

Response: `200` with updated item, or `409` if item is no longer pending.

#### `DELETE /api/queue/{id}`

Remove a pending queue item. Returns `409` if item is already rendering.

#### `POST /api/generate-briefs`

Trigger Ollama brief generation.

Request:
```json
{
    "count": 10,
    "category": "futuristic_concept"
}
```

Both fields optional. Returns the generated briefs and count added to queue.

#### `GET /api/completed?limit=50&offset=0`

Returns completed posts grouped by `post_id`.

```json
{
    "posts": [
        {
            "post_id": "20260321_001",
            "category": "pick_your",
            "post_type": "grid",
            "theme": "Pick your 2040 supercar",
            "caption": "Pick your 2040 daily driver...",
            "hashtags": ["#futuristic", "#concept"],
            "images": [
                {"path": "/output/2026-03-21/20260321_001/image_0.png", "index": 0},
                {"path": "/output/2026-03-21/20260321_001/image_1.png", "index": 1},
                {"path": "/output/2026-03-21/20260321_001/image_2.png", "index": 2},
                {"path": "/output/2026-03-21/20260321_001/image_3.png", "index": 3}
            ],
            "composite": "/output/2026-03-21/20260321_001/grid.png",
            "completed_at": "2026-03-21T12:45:00"
        }
    ],
    "total": 3
}
```

#### `GET /api/trends`

Returns cached trending topics.

```json
{
    "trends": [
        {"source": "reddit", "topic": "Apple Vision Pro 2", "score": 0.95, "fetched_at": "..."},
        {"source": "google", "topic": "AI wearables", "score": 0.88, "fetched_at": "..."}
    ],
    "last_updated": "2026-03-21T08:00:00"
}
```

#### `GET /api/progress`

Proxied from ImageGen `/progress`. Returns current render progress. Identical to the ImageGen response but enriched with queue context.

#### `POST /api/pause` / `POST /api/resume`

Toggle pipeline pause state. When paused, the renderer loop sleeps instead of picking up new items. Currently rendering items finish normally.

#### `WS /ws/progress`

WebSocket endpoint. See Section 5 for message protocol.

### Static Files

`GET /` serves `pipeline/static/index.html`.
`GET /output/{path}` serves generated images from the output directory.

---

## 9. Dashboard UI Sections

### Layout

```
┌────────────────────────────────────────────────────┐
│  Pipeline Dashboard                    [Pause] btn │
├────────────────────────────────────────────────────┤
│                                                    │
│  ┌──── Status Bar ────────────────────────────┐   │
│  │ ● Rendering: "Futuristic hypercar..."       │   │
│  │ Step 12/40 ██████░░░░░░░░░░░░ 30% ETA 9m   │   │
│  │ Queue: 14 pending · 3 completed today       │   │
│  └────────────────────────────────────────────┘   │
│                                                    │
│  ┌──── Queue ─────────────────────────────────┐   │
│  │ [Generate Briefs] btn                       │   │
│  │                                              │   │
│  │ #43 · dream_space · "Cyberpunk penthouse"   │   │
│  │     Prompt: A sprawling cyberpunk pent...    │   │
│  │     [Edit] [Delete] [▲ Priority]            │   │
│  │                                              │   │
│  │ #44 · what_if · "Nike spaceship"            │   │
│  │     Prompt: What if Nike designed a sp...    │   │
│  │     [Edit] [Delete] [▲ Priority]            │   │
│  └────────────────────────────────────────────┘   │
│                                                    │
│  ┌──── Completed Posts ───────────────────────┐   │
│  │ ┌─────────┐ ┌─────────┐ ┌─────────┐      │   │
│  │ │  thumb  │ │  thumb  │ │  thumb  │       │   │
│  │ │ grid.png│ │ img.png │ │ img.png │       │   │
│  │ └─────────┘ └─────────┘ └─────────┘       │   │
│  │ "Pick your"  "Dream..."   "What if..."     │   │
│  │ Click to expand: full images + caption     │   │
│  └────────────────────────────────────────────┘   │
│                                                    │
│  ┌──── Trending Topics ──────────────────────┐   │
│  │ Reddit: Apple Vision Pro 2, AI wearables   │   │
│  │ Google: quantum computing, Mars colony     │   │
│  │ Last updated: 2h ago  [Refresh]            │   │
│  └────────────────────────────────────────────┘   │
│                                                    │
│  ┌──── Settings ─────────────────────────────┐   │
│  │ Category Weights:                          │   │
│  │ futuristic_concept: [====30%====] slider   │   │
│  │ dream_space:        [==15%==] slider       │   │
│  │ ...                                        │   │
│  │ Steps: [40]  Guidance: [4.5]               │   │
│  │ [Save Settings]                            │   │
│  └────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────┘
```

### WebSocket Integration

```javascript
const ws = new WebSocket(`ws://${location.host}/ws/progress`);

ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    switch (msg.type) {
        case 'render_progress':
            updateProgressBar(msg);    // Update step/total, percentage, ETA
            break;
        case 'status_change':
            updateStatusBar(msg);      // Update status indicator
            updateQueueBadge(msg);     // Update queue count
            break;
        case 'queue_update':
            refreshQueue();            // Re-fetch queue list
            break;
        case 'image_complete':
        case 'post_complete':
            refreshCompleted();        // Re-fetch completed gallery
            break;
        case 'error':
            showError(msg.message);    // Toast notification
            break;
    }
};

// Auto-reconnect on disconnect
ws.onclose = () => setTimeout(connectWS, 3000);
```

### Progress Bar

The progress bar updates every ~5 seconds (matching the poll interval). It shows:
- Step count: "Step 12/40"
- Visual bar: filled proportionally
- Percentage: "30%"
- ETA: calculated from `eta_seconds`, displayed as "ETA 9m 37s"
- Elapsed time: "Elapsed 7m 12s"
- Truncated prompt text below the bar

### Styling

Reuse the CSS variables from the existing web UI (`web/index.html`):
- `--bg-primary: #0f0f1a`
- `--bg-secondary: #1a1a2e`
- `--accent: #00d4ff`
- Same font stack, border-radius, and component patterns

---

## 10. File Structure and Dependencies

### `pipeline/requirements.txt`

```
fastapi
uvicorn[standard]
httpx
websockets
Pillow
aiosqlite
```

Note: `aiosqlite` is not strictly required — we can use synchronous `sqlite3` in a thread pool since writes are infrequent. Using standard `sqlite3` reduces dependencies. Decision: **use `sqlite3`** with `asyncio.to_thread()` for the rare write operations.

Revised:

```
fastapi
uvicorn[standard]
httpx
websockets
Pillow
```

### `pipeline/run.sh`

```bash
#!/bin/bash
cd "$(dirname "$0")"
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 9504 --reload
```

### Output Directory Convention

```
pipeline/output/
  2026-03-21/
    20260321_001/
      brief.json          # Original Ollama brief
      image_0.png         # Individual generated images
      image_1.png
      image_2.png
      image_3.png
      grid.png            # Composite (if applicable)
      caption.txt         # Instagram caption + hashtags
    20260321_002/
      brief.json
      image_0.png
      caption.txt
```

---

## 11. Security Considerations

- **Input validation**: All API inputs validated via Pydantic models. Prompt text sanitized (max length 2000 chars).
- **SQLite injection**: All queries use parameterized statements (never string interpolation).
- **File path traversal**: Output paths constructed server-side. The `/output/` static file handler restricts to the output directory only.
- **CORS**: Allow `*` for local development (matches existing ImageGen pattern).
- **No secrets**: No API keys, tokens, or credentials anywhere. All services are local.
- **Resource limits**: Queue has a max size (1000 items) to prevent unbounded growth. Brief generation capped at 20 per request.

---

## 12. Technology Choices Summary

| Component | Choice | Why | Alternatives Considered |
|---|---|---|---|
| Pipeline server | FastAPI | Already used by ImageGen, async support, WebSocket built-in | Flask (no async), aiohttp (less ergonomic) |
| Queue storage | SQLite | Zero config, survives restarts, single-file, WAL for concurrency | PostgreSQL (overkill), Redis (no persistence without config) |
| Image compositing | Pillow | Standard, lightweight, sufficient for grids and overlays | ImageMagick (external dependency), cairo (complex) |
| HTTP client | httpx | Async support, similar API to requests | aiohttp (less ergonomic), requests (no async) |
| LLM | Ollama + llama3.2 | Already running locally, JSON mode support | Direct llama.cpp (more setup) |
| Progress tracking | mflux callbacks | Official API, clean, per-step granularity | Log parsing (fragile), monkey-patching (breaks on updates) |
| Dashboard | Vanilla JS | No build step, consistent with existing web UI | React (overkill for one page), htmx (extra dependency) |
