"""Pipeline configuration — all tunables in one place."""

import os

# External service URLs
IMAGEGEN_URL = os.environ.get("IMAGEGEN_URL", "http://localhost:9502")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")

# Pipeline server
PIPELINE_PORT = int(os.environ.get("PIPELINE_PORT", "9504"))

# Image generation defaults
DEFAULT_STEPS = int(os.environ.get("DEFAULT_STEPS", "40"))
DEFAULT_GUIDANCE = float(os.environ.get("DEFAULT_GUIDANCE", "4.5"))
DEFAULT_WIDTH = 1024
DEFAULT_HEIGHT = 1024

# Queue management
QUEUE_REFILL_THRESHOLD = 5
BRIEFS_PER_BATCH = 10
MAX_QUEUE_SIZE = 1000
MAX_BRIEFS_PER_REQUEST = 20

# Progress polling interval (seconds)
PROGRESS_POLL_INTERVAL = 5

# Renderer retry settings
MAX_RETRIES = 3
RETRY_BACKOFF = [10, 30, 90]  # seconds between retries

# Trends refresh interval (seconds)
TRENDS_REFRESH_INTERVAL = 3 * 3600  # 3 hours

# Output directory
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output")

# Database path
DB_PATH = os.path.join(os.path.dirname(__file__), "pipeline.db")

# Category weights (configurable via API)
CATEGORY_WEIGHTS = {
    "futuristic_concept": 0.30,
    "dream_space": 0.15,
    "what_if": 0.15,
    "pick_your": 0.15,
    "then_vs_2040": 0.10,
    "gf_knows": 0.10,
    "meme": 0.05,
}

# Compositor settings
GRID_CELL_SIZE = 512
GRID_PADDING = 40
GRID_GAP = 20
GRID_BG_COLOR = "#0f0f1a"
GRID_LABEL_SIZE = 48

SIDE_BY_SIDE_PADDING = 30
SIDE_BY_SIDE_DIVIDER_WIDTH = 4
SIDE_BY_SIDE_ACCENT_COLOR = "#00d4ff"
