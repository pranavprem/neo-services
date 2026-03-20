"""Whisper STT FastAPI service."""

import os
import tempfile
from pathlib import Path

import whisper
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import JSONResponse

app = FastAPI(title="Whisper STT", version="1.0.0")

MODEL_NAME = os.getenv("WHISPER_MODEL", "base")
model = None


def get_model():
    global model
    if model is None:
        print(f"Loading Whisper model: {MODEL_NAME}")
        model = whisper.load_model(MODEL_NAME, download_root="/models")
        print("Model loaded.")
    return model


@app.on_event("startup")
async def startup():
    get_model()


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    suffix = Path(file.filename).suffix or ".wav"
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            content = await file.read()
            tmp.write(content)
            tmp_path = tmp.name

        result = get_model().transcribe(tmp_path)
        return JSONResponse(content={
            "text": result["text"].strip(),
            "language": result.get("language", ""),
            "segments": [
                {
                    "start": seg["start"],
                    "end": seg["end"],
                    "text": seg["text"].strip(),
                }
                for seg in result.get("segments", [])
            ],
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if "tmp_path" in locals():
            os.unlink(tmp_path)
