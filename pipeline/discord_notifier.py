"""Discord webhook notifier — posts completed content and queue previews."""

import io
import json
import logging
import os
from pathlib import Path

import httpx

from config import DISCORD_WEBHOOK_URL

logger = logging.getLogger(__name__)


async def post_completed_image(
    image_path: str,
    category: str,
    theme: str,
    caption: str,
    hashtags: list[str],
    post_id: str,
    image_index: int = 1,
    total_images: int = 1,
):
    """Post a completed image to Discord with caption and metadata."""
    if not DISCORD_WEBHOOK_URL:
        logger.warning("No Discord webhook URL configured, skipping notification")
        return

    category_emoji = {
        "futuristic_concept": "🚀",
        "dream_space": "🏠",
        "what_if": "🤔",
        "pick_your": "🎯",
        "then_vs_2040": "🕰️",
        "gf_knows": "💕",
        "meme": "😂",
    }

    emoji = category_emoji.get(category, "🎨")
    hashtag_str = " ".join(f"#{t}" for t in hashtags[:10]) if hashtags else ""

    # Build embed
    embed = {
        "title": f"{emoji} {theme}",
        "description": f"**Caption:**\n{caption}\n\n{hashtag_str}",
        "color": 0x00d4ff,
        "footer": {
            "text": f"📁 {post_id} • Image {image_index}/{total_images} • {category.replace('_', ' ').title()}"
        },
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Read image file
            image_data = Path(image_path).read_bytes()
            filename = os.path.basename(image_path)

            # Send with file attachment
            resp = await client.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps({"embeds": [embed]})},
                files={"file": (filename, image_data, "image/png")},
            )
            if resp.status_code in (200, 204):
                logger.info(f"Posted {filename} to Discord")
            else:
                logger.error(f"Discord webhook failed: {resp.status_code} {resp.text}")
    except Exception as e:
        logger.error(f"Failed to post to Discord: {e}")


async def post_composite(
    image_path: str,
    category: str,
    theme: str,
    caption: str,
    hashtags: list[str],
    post_id: str,
    composite_type: str = "grid",
):
    """Post a composite image (grid or side-by-side) to Discord."""
    if not DISCORD_WEBHOOK_URL:
        return

    type_label = "2×2 Grid" if composite_type == "grid" else "Side by Side"
    category_emoji = {
        "pick_your": "🎯",
        "gf_knows": "💕",
        "then_vs_2040": "🕰️",
    }
    emoji = category_emoji.get(category, "🎨")

    embed = {
        "title": f"{emoji} {theme} — {type_label}",
        "description": f"**Caption:**\n{caption}\n\n{' '.join(f'#{t}' for t in hashtags[:10])}",
        "color": 0xff6b35,
        "footer": {"text": f"📁 {post_id} • Composite • {category.replace('_', ' ').title()}"},
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            image_data = Path(image_path).read_bytes()
            filename = os.path.basename(image_path)
            resp = await client.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps({"embeds": [embed]})},
                files={"file": (filename, image_data, "image/png")},
            )
            if resp.status_code not in (200, 204):
                logger.error(f"Discord webhook failed: {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to post composite to Discord: {e}")


async def post_queue_preview(queue_items: list[dict]):
    """Post a preview of the next items in the queue."""
    if not DISCORD_WEBHOOK_URL or not queue_items:
        return

    lines = ["**📋 Up Next in the Pipeline:**\n"]
    for i, item in enumerate(queue_items[:8], 1):
        cat = item.get("category", "unknown").replace("_", " ").title()
        theme = item.get("theme", "Untitled")[:60]
        post_type = item.get("post_type", "single")
        lines.append(f"`{i}.` **{theme}** ({cat}, {post_type})")

    remaining = len(queue_items) - 8
    if remaining > 0:
        lines.append(f"\n*...and {remaining} more in queue*")

    embed = {
        "description": "\n".join(lines),
        "color": 0x7b61ff,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                DISCORD_WEBHOOK_URL,
                json={"embeds": [embed]},
            )
            if resp.status_code not in (200, 204):
                logger.error(f"Queue preview webhook failed: {resp.status_code}")
    except Exception as e:
        logger.error(f"Failed to post queue preview: {e}")
