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
from .routes_benchmarks import router as benchmarks_router
from .routes_jobs import router as jobs_router
from .routes_logs import router as logs_router
from .routes_models import router as models_router
from .routes_nodes import router as nodes_router
from .routes_scheduler import router as scheduler_router
from .routes_settings import router as settings_router
from .routes_status import router as status_router
from ..config import VERSION, settings
from ..hardware import discover_hardware, get_lightweight_update
from ..scheduler.laya_adapter import get_laya_adapter
from ..security import (
    get_auth_token,
    redact_sensitive_info,
    verify_request_token,
)
from ..storage.db import get_database

logger = logging.getLogger("racon.api")


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
        "processing_speed_tps": 30.0,
        "network_latency_ms": 0.0,
        "workload_pct": snap.cpu_utilization_pct,
        "capabilities": {
            "cpu_logical_count": snap.cpu_logical_count,
            "cpu_physical_count": snap.cpu_physical_count,
            "cpu_cores": snap.cpu_physical_count or snap.cpu_logical_count or 4,
            "os": f"{snap.os_name} {snap.os_version}",
            "arch": snap.os_architecture,
            "ram_total_mb": snap.ram_total_mb,
            "ram_available_mb": snap.ram_available_mb,
            "gpu_name": snap.gpu.name,
            "gpu_supported": snap.gpu.supported,
            "detection_method": snap.gpu.detection_method,
        },
    }
    db.upsert_node(local_node)
    logger.info("Local node registered: %s (%s)", local_node["name"], local_node["device_type"])

    # 4. Background hardware telemetry task
    stop_event = asyncio.Event()

    async def _hardware_sampler_loop():
        while not stop_event.is_set():
            try:
                upd = get_lightweight_update()
                db.update_node_heartbeat(
                    "node-local",
                    ram_available=upd["ram_available_mb"],
                    workload_pct=upd["cpu_utilization_pct"],
                )
            except Exception as e:
                logger.debug("Hardware sampler heartbeat update failed: %s", e)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass

    sampler_task = asyncio.create_task(_hardware_sampler_loop())

    try:
        yield
    finally:
        # Graceful shutdown
        logger.info("Initiating graceful shutdown of RACoN Desktop Backend...")
        stop_event.set()
        await sampler_task

        # Unload Laya models to release memory
        laya = get_laya_adapter()
        if hasattr(laya, "shutdown"):
            laya.shutdown()
        elif hasattr(laya, "unload"):
            laya.unload()

        logger.info("RACoN Backend shutdown complete.")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application instance."""
    app = FastAPI(
        title="RACoN Platform API",
        description="Local-first distributed AI compute orchestration and Laya-assisted scheduling API.",
        version=VERSION,
        lifespan=lifespan,
    )

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health endpoint without authentication
    @app.get("/health", tags=["System"])
    async def health():
        return {
            "status": "ok",
            "service": "racon-coordinator",
            "version": VERSION,
        }

    # Authentication Middleware for /api/v1/*
    @app.middleware("http")
    async def token_auth_middleware(request: Request, call_next):
        # Only require token verification on /api/v1/* routes
        if not request.url.path.startswith("/api/v1"):
            return await call_next(request)

        # Allow CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # Extract token from headers or query parameters
        token = (
            request.headers.get("X-RACON-TOKEN")
            or request.headers.get("x-racon-token")
            or request.headers.get("X-RACoN-Token")
            or request.headers.get("X-API-Token")
            or request.headers.get("x-api-token")
        )
        if not token:
            auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header[7:].strip()
        if not token:
            token = request.query_params.get("token") or request.query_params.get("auth_token")

        if not token or not verify_request_token(token):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "detail": "Missing or invalid session token",
                },
            )

        return await call_next(request)

    # Global Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail, "status_code": exc.status_code},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": "Validation Error", "detail": exc.errors(), "status_code": 422},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled server error: %s", exc, exc_info=True)
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
            if full_path.startswith("api/") or full_path == "health":
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
