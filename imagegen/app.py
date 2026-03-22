"""Image generation service using mflux (MLX-based Flux on Apple Silicon)."""

import io
import os
import random
import tempfile
import threading
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

# Lazy-loaded at startup
flux_model = None

# Thread-safe progress state for the pipeline to poll
_progress_lock = threading.Lock()
_progress: dict = {
    "active": False,
    "step": 0,
    "total_steps": 0,
    "start_time": None,
    "elapsed": 0.0,
    "eta_seconds": None,
    "prompt": None,
}


class ProgressTracker:
    """Registered with mflux callbacks to track generation progress per step."""

    def call_before_loop(self, seed, prompt, latents, config, **kwargs):
        with _progress_lock:
            _progress["active"] = True
            _progress["step"] = 0
            _progress["total_steps"] = config.num_inference_steps
            _progress["start_time"] = time.time()
            _progress["elapsed"] = 0.0
            _progress["eta_seconds"] = None
            _progress["prompt"] = prompt

    def call_in_loop(self, t, seed, prompt, latents, config, time_steps, **kwargs):
        with _progress_lock:
            step = _progress["step"] + 1
            _progress["step"] = step
            elapsed = time.time() - (_progress["start_time"] or time.time())
            _progress["elapsed"] = elapsed
            total = _progress["total_steps"]
            if step > 0 and total > 0:
                per_step = elapsed / step
                _progress["eta_seconds"] = (total - step) * per_step

    def call_after_loop(self, seed, prompt, latents, config, **kwargs):
        with _progress_lock:
            _progress["active"] = False
            _progress["step"] = _progress["total_steps"]
            _progress["eta_seconds"] = 0
            _progress["elapsed"] = time.time() - (_progress["start_time"] or time.time())
            _progress["prompt"] = None


MODEL_NAME = os.environ.get("IMAGEGEN_MODEL", "krea-dev")
QUANTIZE = int(os.environ.get("IMAGEGEN_QUANTIZE", "8"))  # 8-bit for quality


# Default steps: schnell=4, dev/krea-dev=30
# Default guidance: schnell doesn't use it, dev/krea-dev=4.5 (official recommendation: 3.5-5.0)
DEFAULT_STEPS = 4 if "schnell" in MODEL_NAME else 30
DEFAULT_GUIDANCE = 3.5 if "schnell" in MODEL_NAME else 4.5


class GenerateRequest(BaseModel):
    prompt: str
    width: int = Field(default=1024, ge=256, le=2048)
    height: int = Field(default=1024, ge=256, le=2048)
    steps: int = Field(default=DEFAULT_STEPS, ge=1, le=50)
    guidance: float = Field(default=DEFAULT_GUIDANCE, ge=1.0, le=20.0)
    seed: int | None = None


def load_model():
    """Load Flux model. First run downloads weights (~3.5GB for schnell-4bit)."""
    global flux_model
    from mflux.models.flux.variants.txt2img.flux import Flux1
    from mflux.models.common.config import ModelConfig

    print(f"Loading flux-{MODEL_NAME} (quantized={QUANTIZE})...")
    start = time.time()
    flux_model = Flux1(
        model_config=ModelConfig.from_name(model_name=MODEL_NAME),
        quantize=QUANTIZE,
    )
    flux_model.callbacks.register(ProgressTracker())
    print(f"Model loaded in {time.time() - start:.1f}s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_model()
    yield


app = FastAPI(title="ImageGen Service", lifespan=lifespan)

# Allow CORS for web UI
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _generate_image(prompt: str, width: int, height: int, steps: int, seed: int,
                    guidance: float = DEFAULT_GUIDANCE,
                    init_image_path: str | None = None, init_image_strength: float | None = None):
    """Core generation logic shared by both endpoints."""
    kwargs = dict(
        seed=seed,
        prompt=prompt,
        num_inference_steps=steps,
        guidance=guidance,
        height=height,
        width=width,
    )
    if init_image_path is not None:
        kwargs["image_path"] = init_image_path
        kwargs["image_strength"] = init_image_strength if init_image_strength is not None else 0.4

    image = flux_model.generate_image(**kwargs)

    buf = io.BytesIO()
    image.image.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()


@app.post("/generate")
async def generate(
    # Accept either JSON body or multipart form data
    prompt: str = Form(None),
    width: int = Form(1024),
    height: int = Form(1024),
    steps: int = Form(DEFAULT_STEPS),
    guidance: float = Form(DEFAULT_GUIDANCE),
    seed: int = Form(None),
    strength: float = Form(None),
    image: UploadFile = File(None),
):
    """Generate an image from a text prompt, optionally with a reference image (img2img).

    Accepts either:
    - JSON body (text-to-image only): {"prompt": "...", "width": 1024, ...}
    - Multipart form data (text-to-image or img2img): prompt, width, height, steps, seed, strength, image
    """
    if flux_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    # If no form prompt, try parsing JSON body
    if prompt is None:
        from starlette.requests import Request
        raise HTTPException(status_code=422, detail="prompt is required")

    actual_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
    init_image_path = None

    try:
        # Handle reference image for img2img
        if image is not None:
            contents = await image.read()
            suffix = os.path.splitext(image.filename or "img.png")[1] or ".png"
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            tmp.write(contents)
            tmp.close()
            init_image_path = tmp.name

        start = time.time()
        png_bytes = _generate_image(
            prompt=prompt,
            width=width,
            height=height,
            steps=steps,
            seed=actual_seed,
            guidance=guidance,
            init_image_path=init_image_path,
            init_image_strength=strength,
        )
        gen_time = time.time() - start

        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "X-Seed": str(actual_seed),
                "X-Generation-Time": f"{gen_time:.2f}",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")
    finally:
        if init_image_path and os.path.exists(init_image_path):
            os.unlink(init_image_path)


@app.post("/generate/json")
async def generate_json(req: GenerateRequest):
    """Text-to-image only via JSON body. Returns PNG binary."""
    if flux_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    seed = req.seed if req.seed is not None else random.randint(0, 2**32 - 1)

    try:
        start = time.time()
        png_bytes = _generate_image(
            prompt=req.prompt,
            width=req.width,
            height=req.height,
            steps=req.steps,
            seed=seed,
            guidance=req.guidance,
        )
        gen_time = time.time() - start

        return Response(
            content=png_bytes,
            media_type="image/png",
            headers={
                "X-Seed": str(seed),
                "X-Generation-Time": f"{gen_time:.2f}",
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


@app.get("/progress")
async def progress():
    """Current generation progress — polled by the pipeline for live dashboard updates."""
    with _progress_lock:
        return dict(_progress)


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
