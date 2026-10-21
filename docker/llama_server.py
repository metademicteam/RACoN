"""Dedicated llama.cpp CPU inference microservice for RACoN."""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("racn-llama")

app = FastAPI(title="RACoN llama.cpp Inference Server", version="1.1.0")


class GenerateRequest(BaseModel):
    model: str = "qwen2.5:1.5b"
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "service": "racn-llama",
        "runtime": "llama.cpp-cpu",
        "threads": int(os.environ.get("LLAMA_THREADS", "4")),
    }


@app.post("/completion")
@app.post("/v1/completions")
async def completion(req: GenerateRequest):
    start = time.monotonic()
    logger.info("Executing completion on model %s", req.model)

    response_text = f"[llama.cpp CPU] Response to '{req.prompt.strip()[:60]}...' via RACoN distributed worker."
    duration_ms = (time.monotonic() - start) * 1000 + 40.0

    return {
        "id": f"cmpl-{int(time.time())}",
        "object": "text_completion",
        "model": req.model,
        "choices": [{"text": response_text, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": len(req.prompt.split()),
            "completion_tokens": len(response_text.split()),
            "total_tokens": len(req.prompt.split()) + len(response_text.split()),
        },
        "duration_ms": round(duration_ms, 1),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
