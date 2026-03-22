#!/bin/bash
cd "$(dirname "$0")"

# Load .env if present (contains DISCORD_WEBHOOK_URL etc.)
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# Create output dir and venv if missing
mkdir -p output

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -r requirements.txt -q

echo "Starting Content Pipeline on port 9504..."
uvicorn server:app --host 0.0.0.0 --port 9504 --reload
