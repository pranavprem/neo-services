"""Image generation service using mflux (MLX-based Flux on Apple Silicon)."""

import io
import random
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

# Lazy-loaded at startup
flux_model = None
MODEL_NAME = "schnell"
QUANTIZE = 4  # 4-bit quantization for speed/memory


class GenerateRequest(BaseModel):
    prompt: str
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=4, ge=1, le=50)
    seed: int | None = None


def load_model():
    """Load Flux model. First run downloads weights (~3.5GB for schnell-4bit)."""
    global flux_model
    from mflux import Flux1

    print(f"Loading flux-{MODEL_NAME} (quantized={QUANTIZE})...")
    start = time.time()
    flux_model = Flux1.from_alias(MODEL_NAME, quantize=QUANTIZE)
    print(f"Model loaded in {time.time() - start:.1f}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    yield


app = FastAPI(title="ImageGen Service", lifespan=lifespan)


@app.post("/generate")
async def generate(req: GenerateRequest):
    """Generate an image from a text prompt. Returns PNG binary."""
    if flux_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    from mflux import Config

    seed = req.seed if req.seed is not None else random.randint(0, 2**32 - 1)

    try:
        image = flux_model.generate_image(
            seed=seed,
            prompt=req.prompt,
            config=Config(
                num_inference_steps=req.steps,
                height=req.height,
                width=req.width,
            ),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    # Convert PIL image to PNG bytes
    buf = io.BytesIO()
    image.image.save(buf, format="PNG")
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"X-Seed": str(seed)},
    )


@app.get("/health")
async def health():
    return {
        "status": "ok" if flux_model is not None else "loading",
        "model": f"flux-{MODEL_NAME}",
        "quantize": QUANTIZE,
        "device": "mps",
    }


@app.get("/models")
async def models():
    return {
        "models": [f"flux-{MODEL_NAME}"],
        "active": f"flux-{MODEL_NAME}",
        "quantize": QUANTIZE,
    }
