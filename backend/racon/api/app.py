"""FastAPI application factory, lifecycle orchestration, and middleware configuration."""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .log_buffer import get_log_buffer_handler
from .routes_admin import router as admin_router
from .routes_keys import router as keys_router
from .routes_benchmarks import router as benchmarks_router
from .routes_cloud import router as cloud_router
from .routes_jobs import router as jobs_router
from .routes_logs import router as logs_router
from .routes_models import router as models_router
from .routes_nodes import router as nodes_router
from .routes_scheduler import router as scheduler_router
from .routes_settings import router as settings_router
from .routes_status import router as status_router
from .routes_engine import router as engine_router
from .routes_worker import router as worker_router
from ..config import VERSION, settings
from ..hardware import discover_hardware, get_lightweight_update
from ..scheduler.laya_adapter import get_laya_adapter
from ..network.api_keys import get_api_key_service
from ..network.ratelimit import SlidingWindowLimiter
from ..security import (
    get_auth_token,
    get_token_scope,
    redact_sensitive_info,
)
from ..storage.db import get_database

logger = logging.getLogger("racon.api")

# Paths that only the administrator token may reach. The client token issued to desktop
# and web builds is deliberately not enough for operator actions.
_ADMIN_ONLY_PREFIXES = ("/api/v1/admin",)
_ADMIN_ONLY_ROUTES = {("PUT", "/api/v1/settings")}
_API_KEY_LIMITER = SlidingWindowLimiter()


def _openai_error(
    status_code: int,
    message: str,
    *,
    error_type: str = "invalid_request_error",
    code: Optional[str] = None,
    param: Optional[str] = None,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": param,
                "code": code,
            }
        },
        headers=headers,
    )


