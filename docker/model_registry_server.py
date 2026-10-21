"""Dedicated Model Registry Microservice for RACoN."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("racn-model-registry")

app = FastAPI(title="RACoN Model Registry Service", version="1.1.0")

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models"))
MODELS_DIR.mkdir(parents=True, exist_ok=True)


class RegisterModelRequest(BaseModel):
    name: str
    format: str = "gguf"
    parameter_size: str = "1B"
    quantization: str = "Q4_K_M"
    url: str | None = None


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "racn-model-registry",
        "models_count": len(list(MODELS_DIR.glob("*.gguf"))),
    }


@app.get("/api/models")
async def list_registered_models():
    models = []
    for f in MODELS_DIR.glob("*"):
        if f.is_file():
            models.append({
                "name": f.name,
                "size_bytes": f.stat().st_size,
                "path": str(f),
                "format": f.suffix.replace(".", "") or "gguf",
            })
    return {"models": models, "count": len(models)}


@app.post("/api/models/register")
async def register_model(req: RegisterModelRequest):
    logger.info("Registering model: %s (%s)", req.name, req.format)
    return {"status": "registered", "model": req.model_dump()}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
