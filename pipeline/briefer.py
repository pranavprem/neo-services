"""Content brief generator using Ollama (llama3.2) for creative Instagram concepts."""

import json
import logging

import httpx

from config import OLLAMA_URL, OLLAMA_MODEL, CATEGORY_WEIGHTS, MAX_BRIEFS_PER_REQUEST

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a creative director for a futuristic tech/design Instagram account. You generate content briefs as structured JSON.

Your briefs must contain image prompts optimized for the Flux Krea Dev AI image generator. Write prompts that are:
- Highly detailed and visually specific (materials, lighting, camera angle, environment)
- Focused on photorealistic or cinematic rendering style
- 1-3 sentences, no more than 80 words per prompt
- Include specific details: "brushed titanium with blue LED accents" not just "futuristic metal"

Each brief must specify the exact post format and include an Instagram caption with engagement hook.

IMPORTANT: Output valid JSON only. No markdown, no commentary. Output a JSON object with a "briefs" key containing an array of brief objects."""

USER_PROMPT_TEMPLATE = """Generate {count} content briefs for Instagram. Use these category weights to guide variety:
- futuristic_concept (30%): Cars, watches, yachts, sneakers, headphones, phones, gaming setups, motorcycles, drones
- dream_space (15%): Luxury interiors, penthouses, underground lairs, cyberpunk apartments
- what_if (15%): "What if [Brand X] designed [Product Y]?" crossovers
- pick_your (15%): 4 variants of a concept for a 2x2 grid labeled 1-4
- then_vs_2040 (10%): Current-style product vs futuristic 2040 version (generate both with AI)
- gf_knows (10%): Same as pick_your but with relationship bait caption
- meme (5%): Just suggest a trending topic + concept (no image prompt needed)

{trending_context}

Output a JSON object with a "briefs" key containing an array where each object has:
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
- meme: empty image_prompts array, add a "meme_concept" field instead"""

VALID_CATEGORIES = set(CATEGORY_WEIGHTS.keys())
VALID_POST_TYPES = {"single", "carousel", "grid", "side_by_side"}

# Expected prompt count per post_type
EXPECTED_PROMPT_COUNTS = {
    "single": (1, 1),
    "carousel": (3, 5),
    "grid": (4, 4),
    "side_by_side": (2, 2),
}


def _format_trending(topics: list[dict]) -> str:
    if not topics:
        return ""
    lines = ["Currently trending topics (use 1-2 if they inspire good concepts):"]
    by_source: dict[str, list[str]] = {}
    for t in topics:
        by_source.setdefault(t.get("source", "other"), []).append(t["topic"])
    for source, names in by_source.items():
        lines.append(f"- {source.title()}: {', '.join(names[:5])}")
    return "\n".join(lines)


def _validate_brief(brief: dict) -> bool:
    """Validate a single brief has required fields and correct structure."""
    required = {"category", "post_type", "theme", "image_prompts", "caption"}
    if not required.issubset(brief.keys()):
        return False
    if brief["category"] not in VALID_CATEGORIES:
        return False
    if brief["post_type"] not in VALID_POST_TYPES:
        # Meme briefs have no image_prompts — skip them
        if brief["category"] == "meme":
            return False
        return False

    prompts = brief["image_prompts"]
    if not isinstance(prompts, list):
        return False

    expected = EXPECTED_PROMPT_COUNTS.get(brief["post_type"])
    if expected:
        min_count, max_count = expected
        if not (min_count <= len(prompts) <= max_count):
            return False

    return True


async def generate_briefs(
    count: int = 10,
    category: str | None = None,
    trending: list[dict] | None = None,
) -> list[dict]:
    """Generate content briefs via Ollama. Returns validated briefs only."""
    count = min(count, MAX_BRIEFS_PER_REQUEST)
    trending_context = _format_trending(trending) if trending else ""

    prompt = USER_PROMPT_TEMPLATE.format(
        count=count,
        trending_context=trending_context,
    )

    if category:
        prompt += f"\n\nFocus all briefs on the '{category}' category."

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0.9,
                        "num_predict": 4096,
                    },
                },
                timeout=120.0,
            )
            response.raise_for_status()
    except httpx.HTTPError as e:
        logger.error("Ollama request failed: %s", e)
        return []

    try:
        raw = response.json().get("response", "")
        parsed = json.loads(raw)
        # Handle both {"briefs": [...]} and bare [...]
        if isinstance(parsed, list):
            briefs = parsed
        elif isinstance(parsed, dict) and "briefs" in parsed:
            briefs = parsed["briefs"]
        else:
            logger.warning("Unexpected Ollama response structure: %s", type(parsed))
            return []
    except (json.JSONDecodeError, KeyError) as e:
        logger.error("Failed to parse Ollama response: %s", e)
        return []

    # Validate and keep only well-formed briefs
    valid = [b for b in briefs if _validate_brief(b)]
    dropped = len(briefs) - len(valid)
    if dropped:
        logger.warning("Dropped %d malformed briefs out of %d", dropped, len(briefs))

    return valid