def _matches_path_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _requires_admin_scope(path: str, method: str) -> bool:
    if any(_matches_path_prefix(path, prefix) for prefix in _ADMIN_ONLY_PREFIXES):
        return True
    return (method.upper(), path.rstrip("/")) in _ADMIN_ONLY_ROUTES


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Service lifespan context:
    - Sets up in-memory logging buffer.
    - Generates ephemeral token.
    - Initializes SQLite schema.
    - Discovers hardware and registers local node.
    - Starts background hardware telemetry sampler.
    - Gracefully cleans up resources and unloads models on shutdown.
    """
    # 1. Log buffer & Ephemeral Token
    get_log_buffer_handler()
    token = get_auth_token()
    logger.info("Initializing RACoN Desktop Backend Service (v%s)...", VERSION)
    logger.info("API Token ready (%s)", redact_sensitive_info(token))

    # 2. Database schema initialization
    db = get_database()
    db.initialize()

    # 2b. Fail jobs left non-terminal by a previous process. RACoN never silently
    # resumes work whose worker-side completion cannot be proven.
    try:
        from ..orchestration.service import get_orchestration_service

        interrupted = get_orchestration_service().recover_interrupted()
        if interrupted:
            logger.warning("Marked %d interrupted orchestration job(s) as failed.", interrupted)
    except Exception as exc:
        logger.error("Could not reconcile interrupted jobs: %s", exc)

    # 2c. Coordinator identity and worker ingress. The desktop shell, the Docker
    # container and `python -m backend.racon.api` all expose /enroll, /heartbeat and
    # /tasks/* from this one app. Previously those routes only existed in the standalone
    # coordinator process, so a worker could never join the container.
    try:
        from .coordinator_state import get_coordinator_state

        state = get_coordinator_state()
        logger.info(
            "Worker ingress ready (identity %s…, auto-approve=%s, auto-join=%s).",
            state.public_key_hex[:16],
            state.auto_approve,
            bool(state.auto_join_code),
        )
        db.prune_routing_events(keep=20000)
    except Exception as exc:
        logger.error("Could not initialize worker ingress: %s", exc)

    # 2d. Distributed dispatch stays in-process, so the scheduler does not need a second
    # HTTP listener on port 8766 (which does not exist in the container).
    try:
        from ..orchestration.executor import InProcessDispatcher
        from ..orchestration.service import get_orchestration_service

        orchestration = get_orchestration_service()
        if orchestration.coordinator_dispatcher is None:
            orchestration.coordinator_dispatcher = InProcessDispatcher()
    except Exception as exc:
        logger.error("Could not attach the in-process dispatcher: %s", exc)

    # 3. Discover hardware and register local node
    snap = discover_hardware()
    local_node = {
        "id": "node-local",
        "name": f"Local Node ({snap.cpu_name[:24]})",
        "is_local": True,
        "status": "available",
        "device_type": "cuda" if (snap.gpu.supported and snap.gpu.vendor == "NVIDIA") else "cpu",
        "ram_total_mb": snap.ram_total_mb,
        "ram_available_mb": snap.ram_available_mb,
        "processing_speed_tps": None,
        "network_latency_ms": 0.0,
        "workload_pct": snap.cpu_utilization_pct,
        "capabilities": {
            "cpu_name": snap.cpu_name,
            "cpu_logical_count": snap.cpu_logical_count,
            "cpu_physical_count": snap.cpu_physical_count,
            "cpu_cores": snap.cpu_physical_count or snap.cpu_logical_count or 4,
            "os": snap.os_name,
            "arch": snap.os_architecture,
            "ram_total_mb": snap.ram_total_mb,
            "ram_available_mb": snap.ram_available_mb,
            "gpu": {"name": snap.gpu.name, "vram_total_mb": snap.gpu.vram_total_mb},
            "gpu_name": snap.gpu.name,
            "gpu_vram_mb": snap.gpu.vram_total_mb,
            "gpu_supported": snap.gpu.supported,
            "detection_method": snap.gpu.detection_method,
            "runtimes": ["cpu"],
        },
    }
    db.upsert_node(local_node)
    logger.info("Local node registered: %s (%s)", local_node["name"], local_node["device_type"])

    # 3b. Optional model preload. Otherwise the first inference request pays the full
    #     llama-server startup cost, which is tens of seconds for larger GGUF files on CPU.
    preload_model = os.environ.get("RACON_PRELOAD_MODEL", "").strip()
    if preload_model:
        try:
            from racon.services.engine_service import get_engine

            preloaded = await get_engine().load_model(preload_model)
            logger.info(
                "Preloaded model %s in %.0f ms",
                preload_model,
                float(preloaded.get("load_ms") or 0.0),
            )
        except Exception as exc:
            logger.warning("Model preload failed for %s: %s", preload_model, exc)

    # 4. Background hardware telemetry task
    stop_event = asyncio.Event()

    async def _hardware_sampler_loop():
        while not stop_event.is_set():
            try:
                upd = get_lightweight_update()
                db.update_node_heartbeat(
                    "node-local",
                    ram_available=upd["ram_available_mb"],
                )
            except Exception as e:
                logger.debug("Hardware sampler heartbeat update failed: %s", e)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    sampler_task = asyncio.create_task(_hardware_sampler_loop())

    async def _coordinator_maintenance_loop():
        """Expire silent workers so the selector never places work on a dead node."""
        from .coordinator_state import get_coordinator_state

        while not stop_event.is_set():
            try:
                coordinator = get_coordinator_state()
                stale = coordinator.registry.check_stale_heartbeats(timeout_s=30.0)
                for node_id in stale:
                    db.update_node_status(node_id, "offline")
                    db.log_routing_event(
                        "node_offline",
                        source=node_id,
                        target="coordinator",
                        state="offline",
                        message=f"{node_id} missed the 30s heartbeat window",
                    )
                    logger.warning("Worker %s marked offline after a missed heartbeat", node_id)
            except Exception as exc:
                logger.debug("Coordinator maintenance failed: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=10.0)
            except asyncio.TimeoutError:
                pass

    maintenance_task = asyncio.create_task(_coordinator_maintenance_loop())

    try:
        yield
    finally:
        # Graceful shutdown
        logger.info("Initiating graceful shutdown of RACoN Desktop Backend...")
        stop_event.set()
        await asyncio.gather(sampler_task, maintenance_task, return_exceptions=True)

        # Unload Laya models to release memory
        laya = get_laya_adapter()
        if hasattr(laya, "shutdown"):
            laya.shutdown()
        elif hasattr(laya, "unload"):
            laya.unload()

        # Stop the inference runtime so no llama-server child outlives the app.
        try:
            from racon.services.engine_service import get_engine

            await get_engine().aclose()
        except Exception as exc:
            logger.debug("Engine shutdown failed: %s", exc)

        logger.info("RACoN Backend shutdown complete.")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    app = FastAPI(
        title="RACoN Platform API",
        description="Local-first distributed AI compute orchestration and Laya-assisted scheduling API.",
        version=VERSION,
        lifespan=lifespan,
    )

    # Configure CORS: explicit allow-list, never a wildcard with credentials.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-RACON-TOKEN", "X-API-Token", "x-api-key"],
    )

    # Health endpoint without authentication
    @app.get("/health", tags=["System"])
    async def health():
        return {
            "status": "ok",
            "service": "racon-local-engine",
            "version": VERSION,
        }

    # Authentication middleware for /api/v1/* and the OpenAI-compatible /v1/* surface.
    @app.middleware("http")
    async def token_auth_middleware(request: Request, call_next):
        path = request.url.path
        protected = path.startswith("/api/v1") or path.startswith("/v1")
        if not protected:
            return await call_next(request)

        # Static assets and the SPA shell under /v1 do not exist, but keep preflight open.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Extract token from headers or query parameters
        token = (
            request.headers.get("x-api-key")
            or request.headers.get("X-RACON-TOKEN")
            or request.headers.get("X-API-Token")
            or request.headers.get("x-api-token")
        )
        if not token:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.lower().startswith("bearer "):
                token = auth_header[7:].strip()
        if not token:
            token = request.query_params.get("token") or request.query_params.get("auth_token")

        # RACoN inference keys are scoped to the public /v1 surface.
        if token and token.startswith("rk_"):
            if not path.startswith("/v1"):
                return _openai_error(
                    403,
                    "This API key is scoped to inference endpoints only",
                    error_type="permission_error",
                    code="insufficient_scope",
                )
            try:
                record = get_api_key_service().verify(token, required_scope="inference")
            except Exception as exc:
                logger.error("API key verification failed: %s", exc)
                return _openai_error(
                    503,
                    "The API key store is unavailable",
                    error_type="server_error",
                    code="key_store_unavailable",
                )
            if record is None:
                return _openai_error(
                    401,
                    "Invalid or revoked RACoN API key",
                    error_type="authentication_error",
                    code="invalid_api_key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            allowed, remaining, retry_after = _API_KEY_LIMITER.check(
                record.id, record.rate_limit_per_min
            )
            if not allowed:
                return _openai_error(
                    429,
                    "Rate limit exceeded for this RACoN API key",
                    error_type="rate_limit_error",
                    code="rate_limit_exceeded",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(record.rate_limit_per_min),
                        "X-RateLimit-Remaining": "0",
                    },
                )
            request.state.api_key_id = record.id
            request.state.api_key_record = record
            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(record.rate_limit_per_min)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response

        scope = get_token_scope(token)
        if scope is None:
            if path.startswith("/v1"):
                return _openai_error(
                    401,
                    "Missing or invalid API key",
                    error_type="authentication_error",
                    code="invalid_api_key",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "unauthorized",
                        "message": "Missing or invalid API token",
                        "details": {},
                    }
                },
            )

        if scope != "admin" and _requires_admin_scope(path, request.method):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "forbidden",
                        "message": "This operation requires the administrator token",
                        "details": {},
                    }
                },
            )

        return await call_next(request)

    # Global Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/v1"):
            message = exc.detail if isinstance(exc.detail, str) else "Request failed"
            return _openai_error(
                exc.status_code,
                message,
                error_type="invalid_request_error" if exc.status_code < 500 else "server_error",
                code="http_error",
            )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail, "status_code": exc.status_code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/v1"):
            errors = exc.errors()
            param = None
            if errors:
                location = errors[0].get("loc") or []
                if len(location) > 1:
                    param = ".".join(str(part) for part in location[1:])
            return _openai_error(
                422,
                "Invalid request payload",
                error_type="invalid_request_error",
                code="invalid_request",
                param=param,
                headers=None,
            )
        return JSONResponse(
            status_code=422,
            content={"error": "Validation Error", "detail": exc.errors(), "status_code": 422},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled server error: %s", exc, exc_info=True)
        if request.url.path.startswith("/v1"):
            return _openai_error(
                500,
                "Internal server error",
                error_type="server_error",
                code="internal_error",
            )
        return JSONResponse(
            status_code=500,
            content={"error": "Internal Server Error", "detail": str(exc), "status_code": 500},
        )

    # Register API Route Handlers
    app.include_router(status_router)
    app.include_router(nodes_router)
    app.include_router(models_router)
    app.include_router(scheduler_router)
    app.include_router(jobs_router)
    app.include_router(benchmarks_router)
    app.include_router(logs_router)
    app.include_router(settings_router)
    app.include_router(engine_router)
    app.include_router(cloud_router)
    app.include_router(worker_router)
    app.include_router(admin_router)
    app.include_router(keys_router)

    # Standalone operator dashboard. Served from the same origin as the API; the page asks
    # for the API token and then calls /api/v1/admin/*. It has no build step and no CDN.
    admin_index = Path(__file__).resolve().parent.parent / "admin_ui" / "index.html"
    if admin_index.is_file():
        @app.get("/admin", include_in_schema=False)
        @app.get("/admin/", include_in_schema=False)
        async def serve_admin_dashboard():
            return FileResponse(str(admin_index))
    else:  # pragma: no cover - only when the package was stripped
        logger.warning("Admin dashboard not found at %s", admin_index)

    # Locate static assets
    base_file = Path(__file__).resolve()
    meipass = getattr(sys, "_MEIPASS", "")
    candidates = [
        Path(meipass) / "apps" / "desktop" / "dist" if meipass else None,
        base_file.parent.parent.parent.parent / "apps" / "desktop" / "dist",
        Path.cwd() / "apps" / "desktop" / "dist",
        Path(sys.executable).parent / "apps" / "desktop" / "dist",
        Path(sys.executable).parent / "_internal" / "apps" / "desktop" / "dist",
        Path.cwd() / "dist",
    ]
    dist_dir = next((p for p in candidates if p and p.exists() and (p / "index.html").exists()), None)

    if dist_dir:
        index_file = dist_dir / "index.html"
        assets_dir = dist_dir / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="static_assets")

        @app.get("/", include_in_schema=False)
        async def serve_root():
            return FileResponse(str(index_file))

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa_fallback(full_path: str):
            if full_path.startswith("api/") or full_path.startswith("v1/") or full_path == "health":
                raise HTTPException(status_code=404, detail="Not Found")
            target = dist_dir / full_path
            if target.is_file():
                return FileResponse(str(target))
            return FileResponse(str(index_file))
    else:
        # Fallback: Redirect root to UI port 1420
        @app.get("/", include_in_schema=False)
        async def serve_root_redirect():
            return RedirectResponse(url="http://127.0.0.1:1420/")

    return app


# Default application instance for Uvicorn
app = create_app()
