"""Pipeline server — FastAPI app orchestrating queue, renderer, briefer, and dashboard."""

import asyncio
import logging
import os

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field

from config import PIPELINE_PORT, OUTPUT_DIR, TRENDS_REFRESH_INTERVAL
from queue_manager import QueueManager
from renderer import Renderer
from trends import trends_loop, refresh_trends
import briefer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Globals
queue_manager = QueueManager()
connected_clients: set[WebSocket] = set()
renderer: Renderer | None = None


async def broadcast(message: dict):
    """Send a message to all connected WebSocket clients."""
    global connected_clients
    dead = set()
    for ws in list(connected_clients):
        try:
            await ws.send_json(message)
        except Exception:
            dead.add(ws)
    connected_clients -= dead


# --- App setup ---

app = FastAPI(title="Content Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    global renderer
    renderer = Renderer(queue_manager, broadcast)
    asyncio.create_task(renderer.start())
    asyncio.create_task(trends_loop(queue_manager, TRENDS_REFRESH_INTERVAL))
    # Initial trends fetch
    asyncio.create_task(refresh_trends(queue_manager))
    logger.info("Pipeline server started on port %d", PIPELINE_PORT)


# --- WebSocket ---

@app.websocket("/ws/progress")
async def ws_progress(ws: WebSocket):
    await ws.accept()
    connected_clients.add(ws)
    try:
        while True:
            await ws.receive_text()  # keep connection alive
    except WebSocketDisconnect:
        pass
    finally:
        connected_clients.discard(ws)


# --- API Models ---

class QueueUpdateRequest(BaseModel):
    prompt: str | None = None
    caption: str | None = None
    hashtags: str | None = None
    priority: int | None = None


class GenerateBriefsRequest(BaseModel):
    count: int = Field(default=10, ge=1, le=20)
    category: str | None = None


# --- API Endpoints ---

@app.get("/api/status")
async def api_status():
    current = renderer.current_item if renderer else None
    return {
        "status": "paused" if (renderer and renderer.paused) else (
            "rendering" if current else "idle"
        ),
        "current_item": {
            "id": current["id"],
            "post_id": current["post_id"],
            "category": current["category"],
            "prompt": current["prompt"][:120],
        } if current else None,
        "queue_length": await asyncio.to_thread(queue_manager.pending_count),
        "completed_today": await asyncio.to_thread(queue_manager.completed_today_count),
        "paused": renderer.paused if renderer else False,
    }


@app.get("/api/queue")
async def api_queue(status: str | None = Query(None)):
    items = await asyncio.to_thread(queue_manager.get_queue, status)
    return {"items": items, "total": len(items)}


@app.put("/api/queue/{item_id}")
async def api_update_queue_item(item_id: int, req: QueueUpdateRequest):
    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    if not updates:
        return JSONResponse(status_code=400, content={"error": "No fields to update"})

    ok = await asyncio.to_thread(queue_manager.update_item, item_id, updates)
    if not ok:
        return JSONResponse(status_code=409, content={"error": "Item not found or not in pending status"})

    await broadcast({
        "type": "queue_update",
        "action": "updated",
        "items": [{"id": item_id, **updates}],
    })
    return {"ok": True, "id": item_id}


@app.delete("/api/queue/{item_id}")
async def api_delete_queue_item(item_id: int):
    ok = await asyncio.to_thread(queue_manager.delete_item, item_id)
    if not ok:
        return JSONResponse(status_code=409, content={"error": "Item not found or not in pending status"})

    await broadcast({
        "type": "queue_update",
        "action": "deleted",
        "items": [{"id": item_id}],
    })
    return {"ok": True}


@app.post("/api/generate-briefs")
async def api_generate_briefs(req: GenerateBriefsRequest):
    trends = await asyncio.to_thread(queue_manager.get_trends)
    briefs = await briefer.generate_briefs(
        count=req.count, category=req.category, trending=trends
    )

    added_ids = []
    for brief in briefs:
        ids = await asyncio.to_thread(queue_manager.add_brief, brief)
        added_ids.extend(ids)

    await broadcast({
        "type": "queue_update",
        "action": "added",
        "items": [{"id": i} for i in added_ids],
    })

    return {
        "briefs_generated": len(briefs),
        "queue_items_added": len(added_ids),
        "ids": added_ids,
    }


@app.get("/api/completed")
async def api_completed(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    posts = await asyncio.to_thread(queue_manager.get_completed, limit, offset)
    return {"posts": posts, "total": len(posts)}


@app.get("/api/trends")
async def api_trends():
    trends = await asyncio.to_thread(queue_manager.get_trends)
    last_updated = await asyncio.to_thread(queue_manager.get_trends_last_updated)
    return {"trends": trends, "last_updated": last_updated}


@app.post("/api/trends/refresh")
async def api_refresh_trends():
    await refresh_trends(queue_manager)
    return {"ok": True}


@app.get("/api/progress")
async def api_progress():
    """Proxy progress from ImageGen API, enriched with queue context."""
    from config import IMAGEGEN_URL
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{IMAGEGEN_URL}/progress")
            data = resp.json()
    except Exception:
        data = {"active": False, "step": 0, "total_steps": 0}

    current = renderer.current_item if renderer else None
    if current:
        data["queue_item_id"] = current["id"]
        data["post_id"] = current["post_id"]

    return data


@app.post("/api/pause")
async def api_pause():
    if renderer:
        renderer.paused = True
        await broadcast({"type": "status_change", "status": "paused",
                         "queue_item_id": None, "post_id": None, "queue_length": 0})
    return {"paused": True}


@app.post("/api/resume")
async def api_resume():
    if renderer:
        renderer.paused = False
        await broadcast({"type": "status_change", "status": "idle",
                         "queue_item_id": None, "post_id": None, "queue_length": 0})
    return {"paused": False}


# --- Static files ---

# Serve output images (ensure dir exists to avoid startup crash)
os.makedirs(OUTPUT_DIR, exist_ok=True)
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")


@app.get("/")
async def dashboard():
    return FileResponse("static/index.html")
