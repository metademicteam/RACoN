"""Inference engine API: real local generation plus model lifecycle.

These routes expose exactly what the engine actually did. There is no simulated fallback:
if no runtime can serve a model, the caller receives a structured 503.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from backend.racon.network.catalog import (
    is_advertisable_model,
    merge_model_entries,
    network_model_entries,
)
from backend.racon.orchestration.model_router import (
    build_profiles_from_engine,
    route_request,
)
from racon.models.manager import ModelStorageError
from racon.runtime.types import (
    ChatMessage,
    GenerationOptions,
    InferenceCancelledError,
    ModelNotFoundError,
    ModelNotLoadedError,
    RaconRuntimeError,
    RuntimeUnavailableError,
)
from racon.services.engine_service import get_engine

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Engine"])

ROLE_ALIASES = {"human": "user", "ai": "assistant", "model": "assistant"}


class ChatMessagePayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = "user"
    content: Optional[Any] = ""
    name: Optional[str] = None
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    model: str
    messages: list[ChatMessagePayload]
    max_tokens: int = Field(default=512, ge=1, le=32768)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.95, ge=0.0, le=1.0)
    top_k: Optional[int] = Field(default=None, ge=0, le=1000)
    stop: Optional[list[str]] = None
    stream: bool = False
    stream_options: Optional[dict[str, Any]] = None
    user: Optional[str] = None
    n: int = Field(default=1, ge=1, le=1)
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Optional[Any] = None
    parallel_tool_calls: Optional[bool] = None
    response_format: Optional[dict[str, Any]] = None
    seed: Optional[int] = None
    presence_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    frequency_penalty: Optional[float] = Field(default=None, ge=-2.0, le=2.0)
    metadata: Optional[dict[str, Any]] = None


class LoadModelRequest(BaseModel):
    model: str
    context_size: Optional[int] = None


class ImportModelRequest(BaseModel):
    path: str = Field(..., min_length=1, max_length=4096)
    model_id: Optional[str] = Field(default=None, max_length=128)
    copy_file: bool = Field(default=True, alias="copy")


def _to_messages(payload: list[ChatMessagePayload]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for item in payload:
        role = ROLE_ALIASES.get(item.role, item.role)
        if role not in ("system", "user", "assistant", "tool"):
            role = "user"
        messages.append(
            ChatMessage(
                role=role,  # type: ignore[arg-type]
                content=item.content,
                name=item.name,
                tool_calls=item.tool_calls,
                tool_call_id=item.tool_call_id,
            )
        )
    return messages


def _engine_kwargs(request: ChatCompletionRequest) -> dict[str, Any]:
    return {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "top_k": request.top_k,
        "stop": request.stop,
        "tools": request.tools,
        "tool_choice": request.tool_choice,
        "response_format": request.response_format,
        "seed": request.seed,
        "presence_penalty": request.presence_penalty,
        "frequency_penalty": request.frequency_penalty,
        "parallel_tool_calls": request.parallel_tool_calls,
    }


def _openai_chat_error(exc: Exception) -> JSONResponse:
    if isinstance(exc, RaconRuntimeError):
        status = exc.http_status
        error_type = (
            "invalid_request_error"
            if 400 <= status < 500
            else "server_error"
        )
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "message": exc.message,
                    "type": error_type,
                    "param": None,
                    "code": exc.code,
                    "details": exc.details,
                }
            },
        )
    if isinstance(exc, ModelStorageError):
        return JSONResponse(
            status_code=404 if exc.code == "model_not_found" else 409,
            content={
                "error": {
                    "message": exc.message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": exc.code,
                    "details": exc.details,
                }
            },
        )
    logger.error("Unhandled chat error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "message": str(exc),
                "type": "server_error",
                "param": None,
                "code": "internal_error",
            }
        },
    )


def _engine():
    return get_engine()


async def _resolve_auto_route(request: ChatCompletionRequest):
    """Resolve ``model="auto"`` with the deterministic router."""
    profiles = await build_profiles_from_engine(_engine())
    ram_available_mb = None
    try:
        import psutil

        ram_available_mb = float(psutil.virtual_memory().available) / (1024 * 1024)
    except Exception:
        pass
    return route_request(
        [message.model_dump() for message in request.messages],
        requested_model=request.model,
        profiles=profiles,
        tools=request.tools,
        ram_available_mb=ram_available_mb,
    )


def _error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, RaconRuntimeError):
        return JSONResponse(status_code=exc.http_status, content=exc.to_dict())
    if isinstance(exc, ModelStorageError):
        status = 404 if exc.code in ("model_not_found",) else 409
        if exc.code == "insufficient_disk_space":
            status = 507
        return JSONResponse(
            status_code=status,
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )
    logger.error("Unhandled engine error: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": str(exc), "details": {}}},
    )


# ---------------------------------------------------------------------------
# OpenAI-compatible surface
# ---------------------------------------------------------------------------

def _model_ids() -> list[dict[str, Any]]:
    engine = _engine()
    entries: dict[str, dict[str, Any]] = {
        "auto": {
            "id": "auto",
            "object": "model",
            "owned_by": "racon",
            "racon": {
                "virtual": True,
                "installed": True,
                "description": (
                    "Deterministic smart router: classifies the request and selects a "
                    "capable installed model. The decision is returned in racon.router."
                ),
            },
        }
    }
    for model in engine.models.installed():
        if not is_advertisable_model(model.id):
            continue
        entries[model.id] = {
            "id": model.id,
            "object": "model",
            "owned_by": "racon",
            "racon": {
                "runtime": model.runtime,
                "size_bytes": model.size_bytes,
                "family": model.family,
                "context_length": model.context_length,
                "source": model.source,
                "installed": True,
            },
        }
    for entry in engine.models.catalog_entries():
        if entry.id in entries or not is_advertisable_model(entry.id):
            continue
        entries[entry.id] = {
            "id": entry.id,
            "object": "model",
            "owned_by": "racon",
            "racon": {
                "runtime": entry.runtime,
                "context_length": entry.context_length,
                "parameter_size": entry.parameter_size,
                "quantization": entry.quantization,
                "source": entry.source,
                "installed": False,
            },
        }
    return merge_model_entries(list(entries.values()), network_model_entries())


@router.get("/v1/models")
async def list_models() -> dict[str, Any]:
    return {"object": "list", "data": _model_ids()}


@router.get("/v1/models/{model_id}")
async def get_model(model_id: str) -> dict[str, Any]:
    for item in _model_ids():
        if item["id"] == model_id:
            return item
    raise HTTPException(status_code=404, detail=f"Unknown model '{model_id}'")


async def _sse_stream(
    request: ChatCompletionRequest,
    router_payload: Optional[dict[str, Any]] = None,
) -> AsyncIterator[bytes]:
    engine = _engine()
    messages = _to_messages(request.messages)
    created = int(time.time())
    completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
    started = time.monotonic()
    first_delta_at: Optional[float] = None
    usage: dict[str, Any] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reported": False}

    def frame(payload: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")

    def chunk_frame(
        delta: dict[str, Any],
        *,
        finish_reason: Optional[str] = None,
        usage_payload: Optional[dict[str, Any]] = None,
        racon: Optional[dict[str, Any]] = None,
    ) -> bytes:
        payload: dict[str, Any] = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": request.model,
            "choices": [
                {"index": 0, "delta": delta, "finish_reason": finish_reason}
            ],
        }
        if usage_payload is not None:
            payload["usage"] = usage_payload
        if racon:
            payload["racon"] = racon
        return frame(payload)

    def stream_error(exc: RaconRuntimeError) -> bytes:
        return frame(
            {
                "error": {
                    "message": exc.message,
                    "type": "server_error",
                    "param": None,
                    "code": exc.code,
                    "details": exc.details,
                }
            }
        )

    try:
        yield chunk_frame({"role": "assistant"})
        async for chunk in engine.chat_stream(messages, **_engine_kwargs(request)):
            if (chunk.delta or chunk.tool_calls) and first_delta_at is None:
                first_delta_at = time.monotonic()
            if chunk.usage is not None:
                usage = chunk.usage.to_dict()
            delta: dict[str, Any] = {}
            if chunk.role:
                delta["role"] = chunk.role
            if chunk.delta:
                delta["content"] = chunk.delta
            if chunk.tool_calls:
                delta["tool_calls"] = chunk.tool_calls
            if delta:
                yield chunk_frame(delta)
            if chunk.done:
                ttft_ms = (first_delta_at - started) * 1000.0 if first_delta_at else None
                yield chunk_frame(
                    {},
                    finish_reason=chunk.finish_reason or "stop",
                    usage_payload=usage,
                    racon={
                        "runtime": engine.runtime_name,
                        "time_to_first_token_ms": round(ttft_ms, 2) if ttft_ms else None,
                        "duration_ms": round((time.monotonic() - started) * 1000.0, 2),
                        **({"router": router_payload} if router_payload else {}),
                    },
                )
        yield b"data: [DONE]\n\n"
    except InferenceCancelledError as exc:
        yield stream_error(exc)
        yield b"data: [DONE]\n\n"
    except RaconRuntimeError as exc:
        yield stream_error(exc)
        yield b"data: [DONE]\n\n"
    except ModelStorageError as exc:
        yield frame(
            {
                "error": {
                    "message": exc.message,
                    "type": "invalid_request_error",
                    "param": None,
                    "code": exc.code,
                    "details": exc.details,
                }
            }
        )
        yield b"data: [DONE]\n\n"


@router.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, http_request: Request):
    if not request.messages:
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": "messages must contain at least one entry",
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": "invalid_messages",
                }
            },
        )

    # Requests authenticated with a RACoN inference key are served by the network
    # gateway. The local path below remains for the desktop UI and per-install token.
    api_key_id = getattr(http_request.state, "api_key_id", None)
    requested_lower = request.model.strip().lower()
    network_alias = requested_lower in {"racon", "racon-single", "racon-task", "mesh"} or (
        requested_lower.startswith("racon-single:")
        or requested_lower.startswith("racon-task:")
    )
    if api_key_id or network_alias:
        from backend.racon.network.gateway import get_network_gateway

        gateway = get_network_gateway()
        return await gateway.handle_chat(request, api_key_id=api_key_id)

    router_payload: Optional[dict[str, Any]] = None
    if request.model.strip().lower() == "auto":
        decision = await _resolve_auto_route(request)
        router_payload = decision.to_dict()
        if not decision.selected_model:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": decision.reason,
                        "type": "server_error",
                        "param": None,
                        "code": "no_route",
                        "details": decision.to_dict(),
                    }
                },
            )
        request.model = decision.selected_model

    if request.stream:
        return StreamingResponse(
            _sse_stream(request, router_payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    engine = _engine()
    messages = _to_messages(request.messages)
    started = time.monotonic()
    try:
        result = await engine.chat(messages, **_engine_kwargs(request))
    except (RaconRuntimeError, ModelStorageError) as exc:
        return _openai_chat_error(exc)

    message: dict[str, Any] = {"role": "assistant", "content": result.text}
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls
        if not result.text:
            message["content"] = None

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": result.finish_reason or ("tool_calls" if result.tool_calls else "stop"),
            }
        ],
        "usage": result.usage.to_dict(),
        "racon": {
            "runtime": result.runtime,
            "time_to_first_token_ms": result.time_to_first_token_ms,
            "duration_ms": round(result.duration_ms, 2),
            "generation_tokens_per_second": result.generation_tokens_per_second,
            "wall_ms": round((time.monotonic() - started) * 1000.0, 2),
            **({"router": router_payload} if router_payload else {}),
        },
    }


# ---------------------------------------------------------------------------
# Engine management
# ---------------------------------------------------------------------------

@router.get("/api/v1/engine/status")
async def engine_status():
    try:
        return await _engine().status()
    except Exception as exc:  # noqa: BLE001 - status must always answer
        logger.exception("Engine status failed")
        return _error_response(exc)


@router.post("/api/v1/engine/load")
async def engine_load(request: LoadModelRequest):
    try:
        return await _engine().load_model(request.model)
    except (RaconRuntimeError, ModelStorageError) as exc:
        return _error_response(exc)


@router.post("/api/v1/engine/unload")
async def engine_unload():
    try:
        return await _engine().unload_model()
    except RaconRuntimeError as exc:
        return _error_response(exc)


@router.post("/api/v1/engine/cancel")
async def engine_cancel():
    return {"cancelled": await _engine().cancel()}


@router.get("/api/v1/engine/models")
async def engine_models():
    engine = _engine()
    return {
        "installed": [m.to_dict() for m in engine.models.installed()],
        "catalog": [e.to_dict() for e in engine.models.catalog_entries()],
        "models_dir": str(engine.models.models_dir),
        "disk_free_mb": round(engine.models.disk_free_mb(), 1),
    }


@router.post("/api/v1/engine/models/import")
async def engine_import_model(request: ImportModelRequest):
    """Import a local GGUF file into the managed model directory.

    The file must exist on this machine. It is verified for GGUF validity before the model
    is reported as installed; no download is implied.
    """
    from pathlib import Path

    try:
        result = _engine().models.install_from_path(
            Path(request.path).expanduser(),
            model_id=request.model_id,
            copy=request.copy_file,
        )
    except (ModelStorageError, OSError) as exc:
        return _error_response(exc)
    return result.to_dict()


@router.get("/api/v1/engine/models/{model_id}/estimate")
async def engine_estimate(
    model_id: str,
    context_size: int = Query(default=4096, ge=128, le=131072),
    kv_cache_type: str = Query(default="f16"),
):
    try:
        return _engine().models.estimate(model_id, context_size, kv_cache_type)
    except ModelStorageError as exc:
        return _error_response(exc)


@router.delete("/api/v1/engine/models/{model_id}")
async def engine_delete(model_id: str):
    try:
        deleted = _engine().models.delete(model_id)
    except ModelStorageError as exc:
        return _error_response(exc)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' is not installed")
    return {"deleted": model_id}


@router.get("/api/v1/engine/history")
async def engine_history(limit: int = Query(default=50, ge=1, le=500)):
    return {"history": _engine().recent_history(limit)}


@router.get("/api/v1/engine/events")
async def engine_events(limit: int = Query(default=100, ge=1, le=500)):
    return {"events": _engine().recent_events(limit)}


@router.get("/api/v1/engine/usage")
async def engine_usage():
    return _engine().usage_summary()
