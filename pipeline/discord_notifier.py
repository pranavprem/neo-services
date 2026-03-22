"""Discord webhook notifier — posts completed content and queue previews."""

import asyncio
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


async def post_reel_video(
    video_path: str,
    category: str,
    theme: str,
    caption: str,
    hashtags: list[str],
    post_id: str,
):
    """Post a reel video (MP4) to Discord as a file attachment."""
    if not DISCORD_WEBHOOK_URL:
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

    emoji = category_emoji.get(category, "🎬")
    hashtag_str = " ".join(f"#{t}" for t in hashtags[:10]) if hashtags else ""
    filename = os.path.basename(video_path)
    is_combined = "combined" in filename

    embed = {
        "title": f"{emoji} 🎬 {theme} — {'Combined Reel' if is_combined else 'Reel'}",
        "description": f"**Caption:**\n{caption}\n\n{hashtag_str}",
        "color": 0xe040fb,  # Purple for reels
        "footer": {
            "text": f"📁 {post_id} • {filename} • {category.replace('_', ' ').title()}"
        },
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            video_data = Path(video_path).read_bytes()
            resp = await client.post(
                DISCORD_WEBHOOK_URL,
                data={"payload_json": json.dumps({"embeds": [embed]})},
                files={"file": (filename, video_data, "video/mp4")},
            )
            if resp.status_code in (200, 204):
                logger.info("Posted reel %s to Discord", filename)
            else:
                logger.error("Discord reel webhook failed: %s %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("Failed to post reel to Discord: %s", e)


async def post_queue_preview(queue_items: list[dict]):
    """Post the full pipeline queue with complete prompts to Discord."""
    if not DISCORD_WEBHOOK_URL or not queue_items:
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

    # Group items by post_id
    posts: dict[str, list[dict]] = {}
    for item in queue_items:
        pid = item.get("post_id", "unknown")
        posts.setdefault(pid, []).append(item)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Header
            header = {
                "title": f"📋 Full Pipeline Queue — {len(queue_items)} images across {len(posts)} posts",
                "color": 0x00d4ff,
            }
            await client.post(DISCORD_WEBHOOK_URL, json={"embeds": [header]})
            await asyncio.sleep(0.5)

            # Send each post as its own embed (max 10 per message)
            current_batch: list[dict] = []
            for post_id, items in posts.items():
                first = items[0]
                cat = first.get("category", "unknown")
                emoji = category_emoji.get(cat, "🎨")
                theme = first.get("theme", "Untitled")
                post_type = first.get("post_type", "single")
                caption = first.get("caption", "")[:300]

                # Full prompts
                prompt_lines = []
                for item in items:
                    idx = item.get("image_index", 0) + 1
                    prompt = item.get("prompt", "No prompt")
                    prompt_lines.append(f"**Image {idx}:**\n```\n{prompt}\n```")

                description = (
                    f"**Theme:** {theme}\n"
                    f"**Type:** {post_type}\n"
                    f"**Caption:** _{caption}_\n\n"
                    + "\n".join(prompt_lines)
                )

                # Discord embed description limit is 4096
                if len(description) > 4096:
                    description = description[:4090] + "\n..."

                embed = {
                    "title": f"{emoji} [{post_id}] {theme}",
                    "description": description,
                    "color": 0x7b61ff,
                    "footer": {"text": f"{cat.replace('_', ' ').title()} • {len(items)} image(s)"},
                }

                current_batch.append(embed)
                if len(current_batch) >= 5:  # keep batches smaller for readability
                    await client.post(DISCORD_WEBHOOK_URL, json={"embeds": current_batch})
                    current_batch = []
                    await asyncio.sleep(1)  # respect rate limits

            if current_batch:
                await client.post(DISCORD_WEBHOOK_URL, json={"embeds": current_batch})

    except Exception as e:
        logger.error(f"Failed to post queue preview: {e}")
