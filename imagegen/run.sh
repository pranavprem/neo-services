#!/usr/bin/env bash
# Run the imagegen service natively (requires Metal GPU — no Docker)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PORT="${IMAGEGEN_PORT:-9502}"

# Create venv if it doesn't exist
if [ ! -d "$VENV_DIR" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
fi

# Activate and install/update deps
source "$VENV_DIR/bin/activate"
pip install -q -r "$SCRIPT_DIR/requirements.txt"

echo "Starting imagegen on port $PORT..."
exec uvicorn app:app --host 0.0.0.0 --port "$PORT" --app-dir "$SCRIPT_DIR"
