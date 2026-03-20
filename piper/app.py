"""Piper TTS FastAPI service."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Piper TTS", version="1.0.0")

PIPER_BIN = "/usr/share/piper/piper"
VOICES_DIR = Path("/models")
DEFAULT_VOICE = os.getenv("PIPER_VOICE", "en_US-lessac-medium")


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None


def get_voice_path(voice_name: str) -> Path:
    """Get path to voice ONNX model, downloading if needed."""
    model_path = VOICES_DIR / f"{voice_name}.onnx"
    config_path = VOICES_DIR / f"{voice_name}.onnx.json"

    if not model_path.exists():
        print(f"Downloading voice: {voice_name}")
        # Download from huggingface
        lang_parts = voice_name.split("-")
        # e.g. en_US-lessac-medium -> en/en_US/lessac/medium
        lang = lang_parts[0].split("_")[0]  # en
        locale = lang_parts[0]  # en_US
        name = lang_parts[1]  # lessac
        quality = lang_parts[2] if len(lang_parts) > 2 else "medium"  # medium

        base_url = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/{lang}/{locale}/{name}/{quality}"
        model_url = f"{base_url}/{voice_name}.onnx"
        config_url = f"{base_url}/{voice_name}.onnx.json"

        try:
            subprocess.run(
                ["curl", "-sL", "-o", str(model_path), model_url],
                check=True, timeout=300,
            )
            subprocess.run(
                ["curl", "-sL", "-o", str(config_path), config_url],
                check=True, timeout=60,
            )
            print(f"Voice {voice_name} downloaded.")
        except subprocess.CalledProcessError as e:
            # Clean up partial downloads
            model_path.unlink(missing_ok=True)
            config_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail=f"Failed to download voice: {e}")

    return model_path


@app.on_event("startup")
async def startup():
    """Pre-download default voice on startup."""
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        get_voice_path(DEFAULT_VOICE)
        print(f"Default voice ready: {DEFAULT_VOICE}")
    except Exception as e:
        print(f"Warning: could not pre-download default voice: {e}")


@app.get("/health")
async def health():
    return {"status": "ok", "default_voice": DEFAULT_VOICE}


@app.get("/voices")
async def list_voices():
    """List locally available voices."""
    voices = []
    for f in VOICES_DIR.glob("*.onnx"):
        name = f.stem
        config_path = VOICES_DIR / f"{name}.onnx.json"
        info = {"name": name}
        if config_path.exists():
            try:
                with open(config_path) as cf:
                    config = json.load(cf)
                    info["language"] = config.get("language", {})
                    info["num_speakers"] = config.get("num_speakers", 1)
            except Exception:
                pass
        voices.append(info)
    return {"voices": voices, "default": DEFAULT_VOICE}


@app.post("/speak")
async def speak(req: SpeakRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")

    voice = req.voice or DEFAULT_VOICE
    model_path = get_voice_path(voice)

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp:
            tmp_path = tmp.name

        result = subprocess.run(
            [
                PIPER_BIN,
                "--model", str(model_path),
                "--output_file", tmp_path,
            ],
            input=req.text,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise HTTPException(
                status_code=500,
                detail=f"Piper error: {result.stderr}",
            )

        return FileResponse(
            tmp_path,
            media_type="audio/wav",
            filename="speech.wav",
            # Clean up after sending
            background=None,
        )
    except HTTPException:
        raise
    except Exception as e:
        if "tmp_path" in locals():
            Path(tmp_path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e))
