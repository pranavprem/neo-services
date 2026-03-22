"""Renderer — processes queue items by calling ImageGen API and polling progress."""

import asyncio
import json
import logging
import os
from datetime import date

import httpx

from config import (
    IMAGEGEN_URL,
    OUTPUT_DIR,
    PROGRESS_POLL_INTERVAL,
    MAX_RETRIES,
    RETRY_BACKOFF,
    QUEUE_REFILL_THRESHOLD,
    BRIEFS_PER_BATCH,
)
from queue_manager import QueueManager
from compositor import Compositor
import briefer
import discord_notifier

logger = logging.getLogger(__name__)


class Renderer:
    def __init__(self, queue: QueueManager, broadcast_fn):
        self.queue = queue
        self.broadcast = broadcast_fn
        self.paused = False
        self._running = False
        self._current_item: dict | None = None

    @property
    def current_item(self) -> dict | None:
        return self._current_item

    async def start(self):
        """Main rendering loop — runs as a background task."""
        self._running = True
        # Recover any items stuck in 'rendering' from a previous crash/restart
        recovered = await asyncio.to_thread(self.queue.recover_stale_rendering)
        if recovered:
            logger.info("Recovered %d stale rendering items back to pending", recovered)
        logger.info("Renderer started")
        while self._running:
            if self.paused:
                await asyncio.sleep(5)
                continue

            # Auto-refill queue when low
            await self._maybe_refill_queue()

            item = await asyncio.to_thread(self.queue.get_next_pending)
            if not item:
                await self.broadcast({
                    "type": "status_change",
                    "status": "idle",
                    "queue_item_id": None,
                    "post_id": None,
                    "queue_length": 0,
                })
                await asyncio.sleep(30)
                continue

            await self._render_item(item)

    def stop(self):
        self._running = False

    async def _render_item(self, item: dict):
        """Render a single queue item via ImageGen API."""
        self._current_item = item
        item_id = item["id"]
        post_id = item["post_id"]

        # Update status to rendering
        await asyncio.to_thread(self.queue.update_status, item_id, "rendering")
        queue_len = await asyncio.to_thread(self.queue.pending_count)
        await self.broadcast({
            "type": "status_change",
            "status": "rendering",
            "queue_item_id": item_id,
            "post_id": post_id,
            "queue_length": queue_len,
        })

        # Prepare output directory
        today = date.today().isoformat()
        post_dir = os.path.join(OUTPUT_DIR, today, post_id)
        os.makedirs(post_dir, exist_ok=True)

        # Save brief JSON
        if item.get("brief_json"):
            brief_path = os.path.join(post_dir, "brief.json")
            if not os.path.exists(brief_path):
                with open(brief_path, "w") as f:
                    f.write(item["brief_json"])

        # Call ImageGen with concurrent progress polling
        gen_task = asyncio.create_task(self._call_imagegen(item))
        poll_task = asyncio.create_task(self._poll_progress(item))

        try:
            png_bytes, gen_time = await gen_task
        except Exception as e:
            logger.error("Render failed for item %d: %s", item_id, e)
            await asyncio.to_thread(
                self.queue.update_status, item_id, "failed", error=str(e)
            )
            await self.broadcast({
                "type": "error",
                "queue_item_id": item_id,
                "message": f"Render failed: {e}",
            })
            self._current_item = None
            return
        finally:
            poll_task.cancel()
            try:
                await poll_task
            except asyncio.CancelledError:
                pass

        # Save image
        image_path = os.path.join(post_dir, f"image_{item['image_index']}.png")
        with open(image_path, "wb") as f:
            f.write(png_bytes)

        # Update queue
        await asyncio.to_thread(
            self.queue.update_status, item_id, "complete", output_path=image_path
        )

        # Record stat
        await asyncio.to_thread(
            self.queue.add_stat, post_id, item["category"], gen_time, item["steps"]
        )

        await self.broadcast({
            "type": "image_complete",
            "queue_item_id": item_id,
            "post_id": post_id,
            "image_index": item["image_index"],
            "output_path": image_path,
            "gen_time": gen_time,
        })

        # Post individual image to Discord
        total_images = item.get("total_images", 1)
        hashtags = [t.strip().strip("#") for t in (item.get("hashtags") or "").split() if t.strip()]
        try:
            await discord_notifier.post_completed_image(
                image_path=image_path,
                category=item.get("category", "unknown"),
                theme=item.get("theme", "Untitled"),
                caption=item.get("caption", ""),
                hashtags=hashtags,
                post_id=post_id,
                image_index=item["image_index"],
                total_images=total_images,
            )
        except Exception as e:
            logger.error("Discord notification failed: %s", e)

        # Check if all images for this post are complete
        await self._check_post_complete(item, post_dir)
        self._current_item = None

    async def _call_imagegen(self, item: dict) -> tuple[bytes, float]:
        """POST to ImageGen and return (PNG bytes, generation time). Retries on failure."""
        payload = {
            "prompt": item["prompt"],
            "width": item["width"],
            "height": item["height"],
            "steps": item["steps"],
            "guidance": item["guidance"],
        }
        if item.get("seed"):
            payload["seed"] = item["seed"]

        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(3600.0)) as client:
                    import time
                    start = time.time()
                    response = await client.post(
                        f"{IMAGEGEN_URL}/generate/json", json=payload
                    )
                    response.raise_for_status()
                    return response.content, time.time() - start
            except (httpx.HTTPError, httpx.ConnectError) as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    wait = RETRY_BACKOFF[attempt]
                    logger.warning(
                        "ImageGen attempt %d failed, retrying in %ds: %s",
                        attempt + 1, wait, e,
                    )
                    await asyncio.sleep(wait)

        raise RuntimeError(f"ImageGen failed after {MAX_RETRIES} attempts: {last_err}")

    async def _poll_progress(self, item: dict):
        """Poll ImageGen /progress and broadcast updates via WebSocket."""
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
            while True:
                await asyncio.sleep(PROGRESS_POLL_INTERVAL)
                try:
                    resp = await client.get(f"{IMAGEGEN_URL}/progress")
                    data = resp.json()
                    if data.get("active"):
                        total = data.get("total_steps", 1)
                        step = data.get("step", 0)
                        await self.broadcast({
                            "type": "render_progress",
                            "queue_item_id": item["id"],
                            "post_id": item["post_id"],
                            "step": step,
                            "total_steps": total,
                            "percentage": round(step / max(total, 1) * 100, 1),
                            "elapsed_seconds": data.get("elapsed", 0),
                            "eta_seconds": data.get("eta_seconds"),
                            "prompt": item["prompt"][:120],
                        })
                except Exception:
                    pass  # ImageGen may be between requests

    async def _check_post_complete(self, item: dict, post_dir: str):
        """Check if all images for a post are done and run compositor if needed."""
        post_items = await asyncio.to_thread(
            self.queue.get_post_items, item["post_id"]
        )
        all_complete = all(i["status"] == "complete" for i in post_items)
        if not all_complete:
            return

        # Run compositor for grid/side_by_side post types
        post_type = item["post_type"]
        image_paths = sorted(
            [i["output_path"] for i in post_items if i["output_path"]],
            key=lambda p: p,  # sorted by image_N.png
        )

        composite_path = None
        if post_type == "grid" and len(image_paths) == 4:
            composite_path = os.path.join(post_dir, "grid.png")
            try:
                Compositor.create_grid(image_paths, composite_path)
            except Exception as e:
                logger.error("Grid compositing failed: %s", e)

        elif post_type == "side_by_side" and len(image_paths) == 2:
            composite_path = os.path.join(post_dir, "side_by_side.png")
            try:
                Compositor.create_side_by_side(
                    image_paths[0], image_paths[1], composite_path
                )
            except Exception as e:
                logger.error("Side-by-side compositing failed: %s", e)

        # Save caption
        caption_path = os.path.join(post_dir, "caption.txt")
        caption = item.get("caption", "")
        hashtags = item.get("hashtags", "")
        with open(caption_path, "w") as f:
            f.write(caption)
            if hashtags:
                f.write(f"\n\n{hashtags}")

        await self.broadcast({
            "type": "post_complete",
            "post_id": item["post_id"],
            "category": item["category"],
            "post_type": post_type,
            "output_path": post_dir,
            "caption": caption,
            "image_count": len(image_paths),
            "composite": composite_path,
        })

        # Post composite to Discord if applicable
        if composite_path and os.path.exists(composite_path):
            hashtags_list = [t.strip().strip("#") for t in (item.get("hashtags") or "").split() if t.strip()]
            try:
                await discord_notifier.post_composite(
                    image_path=composite_path,
                    category=item.get("category", "unknown"),
                    theme=item.get("theme", "Untitled"),
                    caption=caption,
                    hashtags=hashtags_list,
                    post_id=item["post_id"],
                    composite_type="grid" if post_type == "grid" else "side_by_side",
                )
            except Exception as e:
                logger.error("Discord composite notification failed: %s", e)
        await self.broadcast({
            "type": "queue_update",
            "action": "completed",
            "items": [{"id": i["id"], "post_id": i["post_id"]} for i in post_items],
        })

    async def _maybe_refill_queue(self):
        """Auto-generate briefs when queue runs low."""
        pending = await asyncio.to_thread(self.queue.pending_count)
        if pending >= QUEUE_REFILL_THRESHOLD:
            return

        logger.info("Queue low (%d pending), generating briefs...", pending)
        await self.broadcast({"type": "status_change", "status": "briefing",
                              "queue_item_id": None, "post_id": None,
                              "queue_length": pending})

        try:
            trends = await asyncio.to_thread(self.queue.get_trends)
            briefs = await briefer.generate_briefs(
                count=BRIEFS_PER_BATCH, trending=trends
            )
            added_ids = []
            for brief in briefs:
                ids = await asyncio.to_thread(self.queue.add_brief, brief)
                added_ids.extend(ids)

            if added_ids:
                await self.broadcast({
                    "type": "queue_update",
                    "action": "added",
                    "items": [{"id": i} for i in added_ids],
                })
                logger.info("Added %d briefs (%d queue items)", len(briefs), len(added_ids))

                # Post queue preview to Discord
                try:
                    pending_items = await asyncio.to_thread(self.queue.get_queue, "pending")
                    await discord_notifier.post_queue_preview(pending_items)
                except Exception as e:
                    logger.error("Discord queue preview failed: %s", e)
        except Exception as e:
            logger.error("Failed to auto-refill queue: %s", e)
