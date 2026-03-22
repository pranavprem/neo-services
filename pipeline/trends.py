"""Trending topics scraper — Reddit public JSON + Google Trends RSS."""

import asyncio
import logging
import xml.etree.ElementTree as ET

import httpx

from queue_manager import QueueManager

logger = logging.getLogger(__name__)

# Reddit popular JSON (no auth needed)
REDDIT_URL = "https://www.reddit.com/r/popular.json"
# Google Trends RSS (updated URL — old /trends/trendingsearches/ path was deprecated)
GOOGLE_TRENDS_URL = "https://trends.google.com/trending/rss?geo=US"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ContentPipeline/1.0",
}


async def fetch_reddit_trending() -> list[dict]:
    """Fetch top posts from r/popular (public, no auth)."""
    topics = []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            resp = await client.get(REDDIT_URL, params={"limit": 20})
            resp.raise_for_status()
            data = resp.json()

        for post in data.get("data", {}).get("children", []):
            pd = post.get("data", {})
            topics.append({
                "topic": pd.get("title", "")[:120],
                "description": f"r/{pd.get('subreddit', '')} — {pd.get('score', 0)} upvotes",
                "score": pd.get("score", 0) / 100000,  # normalize to 0-1ish
            })
    except Exception as e:
        logger.warning("Reddit fetch failed: %s", e)

    return topics[:10]


async def fetch_google_trends() -> list[dict]:
    """Fetch Google Trends daily trending via RSS feed."""
    topics = []
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=HEADERS) as client:
            resp = await client.get(GOOGLE_TRENDS_URL)
            resp.raise_for_status()
            xml_text = resp.text

        root = ET.fromstring(xml_text)
        ns = {"ht": "https://trends.google.com/trending/rss"}

        for item in root.findall(".//item"):
            title_el = item.find("title")
            traffic_el = item.find("ht:approx_traffic", ns)
            title = title_el.text if title_el is not None else ""
            traffic = traffic_el.text if traffic_el is not None else "0"

            # Parse traffic like "200,000+"
            traffic_num = int(traffic.replace(",", "").replace("+", "") or "0")
            topics.append({
                "topic": title,
                "description": f"{traffic} searches",
                "score": min(traffic_num / 500000, 1.0),
            })
    except Exception as e:
        logger.warning("Google Trends fetch failed: %s", e)

    return topics[:10]


async def refresh_trends(queue_manager: QueueManager):
    """Fetch all trend sources and save to database."""
    reddit, google = await asyncio.gather(
        fetch_reddit_trending(),
        fetch_google_trends(),
        return_exceptions=True,
    )

    if isinstance(reddit, list) and reddit:
        await asyncio.to_thread(queue_manager.save_trends, "reddit", reddit)
        logger.info("Saved %d Reddit trends", len(reddit))

    if isinstance(google, list) and google:
        await asyncio.to_thread(queue_manager.save_trends, "google", google)
        logger.info("Saved %d Google trends", len(google))


async def trends_loop(queue_manager: QueueManager, interval: int):
    """Periodically refresh trends."""
    while True:
        try:
            await refresh_trends(queue_manager)
        except Exception as e:
            logger.error("Trends refresh failed: %s", e)
        await asyncio.sleep(interval)
