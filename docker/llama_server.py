"""llama.cpp inference microservice for the optional Docker stack.

This service is a thin, authenticated-free internal proxy. It forwards to a real
``llama-server`` reachable at ``LLAMA_BACKEND_URL``. If no backend is configured it refuses
the request instead of returning invented text.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("racn-llama")

app = FastAPI(title="RACoN llama.cpp Inference Server", version="1.2.0")

BACKEND_URL = os.environ.get("LLAMA_BACKEND_URL", "").rstrip("/")
BACKEND_API_KEY = os.environ.get("LLAMA_BACKEND_API_KEY", "")


class GenerateRequest(BaseModel):
    model: str = "racon"
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.7


def _backend_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {BACKEND_API_KEY}"} if BACKEND_API_KEY else {}


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok" if BACKEND_URL else "unconfigured",
        "service": "racn-llama",
        "backend": BACKEND_URL or None,
        "threads": int(os.environ.get("LLAMA_THREADS", "4")),
    }


@app.post("/completion")
@app.post("/v1/completions")
async def completion(req: GenerateRequest):
    if not BACKEND_URL:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "runtime_unavailable",
                    "message": "LLAMA_BACKEND_URL is not configured; start a real llama-server.",
                    "details": {},
                }
            },
        )

    payload = {
        "model": req.model,
        "prompt": req.prompt,
        "max_tokens": req.max_tokens,
        "temperature": req.temperature,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=10.0)) as client:
            response = await client.post(
                f"{BACKEND_URL}/v1/completions", json=payload, headers=_backend_headers()
            )
    except httpx.HTTPError as exc:
        logger.warning("Backend request failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={
                "error": {
                    "code": "backend_unreachable",
                    "message": str(exc),
                    "details": {"backend": BACKEND_URL},
                }
            },
        )

    if response.status_code != 200:
        return JSONResponse(
            status_code=response.status_code,
            content={
                "error": {
                    "code": "backend_error",
                    "message": response.text[:512],
                    "details": {"backend": BACKEND_URL},
                }
            },
        )
    return response.json()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8080)
