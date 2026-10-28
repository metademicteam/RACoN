"""Public API gateway: authenticates API keys and proxies OpenAI-compatible /v1 calls to the head.

Only /health and /v1/* are exposed. Anything else returns 404, so the gateway can be published
through Tailscale Funnel without exposing the head, workers or admin functions.
"""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from racon.cluster.auth import ApiKeyStore
from racon.cluster.config import ClusterConfig
from racon.cluster.ratelimit import SlidingWindowLimiter

logger = logging.getLogger(__name__)

ALLOWED_V1_PATHS = {"/v1/chat/completions", "/v1/models", "/v1/completions", "/v1/embeddings"}
HOP_BY_HOP = {
    "host", "connection", "content-length", "transfer-encoding", "authorization", "x-api-key", "accept-encoding",
    "cf-connecting-ip", "cf-ray", "cf-visitor", "x-forwarded-for", "x-forwarded-proto", "x-real-ip",
}


def _extract_key(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("x-api-key")


def _client_ip(request: Request) -> str:
    """Real client address. The gateway only listens on loopback, so cloudflared is the only peer."""
    return (
        request.headers.get("cf-connecting-ip")
        or (request.client.host if request.client else "unknown")
    )


def _requested_max_tokens(body: bytes) -> Optional[int]:
    try:
        value = json.loads(body).get("max_tokens")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _rate_limited(retry_after: float) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": "Rate limit exceeded", "type": "rate_limit_error"}},
        status_code=429,
        headers={"Retry-After": str(max(1, int(retry_after) + 1))},
    )


def create_gateway(
    cfg: ClusterConfig,
    keys: ApiKeyStore,
    head_url: str,
    head_api_key: str,
    max_request_bytes: int = 256 * 1024,
    ip_limit_per_min: int = 30,
    key_limit_per_min: int = 60,
) -> FastAPI:
    ip_limiter = SlidingWindowLimiter(ip_limit_per_min)
    key_limiter = SlidingWindowLimiter(key_limit_per_min)
    client = httpx.AsyncClient(
        base_url=head_url,
        headers={"Authorization": f"Bearer {head_api_key}"},
        timeout=httpx.Timeout(600.0, connect=10.0),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await client.aclose()

    app = FastAPI(
        title="RACoN Cluster Gateway",
        version="1.0.1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "model": cfg.model_alias}

    @app.api_route("/v1/{rest:path}", methods=["GET", "POST"])
    async def proxy_v1(rest: str, request: Request) -> Response:
        path = f"/v1/{rest}"
        if path not in ALLOWED_V1_PATHS:
            return JSONResponse({"error": {"message": "Not found", "type": "invalid_request_error"}}, status_code=404)

        allowed, retry = ip_limiter.check(_client_ip(request))
        if not allowed:
            return _rate_limited(retry)

        token = _extract_key(request) or ""
        if not keys.verify(token):
            return JSONResponse(
                {"error": {"message": "Invalid or missing API key", "type": "authentication_error"}},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )

        allowed, retry = key_limiter.check(token)
        if not allowed:
            return _rate_limited(retry)

        body = await request.body()
        if len(body) > max_request_bytes:
            return JSONResponse({"error": {"message": "Request too large", "type": "invalid_request_error"}}, status_code=413)
        if request.method == "POST":
            requested = _requested_max_tokens(body)
            if requested is not None and requested > cfg.max_tokens_limit:
                return JSONResponse(
                    {"error": {
                        "message": f"max_tokens must be <= {cfg.max_tokens_limit}",
                        "type": "invalid_request_error",
                    }},
                    status_code=400,
                )

        headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
        upstream = client.build_request(request.method, path, content=body, headers=headers, params=request.query_params)
        try:
            resp = await client.send(upstream, stream=True)
        except httpx.TransportError as exc:
            logger.error("Head unreachable: %s", exc)
            return JSONResponse({"error": {"message": "Model backend unavailable", "type": "server_error"}}, status_code=503)

        async def relay():
            try:
                async for chunk in resp.aiter_raw():
                    yield chunk
            finally:
                await resp.aclose()

        content_type = resp.headers.get("content-type", "application/json")
        return StreamingResponse(relay(), status_code=resp.status_code, media_type=content_type)

    @app.exception_handler(404)
    async def not_found(request: Request, exc) -> JSONResponse:
        return JSONResponse({"error": {"message": "Not found", "type": "invalid_request_error"}}, status_code=404)

    return app
