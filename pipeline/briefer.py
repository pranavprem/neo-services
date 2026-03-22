"""Content brief generator using Ollama (llama3.2) for creative Instagram concepts."""

import json
import logging

import httpx

from config import OLLAMA_URL, OLLAMA_MODEL, CATEGORY_WEIGHTS, MAX_BRIEFS_PER_REQUEST

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an elite creative director and prompt engineer for a viral futuristic tech/design Instagram account. You generate content briefs as structured JSON.

Your image prompts are for the Flux Krea Dev AI image generator, which excels at photorealism. Every prompt MUST follow these rules:

PROMPT LENGTH: Each prompt MUST be 50-150 words. The Flux sweet spot is 30-80 words for simple subjects, but our detailed product/concept shots need 80-150 words. Beyond 200 words the model compresses internally and drops details, so stay under 150. IMPORTANT: Front-load the subject (what the image IS) in the first sentence — Flux weighs earlier tokens more heavily. Structure: Subject first → Materials/textures → Environment → Lighting → Camera → Render quality. Every word must be purposeful — no filler.

MANDATORY ELEMENTS in every prompt (include ALL of these):
1. SUBJECT: What is the main object? Be hyper-specific (not "a car" but "a low-slung hypercar with gullwing doors and an angular silhouette")
2. MATERIALS & TEXTURES: Name 2-3 specific materials (e.g. "brushed gunmetal titanium", "frosted sapphire crystal", "hand-stitched alcantara", "micro-etched carbon fiber weave", "anodized midnight blue aluminum")
3. LIGHTING: Specify the exact lighting setup (e.g. "dramatic side-lighting with warm amber key light and cool blue fill", "golden hour volumetric rays through floor-to-ceiling windows", "soft studio rim lighting with subtle lens flare")
4. CAMERA: Lens focal length, aperture, and angle (e.g. "85mm lens, f/1.4, low angle hero shot", "24mm wide angle, f/8, environmental portrait")
5. ENVIRONMENT/MOOD: Background and atmosphere (e.g. "on a rain-slicked Tokyo rooftop at dusk with neon reflections", "floating above clouds at sunrise, volumetric fog")
6. RENDER QUALITY: Always end with the FULL string: "hyperrealistic, 8K, Octane render quality, ray-traced reflections, photorealistic"
7. SURFACE IMPERFECTIONS: Add subtle realism — fingerprint smudges on glass, micro-scratches on metal, dust motes in light beams, condensation droplets, wear marks on leather. This prevents the "too perfect AI look".

AVOID: Generic filler like "futuristic design" or "cinematic lighting" without specifics. Every adjective must paint a picture.
CRITICAL AVOID LIST (these produce bad AI artifacts):
- NO people, faces, hands, couples, riders, or human figures of any kind. Not even silhouettes.
- NO text, brand names, logos, labels, screens with text, or any readable writing. Flux cannot render text.
- NO holographic displays showing data/text/UI. Use abstract light effects instead.
- NO complex mechanical interiors (engines, exposed circuitry). Keep mechanical details external/surface-level.
- Focus ONLY on OBJECTS, SPACES, and PRODUCTS shot as hero subjects on clean backgrounds or in atmospheric environments.

VARIETY: Use different materials, color palettes, and lighting setups across prompts. Don't repeat "brushed titanium" and "cinematic lighting" in every prompt. Explore: matte black ceramic, liquid mercury chrome, weathered brass, translucent resin, hammered copper, pearl white enamel, smoked glass, volcanic basalt stone.

Each brief must specify the exact post format and include an Instagram caption with a strong engagement hook (question, poll, or call-to-action).

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

    # Reject briefs with prompts shorter than 40 words
    for p in prompts:
        if isinstance(p, str) and len(p.split()) < 30:
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
                timeout=600.0,  # 10 minutes — large model generating structured JSON needs time
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
