"""Public RACoN network gateway.

A request authenticated with a RACoN inference key is routed either to one worker
(single-query) or through planner decomposition across workers (task). Both paths stream
OpenAI-compatible chunks. Workers push deltas to the coordinator over the existing
outbound-only transport.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

from fastapi.responses import JSONResponse, StreamingResponse

from backend.racon.network.catalog import online_workers
from backend.racon.network.intent import IntentDecision, choose_intent
from backend.racon.orchestration.executor import InProcessDispatcher
from backend.racon.orchestration.model_router import (
    ModelProfile,
    infer_capabilities,
    parse_parameter_count,
    route_request,
)
from backend.racon.orchestration.schemas import MAX_INSTRUCTION_CHARS, PlanRequest
from backend.racon.orchestration.service import get_orchestration_service
from racon.runtime.tool_calls import normalise_tool_calls
from racon.runtime.types import ChatMessage, GenerationOptions

logger = logging.getLogger(__name__)

def _env_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


MAX_TARGET_ATTEMPTS = 3
#: A worker that has to start llama-server cold can spend a couple of minutes loading the
#: GGUF before its first token. 90s cut off legitimate cold starts on CPU-only machines.
FIRST_TOKEN_TIMEOUT_SECONDS = _env_positive_float("RACON_FIRST_TOKEN_TIMEOUT", 180.0)
#: Total wall-clock budget across every retry. Without this, three silent workers cost
#: 3 x FIRST_TOKEN_TIMEOUT before the gateway returns an error.
TARGET_BUDGET_SECONDS = _env_positive_float("RACON_TARGET_BUDGET", 240.0)


class GatewayError(Exception):
    def __init__(self, status_code: int, message: str, code: str, error_type: str = "invalid_request_error"):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.error_type = error_type


def _error_response(exc: GatewayError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "type": exc.error_type,
                "param": None,
                "code": exc.code,
            }
        },
    )


@dataclass
class Target:
    kind: str  # "worker" | "local"
    model: str
    worker_id: Optional[str] = None
    node: Any = None


def _message_dict(message: Any) -> dict[str, Any]:
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    if isinstance(message, dict):
        return {key: value for key, value in message.items() if value is not None}
    return {
        "role": str(getattr(message, "role", "user")),
        "content": getattr(message, "content", ""),
    }


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    return ""


def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in messages:
        role = str(message.get("role") or "user").upper()
        text = _content_text(message.get("content"))
        if text:
            lines.append(f"{role}: {text}")
    prompt = "\n\n".join(lines).strip()
    if len(prompt) > MAX_INSTRUCTION_CHARS:
        raise GatewayError(
            400,
            f"The decomposed request is {len(prompt)} characters; the planner limit is "
            f"{MAX_INSTRUCTION_CHARS}. Use model 'racon-single' for very long prompts.",
            "prompt_too_long",
        )
    return prompt


def _options_from_request(request: Any) -> dict[str, Any]:
    return {
        "max_tokens": int(request.max_tokens),
        "temperature": float(request.temperature),
        "top_p": float(request.top_p),
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


def _profile_for_worker_model(name: str) -> ModelProfile:
    return ModelProfile(
        name=name,
        runtime="remote",
        parameter_count_b=parse_parameter_count(name),
        capabilities=infer_capabilities(name),
        source="runtime",
    )


class NetworkGateway:
    def __init__(self) -> None:
        self._dispatcher = InProcessDispatcher()

    # ------------------------------------------------------------------ public

    async def handle_chat(self, request: Any, *, api_key_id: Optional[str] = None):
        messages = [_message_dict(message) for message in request.messages]
        intent = choose_intent(request.model, messages, tools=request.tools)
        try:
            if intent.mode == "task":
                return await self._handle_task(request, messages, intent, api_key_id)
            return await self._handle_single(request, messages, intent, api_key_id)
        except GatewayError as exc:
            return _error_response(exc)

    async def list_models(self) -> list[dict[str, Any]]:
        from backend.racon.network.catalog import network_model_entries

        return network_model_entries()

    # ------------------------------------------------------------------ single

    async def _handle_single(
        self,
        request: Any,
        messages: list[dict[str, Any]],
        intent: IntentDecision,
        api_key_id: Optional[str],
    ):
        targets = await self._select_targets(
            intent.model, messages, tools=request.tools
        )
        if not targets:
            raise GatewayError(
                503,
                "No online worker or local model can serve this request",
                "no_worker",
                "server_error",
            )
        options = _options_from_request(request)
        if request.stream:
            return StreamingResponse(
                self._single_sse(request, messages, options, targets, intent, api_key_id),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        finish_reason: Optional[str] = None
        used_target: Optional[Target] = None
        error: Optional[str] = None
        async for event in self._single_events(messages, options, targets):
            if event.get("event") == "delta":
                delta = event.get("delta") or {}
                if delta.get("content"):
                    text_parts.append(str(delta["content"]))
                if delta.get("tool_calls"):
                    tool_calls.extend(normalise_tool_calls(delta["tool_calls"]))
            elif event.get("event") == "done":
                result = event.get("result_data") or {}
                usage = result.get("usage") or {}
                finish_reason = result.get("finish_reason")
                tool_calls = normalise_tool_calls(result.get("tool_calls")) or tool_calls
                used_target = event.get("target")
            elif event.get("event") == "error":
                error = str(event.get("error") or "worker failed")

        text = "".join(text_parts)
        if not text and not tool_calls:
            raise GatewayError(
                502,
                error or "The worker returned no output",
                "worker_failed",
                "server_error",
            )
        message: dict[str, Any] = {"role": "assistant", "content": text or None}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls" if tool_calls else (finish_reason or "stop"),
                }
            ],
            "usage": usage
            or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "reported": False},
            "racon": {
                "mode": "single",
                "worker": getattr(used_target, "worker_id", None),
                "model": getattr(used_target, "model", None) or intent.model,
                "intent": intent.to_dict(),
                "api_key_id": api_key_id,
            },
        }

    async def _single_events(
        self,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        targets: list[Target],
    ) -> AsyncIterator[dict[str, Any]]:
        emitted = False
        last_error: Optional[str] = None
        deadline = time.monotonic() + TARGET_BUDGET_SECONDS
        for target in targets[:MAX_TARGET_ATTEMPTS]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                last_error = last_error or (
                    f"target budget of {TARGET_BUDGET_SECONDS:.0f}s exhausted before a "
                    "worker produced a token"
                )
                break
            try:
                async for event in self._run_target(
                    target, messages, options, budget_s=remaining
                ):
                    if event.get("event") == "delta":
                        delta = event.get("delta") or {}
                        if delta.get("tool_calls"):
                            delta["tool_calls"] = normalise_tool_calls(delta["tool_calls"])
                        if delta.get("content") or delta.get("tool_calls"):
                            emitted = True
                        event["target"] = target
                        yield event
                    elif event.get("event") == "done":
                        event["target"] = target
                        yield event
                        return
                    elif event.get("event") == "error":
                        last_error = str(event.get("error") or "worker failed")
                        if emitted:
                            event["target"] = target
                            yield event
                            return
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Target %s failed before first token: %s", target, last_error)
                if emitted:
                    yield {"event": "error", "error": last_error, "target": target}
                    return
        yield {
            "event": "error",
            "error": last_error or "all workers failed before producing a token",
        }

    async def _run_target(
        self,
        target: Target,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        *,
        budget_s: Optional[float] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        if target.kind == "local":
            async for event in self._local_events(target.model, messages, options):
                yield event
            return
        if not target.worker_id:
            yield {"event": "error", "error": "worker target has no node id"}
            return
        stream_timeout = float(options.get("timeout_s") or 180.0)
        first_token_timeout = FIRST_TOKEN_TIMEOUT_SECONDS
        if budget_s is not None:
            stream_timeout = min(stream_timeout, max(5.0, budget_s))
            first_token_timeout = min(first_token_timeout, max(5.0, budget_s))
        async for event in self._dispatcher.open_stream(
            worker_id=target.worker_id,
            model_name=target.model,
            messages=messages,
            options=options,
            timeout_s=stream_timeout,
            first_token_timeout_s=first_token_timeout,
        ):
            yield event

    async def _local_events(
        self,
        model: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        from racon.services.engine_service import get_engine

        engine = get_engine()
        chat_messages = [
            ChatMessage(
                role=str(item.get("role") or "user"),
                content=item.get("content"),
                name=item.get("name"),
                tool_calls=item.get("tool_calls"),
                tool_call_id=item.get("tool_call_id"),
            )
            for item in messages
        ]
        kwargs = {key: value for key, value in options.items() if value is not None}
        kwargs["model"] = model
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}
        finish_reason: Optional[str] = None
        started = time.monotonic()
        try:
            async for chunk in engine.chat_stream(chat_messages, **kwargs):
                delta: dict[str, Any] = {}
                if chunk.delta:
                    delta["content"] = chunk.delta
                    text_parts.append(chunk.delta)
                if chunk.tool_calls:
                    normalised = normalise_tool_calls(chunk.tool_calls)
                    delta["tool_calls"] = normalised
                    tool_calls.extend(normalised)
                if delta:
                    yield {"event": "delta", "delta": delta}
                if chunk.usage is not None:
                    usage = chunk.usage.to_dict()
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
        except Exception as exc:
            yield {"event": "error", "error": f"{type(exc).__name__}: {exc}"}
            return
        yield {
            "event": "done",
            "state": "completed",
            "result_data": {
                "response": "".join(text_parts),
                "tool_calls": tool_calls or None,
                "usage": usage,
                "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
                "runtime": engine.runtime_name,
            },
            "execution_time_ms": (time.monotonic() - started) * 1000.0,
        }

    async def _single_sse(
        self,
        request: Any,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        targets: list[Target],
        intent: IntentDecision,
        api_key_id: Optional[str],
    ) -> AsyncIterator[bytes]:
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())
        usage: dict[str, Any] = {}

        def frame(payload: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")

        def chunk(delta: dict[str, Any], finish: Optional[str] = None, racon: Optional[dict[str, Any]] = None) -> bytes:
            payload: dict[str, Any] = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if usage and finish:
                payload["usage"] = usage
            if racon:
                payload["racon"] = racon
            return frame(payload)

        yield chunk({"role": "assistant"})
        async for event in self._single_events(messages, options, targets):
            if event.get("event") == "delta":
                delta = event.get("delta") or {}
                yield chunk(delta)
            elif event.get("event") == "error":
                yield frame(
                    {
                        "error": {
                            "message": str(event.get("error") or "worker failed"),
                            "type": "server_error",
                            "param": None,
                            "code": "worker_failed",
                        }
                    }
                )
                yield b"data: [DONE]\n\n"
                return
            elif event.get("event") == "done":
                result = event.get("result_data") or {}
                usage = result.get("usage") or {}
                target = event.get("target")
                yield chunk(
                    {},
                    finish=(
                        "tool_calls"
                        if result.get("tool_calls")
                        else (result.get("finish_reason") or "stop")
                    ),
                    racon={
                        "mode": "single",
                        "worker": getattr(target, "worker_id", None),
                        "model": getattr(target, "model", None),
                        "intent": intent.to_dict(),
                        "api_key_id": api_key_id,
                    },
                )
                yield b"data: [DONE]\n\n"
                return
        yield b"data: [DONE]\n\n"

    # ------------------------------------------------------------------ task

    async def _handle_task(
        self,
        request: Any,
        messages: list[dict[str, Any]],
        intent: IntentDecision,
        api_key_id: Optional[str],
    ):
        prompt = _messages_to_prompt(messages)
        model = intent.model or await self._select_model(messages, tools=request.tools)
        if not model:
            raise GatewayError(
                503,
                "No model is available to plan or execute this task",
                "no_model",
                "server_error",
            )
        worker_nodes = self._workers_with_model(model)
        planner_model = await self._select_local_model(messages)
        plan_request = PlanRequest(
            prompt=prompt,
            model=model,
            planner_model=planner_model,
            synthesis_model=planner_model,
            max_tokens=min(int(request.max_tokens), 4096),
            decompose=True,
            execution_mode="distributed" if worker_nodes else "auto",
            max_parallel_tasks=4,
            stream=True,
            router={"intent": intent.to_dict()},
        )
        service = get_orchestration_service()
        job, queue, replay = await service.submit_stream(plan_request, name="network-task")
        job_id = str(job.get("id") or "")
        if request.stream:
            return StreamingResponse(
                self._task_sse(request, job_id, queue, replay, intent, api_key_id, service),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        text_parts: list[str] = []
        final: Optional[dict[str, Any]] = None
        for item in replay:
            if item.get("event") == "synthesis_delta":
                text_parts.append(str(item.get("delta") or ""))
            elif item.get("event") == "job_done":
                final = item
                break
        while final is None:
            item = await queue.get()
            if item.get("event") == "synthesis_delta":
                text_parts.append(str(item.get("delta") or ""))
            elif item.get("event") == "job_done":
                final = item
        service.unsubscribe_job_stream(job_id, queue)

        text = (final.get("result") or "".join(text_parts) or "").strip()
        if not text:
            raise GatewayError(
                502,
                final.get("error") or "The task produced no answer",
                "task_failed",
                "server_error",
            )
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "reported": False,
            },
            "racon": {
                "mode": "task",
                "job_id": job_id,
                "status": final.get("status"),
                "missing": final.get("missing") or [],
                "intent": intent.to_dict(),
                "api_key_id": api_key_id,
            },
        }

    async def _task_sse(
        self,
        request: Any,
        job_id: str,
        queue: asyncio.Queue,
        replay: list[dict[str, Any]],
        intent: IntentDecision,
        api_key_id: Optional[str],
        service,
    ) -> AsyncIterator[bytes]:
        completion_id = f"chatcmpl-{uuid.uuid4().hex[:24]}"
        created = int(time.time())

        def frame(payload: dict[str, Any]) -> bytes:
            return f"data: {json.dumps(payload, separators=(',', ':'))}\n\n".encode("utf-8")

        def chunk(delta: dict[str, Any], finish: Optional[str] = None, racon: Optional[dict[str, Any]] = None) -> bytes:
            payload: dict[str, Any] = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
            if racon:
                payload["racon"] = racon
            return frame(payload)

        try:
            yield chunk({"role": "assistant"})
            events: list[dict[str, Any]] = list(replay)
            while True:
                if events:
                    event = events.pop(0)
                else:
                    event = await queue.get()
                event_type = str(event.get("event") or "")
                if event_type == "task_delta":
                    delta = event.get("delta") or {}
                    yield chunk(
                        {},
                        racon={
                            "mode": "task",
                            "job_id": job_id,
                            "event": "task_delta",
                            "task_id": event.get("task_id"),
                            "title": event.get("title"),
                            "text": delta.get("content") or "",
                            "tool_calls": delta.get("tool_calls"),
                        },
                    )
                elif event_type == "synthesis_delta":
                    text = str(event.get("delta") or "")
                    if text:
                        yield chunk({"content": text})
                elif event_type == "job_done":
                    status = str(event.get("status") or "failed")
                    result = event.get("result")
                    if status in ("failed", "cancelled") and not result:
                        yield frame(
                            {
                                "error": {
                                    "message": str(event.get("error") or "Task failed"),
                                    "type": "server_error",
                                    "param": None,
                                    "code": "task_failed",
                                }
                            }
                        )
                        yield b"data: [DONE]\n\n"
                        return
                    yield chunk(
                        {},
                        finish="stop",
                        racon={
                            "mode": "task",
                            "job_id": job_id,
                            "status": status,
                            "missing": event.get("missing") or [],
                            "intent": intent.to_dict(),
                            "api_key_id": api_key_id,
                        },
                    )
                    yield b"data: [DONE]\n\n"
                    return
        finally:
            service.unsubscribe_job_stream(job_id, queue)
            try:
                await service.cancel(job_id)
            except Exception:
                logger.debug("Job cancel after client disconnect failed", exc_info=True)

    # ------------------------------------------------------------------ selection

    async def _local_model_ids(self) -> list[str]:
        try:
            from racon.services.engine_service import get_engine

            engine = get_engine()
            models = [str(model.id) for model in engine.models.installed()]
            try:
                runtime_models = await engine.runtime().list_models()
                models.extend(str(model.id) for model in runtime_models)
            except Exception:
                pass
            return sorted(
                {
                    item
                    for item in models
                    if item and self._servable_model(item) and "embed" not in item.lower()
                }
            )
        except Exception as exc:
            logger.debug("Local model inventory failed: %s", exc)
            return []

    @staticmethod
    def _supports_streaming(node: Any) -> bool:
        return "streaming_chat" in (getattr(node, "features", None) or [])

    @staticmethod
    def _servable_model(name: str) -> bool:
        lowered = name.strip().lower()
        return bool(lowered) and not lowered.endswith(":cloud") and "-cloud" not in lowered

    def _workers_with_model(self, model: str) -> list[Any]:
        wanted = model.strip().lower()
        if not self._servable_model(wanted):
            return []
        matches: list[Any] = []
        for node in online_workers():
            if not self._supports_streaming(node):
                continue
            for name in getattr(node, "models", None) or []:
                if str(name).strip().lower() == wanted and self._servable_model(str(name)):
                    matches.append(node)
                    break
        return matches

    async def _select_targets(
        self,
        requested_model: Optional[str],
        messages: list[dict[str, Any]],
        *,
        tools: Any = None,
    ) -> list[Target]:
        workers = online_workers()
        local_models = await self._local_model_ids()

        if requested_model:
            worker_nodes = self._workers_with_model(requested_model)
            targets = [
                Target(kind="worker", model=requested_model, worker_id=str(node.node_id), node=node)
                for node in self._rank_workers(worker_nodes)
            ]
            if requested_model in local_models:
                targets.append(Target(kind="local", model=requested_model))
            if not targets:
                raise GatewayError(
                    404,
                    f"Model '{requested_model}' is not served by an online worker or this coordinator",
                    "model_not_found",
                )
            return targets

        profiles: list[ModelProfile] = []
        seen: set[str] = set()
        for node in workers:
            if not self._supports_streaming(node):
                continue
            for name in getattr(node, "models", None) or []:
                text = str(name).strip()
                if (
                    text
                    and self._servable_model(text)
                    and text.lower() not in seen
                ):
                    seen.add(text.lower())
                    profiles.append(_profile_for_worker_model(text))
        # A public network request should use the network when it can. Local models are
        # the fallback for a coordinator with no online worker, not a competitor to them.
        if not profiles:
            for name in local_models:
                if name.lower() not in seen:
                    seen.add(name.lower())
                    profiles.append(_profile_for_worker_model(name))
        if not profiles:
            return []

        decision = route_request(
            messages,
            requested_model="auto",
            profiles=profiles,
            tools=tools,
            ram_available_mb=None,
        )
        model = decision.selected_model or profiles[0].name
        targets = [
            Target(kind="worker", model=model, worker_id=str(node.node_id), node=node)
            for node in self._rank_workers(self._workers_with_model(model))
        ]
        if not targets and model in local_models:
            targets.append(Target(kind="local", model=model))
        return targets

    async def _select_model(self, messages: list[dict[str, Any]], *, tools: Any = None) -> Optional[str]:
        targets = await self._select_targets(None, messages, tools=tools)
        return targets[0].model if targets else None

    async def _select_local_model(self, messages: list[dict[str, Any]]) -> Optional[str]:
        """Choose a coordinator-local model for planning and synthesis."""
        model_ids = await self._local_model_ids()
        if not model_ids:
            return None
        profiles = [_profile_for_worker_model(name) for name in model_ids]
        decision = route_request(
            messages,
            requested_model="auto",
            profiles=profiles,
            tools=None,
            ram_available_mb=None,
        )
        return decision.selected_model or model_ids[0]

    @staticmethod
    def _rank_workers(nodes: list[Any]) -> list[Any]:
        def key(node: Any):
            active = len(getattr(node, "active_tasks", None) or [])
            load = float(getattr(node, "cpu_util_pct", 0.0) or 0.0)
            ram = float(
                getattr(node, "ram_available_mb", None)
                or getattr(getattr(node, "capabilities", None), "ram_available_mb", 0.0)
                or 0.0
            )
            return (active, load, -ram, str(getattr(node, "node_id", "")))

        return sorted(nodes, key=key)


_GATEWAY: Optional[NetworkGateway] = None
_GATEWAY_LOCK = threading.Lock()


def get_network_gateway() -> NetworkGateway:
    global _GATEWAY
    if _GATEWAY is None:
        with _GATEWAY_LOCK:
            if _GATEWAY is None:
                _GATEWAY = NetworkGateway()
    return _GATEWAY


def reset_network_gateway() -> None:
    global _GATEWAY
    with _GATEWAY_LOCK:
        _GATEWAY = None
