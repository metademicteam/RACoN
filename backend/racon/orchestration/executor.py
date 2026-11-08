"""Task dispatchers.

Two real execution paths exist:

* ``LocalDispatcher`` runs the task on the machine that hosts the server, through the
  runtime adapter layer.
* ``CoordinatorDispatcher`` submits an allow-listed ``text_completion`` task to a remote
  worker through the coordinator admin API and polls for its terminal state.

Both return a ``TaskExecution``. A dispatcher never fabricates output: transport failures,
worker errors, empty results, and timeouts are reported as failed executions.
"""
from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from backend.racon.orchestration.schemas import MAX_CONTEXT_CHARS, MAX_OUTPUT_CHARS, TaskSpec
from backend.racon.orchestration.selector import (
    DISTRIBUTED_ORIGIN,
    LOCAL_ORIGIN,
    WorkerTelemetry,
)
from racon.runtime.types import ChatMessage, RaconRuntimeError

logger = logging.getLogger(__name__)

TERMINAL_REMOTE_STATES = frozenset({"completed", "failed", "cancelled"})

TASK_WORKER_SYSTEM_PROMPT = """You are a task worker in the RACoN distributed system.
Execute only the task you are given. Use the supplied dependency outputs as input.
Be concise and factual. If the task cannot be completed with the information available,
say exactly what is missing instead of inventing it."""


@dataclass(frozen=True)
class DispatchContext:
    job_id: str
    model: str
    max_tokens: int = 512


@dataclass(frozen=True)
class TaskExecution:
    task_id: str
    status: str
    worker_id: Optional[str]
    output: Optional[str] = None
    error: Optional[str] = None
    duration_ms: float = 0.0
    runtime: Optional[str] = None
    truncated: bool = False
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and bool((self.output or "").strip())


class TaskDispatcher(ABC):
    """Execution backend for a single task."""

    @abstractmethod
    async def list_workers(self) -> list[WorkerTelemetry]:
        """Return currently reachable workers with measured telemetry."""

    @abstractmethod
    async def dispatch(
        self,
        task: TaskSpec,
        context: DispatchContext,
        dependency_outputs: dict[str, str],
        worker_id: Optional[str],
    ) -> TaskExecution:
        """Execute one task and return its structured outcome."""

    async def cancel(self, task_id: str) -> None:
        """Best-effort cancellation hook. Dispatchers may override."""
        return None


class LocalDispatcher(TaskDispatcher):
    """Execute tasks on the local runtime adapter."""

    def __init__(self, engine_factory=None) -> None:
        self._engine_factory = engine_factory or _default_engine_factory

    async def list_workers(self) -> list[WorkerTelemetry]:
        ram_total: Optional[float] = None
        ram_available: Optional[float] = None
        workload: Optional[float] = None
        try:
            import psutil

            memory = psutil.virtual_memory()
            ram_total = float(memory.total) / (1024 * 1024)
            ram_available = float(memory.available) / (1024 * 1024)
            workload = float(psutil.cpu_percent(interval=None))
        except Exception as exc:
            logger.debug("Local telemetry unavailable: %s", exc)

        return [
            WorkerTelemetry(
                worker_id=LOCAL_ORIGIN,
                origin=LOCAL_ORIGIN,
                status="available",
                capabilities=frozenset({"text_generation"}),
                ram_total_mb=ram_total,
                ram_available_mb=ram_available,
                workload_pct=workload,
            )
        ]

    async def dispatch(
        self,
        task: TaskSpec,
        context: DispatchContext,
        dependency_outputs: dict[str, str],
        worker_id: Optional[str],
    ) -> TaskExecution:
        started = time.monotonic()
        messages = [
            ChatMessage(role="system", content=TASK_WORKER_SYSTEM_PROMPT),
            ChatMessage(role="user", content=_task_prompt(task, dependency_outputs)),
        ]
        try:
            engine = self._engine_factory()
            result = await engine.chat(
                messages,
                model=context.model,
                max_tokens=max(1, min(task.max_tokens, context.max_tokens, 4096)),
            )
        except RaconRuntimeError as exc:
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=worker_id or LOCAL_ORIGIN,
                error=f"{exc.code}: {exc.message}",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except Exception as exc:
            logger.exception("Local task %s failed", task.task_id)
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=worker_id or LOCAL_ORIGIN,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )

        text = (result.text or "").strip()
        if not text:
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=worker_id or LOCAL_ORIGIN,
                error="runtime returned an empty completion",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )

        truncated = len(text) > MAX_OUTPUT_CHARS
        return TaskExecution(
            task_id=task.task_id,
            status="completed",
            worker_id=worker_id or LOCAL_ORIGIN,
            output=text[:MAX_OUTPUT_CHARS],
            duration_ms=(time.monotonic() - started) * 1000.0,
            runtime=getattr(result, "runtime", None),
            truncated=truncated,
            usage={
                "prompt_tokens": getattr(result.usage, "prompt_tokens", None),
                "completion_tokens": getattr(result.usage, "completion_tokens", None),
                "reported": getattr(result.usage, "reported", False),
            },
        )


class CoordinatorDispatcher(TaskDispatcher):
    """Submit tasks to remote workers through the coordinator admin API."""

    def __init__(
        self,
        base_url: str,
        *,
        client_factory=None,
        poll_interval_s: float = 0.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client_factory = client_factory or _default_http_client
        self._poll_interval_s = poll_interval_s

    async def list_workers(self) -> list[WorkerTelemetry]:
        import httpx

        try:
            async with self._client_factory(timeout=httpx.Timeout(3.0)) as client:
                response = await client.get(f"{self.base_url}/admin/nodes")
                response.raise_for_status()
                nodes = response.json()
        except Exception as exc:
            logger.info("Coordinator at %s is not reachable: %s", self.base_url, exc)
            return []

        workers: list[WorkerTelemetry] = []
        for node in nodes if isinstance(nodes, list) else []:
            if not isinstance(node, dict):
                continue
            if str(node.get("trust_state", "")) != "approved":
                continue
            caps = node.get("capabilities") or {}
            runtimes = caps.get("runtimes") if isinstance(caps, dict) else []
            capabilities = {"text_generation"}
            if isinstance(runtimes, list):
                capabilities.update(str(item) for item in runtimes if item)
            features = node.get("features")
            if isinstance(features, list):
                capabilities.update(str(item) for item in features if item)
            if "ollama" not in capabilities and "local" not in capabilities:
                continue

            def _number(key: str, source: dict = node) -> Optional[float]:
                value = source.get(key)
                try:
                    return float(value) if value is not None else None
                except (TypeError, ValueError):
                    return None

            workers.append(
                WorkerTelemetry(
                    worker_id=str(node.get("node_id") or ""),
                    origin=DISTRIBUTED_ORIGIN,
                    status="available",
                    capabilities=frozenset(capabilities),
                    models=frozenset(
                        str(item) for item in (node.get("models") or []) if item
                    ),
                    ram_total_mb=_number("ram_total_mb", caps) if isinstance(caps, dict) else None,
                    ram_available_mb=_number("ram_available_mb"),
                    gpu_vram_mb=_number("gpu_vram_mb", caps) if isinstance(caps, dict) else None,
                    workload_pct=_number("cpu_util_pct"),
                    latency_ms=None,
                    success_rate=None,
                    active_tasks=len(node.get("active_tasks") or []),
                )
            )
        return workers

    async def dispatch(
        self,
        task: TaskSpec,
        context: DispatchContext,
        dependency_outputs: dict[str, str],
        worker_id: Optional[str],
    ) -> TaskExecution:
        import httpx

        if not worker_id:
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=None,
                error="no worker id supplied for distributed dispatch",
            )

        started = time.monotonic()
        payload = {
            "node_id": worker_id,
            "task_type": "text_completion",
            "model_name": task.requirements.model or context.model,
            "parameters": {
                "prompt": _task_prompt(task, dependency_outputs),
                "max_tokens": max(1, min(task.max_tokens, context.max_tokens, 4096)),
                "job_id": context.job_id,
            },
        }

        remote_task_id: Optional[str] = None
        try:
            async with self._client_factory(timeout=httpx.Timeout(15.0)) as client:
                response = await client.post(f"{self.base_url}/admin/tasks", json=payload)
                response.raise_for_status()
                created = response.json()
                remote_task_id = created.get("task_id")
                if not remote_task_id:
                    raise RuntimeError("coordinator did not return a task id")

                deadline = time.monotonic() + task.timeout_s
                last_state = "assigned"
                while time.monotonic() < deadline:
                    await asyncio.sleep(self._poll_interval_s)
                    listing = await client.get(f"{self.base_url}/admin/tasks")
                    listing.raise_for_status()
                    for entry in listing.json():
                        if entry.get("task_id") != remote_task_id:
                            continue
                        last_state = str(entry.get("state", "assigned"))
                        if last_state in TERMINAL_REMOTE_STATES:
                            return _from_remote(
                                task, worker_id, entry, (time.monotonic() - started) * 1000.0
                            )
                return TaskExecution(
                    task_id=task.task_id,
                    status="failed",
                    worker_id=worker_id,
                    error=f"remote task timed out after {task.timeout_s:.0f}s (last state: {last_state})",
                    duration_ms=(time.monotonic() - started) * 1000.0,
                )
        except httpx.HTTPError as exc:
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=worker_id,
                error=f"coordinator transport error: {exc}",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except Exception as exc:
            logger.exception("Distributed task %s failed", task.task_id)
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=worker_id,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )

    async def cancel(self, task_id: str) -> None:
        # The coordinator exposes no cancel route yet (see docs/DEVELOPMENT_ROADMAP.md P3).
        return None


class InProcessDispatcher(TaskDispatcher):
    """Dispatch tasks through the coordinator state in this same process.

    The unified server (Docker container / headless host) owns the node registry and the
    task manager. Talking to a hypothetical second admin listener on port 8766 is wrong
    there, so the scheduler creates a signed task envelope directly and polls its state.

    The worker still receives the task through ``POST /tasks/claim`` and reports through
    ``POST /tasks/report``; only the operator-side hop is in-process.
    """

    def __init__(self, state: Optional[Any] = None, poll_interval_s: float = 0.4) -> None:
        self._state = state
        self._poll_interval_s = poll_interval_s

    @property
    def state(self):
        if self._state is None:
            from backend.racon.api.coordinator_state import get_coordinator_state

            self._state = get_coordinator_state()
        return self._state

    async def list_workers(self) -> list[WorkerTelemetry]:
        from protocols import TrustState

        import time

        workers: list[WorkerTelemetry] = []
        now = time.time()
        for node in self.state.registry.list_nodes():
            if node.trust_state != TrustState.APPROVED:
                continue
            caps = node.capabilities
            capabilities = {"text_generation"}
            for runtime in caps.runtimes or []:
                if runtime:
                    capabilities.add(str(runtime))
            for feature in node.features or []:
                if feature:
                    capabilities.add(str(feature))
            heartbeat_age = max(0.0, now - float(node.last_heartbeat or 0.0))
            workers.append(
                WorkerTelemetry(
                    worker_id=node.node_id,
                    origin=DISTRIBUTED_ORIGIN,
                    # A worker that has missed the 30s heartbeat window is not a valid
                    # placement target even if its trust state is still APPROVED.
                    status="available" if heartbeat_age <= 30.0 else "unavailable",
                    capabilities=frozenset(capabilities),
                    models=frozenset(str(item) for item in (node.models or []) if item),
                    ram_total_mb=float(caps.ram_total_mb or 0.0) or None,
                    ram_available_mb=(
                        float(node.ram_available_mb or caps.ram_available_mb or 0.0) or None
                    ),
                    gpu_vram_mb=caps.gpu_vram_mb,
                    workload_pct=float(node.cpu_util_pct or 0.0),
                    latency_ms=None,
                    success_rate=None,
                    active_tasks=len(node.active_tasks or []),
                )
            )
        return workers

    async def open_stream(
        self,
        *,
        worker_id: str,
        model_name: str,
        messages: list[dict[str, Any]],
        options: dict[str, Any],
        timeout_s: float = 600.0,
        first_token_timeout_s: float = 90.0,
        job_id: Optional[str] = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Dispatch a streaming chat task and yield worker deltas as they arrive."""
        from protocols import TaskType

        from backend.racon.network.broker import get_delta_broker, task_channel

        envelope = self.state.tasks.create_task(
            node_id=worker_id,
            task_type=TaskType.CHAT_COMPLETION,
            model_name=model_name,
            parameters={
                "messages": messages,
                "options": options,
                "stream": True,
                "timeout_s": timeout_s,
                **({"job_id": job_id} if job_id else {}),
            },
            expiry_seconds=max(120.0, min(timeout_s + 60.0, 3600.0)),
        )
        task_id = envelope.task_id
        channel = task_channel(task_id)
        queue, replay = await get_delta_broker().subscribe(channel)
        started = time.monotonic()
        received_token = False
        try:
            yield {"event": "started", "task_id": task_id, "node_id": worker_id}
            for item in replay:
                delta = item.get("delta") or {}
                if item.get("event") == "delta" and (delta.get("content") or delta.get("tool_calls")):
                    received_token = True
                yield item
                if item.get("event") == "done":
                    return
            while True:
                elapsed = time.monotonic() - started
                remaining = timeout_s + 5.0 - elapsed
                if remaining <= 0:
                    yield {
                        "event": "error",
                        "task_id": task_id,
                        "error": f"worker stream timed out after {timeout_s:.0f}s",
                    }
                    return
                if not received_token and elapsed >= first_token_timeout_s:
                    yield {
                        "event": "error",
                        "task_id": task_id,
                        "error": (
                            f"worker did not produce a token within "
                            f"{first_token_timeout_s:.0f}s"
                        ),
                    }
                    return
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=min(0.5, remaining))
                except asyncio.TimeoutError:
                    entry = self.state.tasks.get_task(task_id)
                    if entry:
                        if time.time() > float(entry.get("expiry") or 0.0) and str(
                            entry.get("state")
                        ) in ("assigned", "queued"):
                            yield {
                                "event": "error",
                                "task_id": task_id,
                                "error": "no worker claimed the task before it expired",
                            }
                            return
                        if str(entry.get("state")) in TERMINAL_REMOTE_STATES:
                            yield {
                                "event": "done",
                                "task_id": task_id,
                                "state": entry.get("state"),
                                "result_data": entry.get("result_data"),
                                "error": entry.get("error"),
                                "execution_time_ms": entry.get("duration_ms", 0.0),
                            }
                            return
                    continue
                delta = item.get("delta") or {}
                if item.get("event") == "delta" and (delta.get("content") or delta.get("tool_calls")):
                    received_token = True
                yield item
                if item.get("event") == "done":
                    return
        finally:
            get_delta_broker().unsubscribe(channel, queue)

    async def dispatch(
        self,
        task: TaskSpec,
        context: DispatchContext,
        dependency_outputs: dict[str, str],
        worker_id: Optional[str],
    ) -> TaskExecution:
        from protocols import TaskType

        if not worker_id:
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=None,
                error="no worker id supplied for distributed dispatch",
            )

        started = time.monotonic()
        model_name = task.requirements.model or context.model
        try:
            envelope = self.state.tasks.create_task(
                node_id=worker_id,
                task_type=TaskType.TEXT_COMPLETION,
                model_name=model_name,
                parameters={
                    "prompt": _task_prompt(task, dependency_outputs),
                    "max_tokens": max(1, min(task.max_tokens, context.max_tokens, 4096)),
                    "job_id": context.job_id,
                },
            )
        except Exception as exc:
            logger.exception("Could not create task for %s", worker_id)
            return TaskExecution(
                task_id=task.task_id,
                status="failed",
                worker_id=worker_id,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )

        try:
            from backend.racon.storage.db import get_database

            get_database().log_routing_event(
                "job_task_dispatched",
                source="coordinator",
                target=worker_id,
                job_id=context.job_id,
                task_id=task.task_id,
                state="assigned",
                message=f"Scheduler assigned '{task.title}' to {worker_id}",
                detail={"model": model_name, "max_tokens": task.max_tokens},
            )
        except Exception as exc:
            logger.debug("Routing log failed for dispatch: %s", exc)

        deadline = time.monotonic() + task.timeout_s
        last_state = "assigned"
        while time.monotonic() < deadline:
            await asyncio.sleep(self._poll_interval_s)
            entry = self.state.tasks.get_task(envelope.task_id)
            if not entry:
                continue
            last_state = str(entry.get("state", "assigned"))
            if last_state in TERMINAL_REMOTE_STATES:
                execution = _from_remote(
                    task, worker_id, entry, (time.monotonic() - started) * 1000.0
                )
                try:
                    from backend.racon.storage.db import get_database

                    get_database().log_routing_event(
                        "job_task_result",
                        source=worker_id,
                        target="coordinator",
                        job_id=context.job_id,
                        task_id=task.task_id,
                        state=execution.status,
                        message=(
                            f"{worker_id} returned {execution.status} for '{task.title}' "
                            f"({execution.duration_ms:.0f} ms)"
                        ),
                        detail={"error": execution.error},
                    )
                except Exception:
                    pass
                return execution

        return TaskExecution(
            task_id=task.task_id,
            status="failed",
            worker_id=worker_id,
            error=(
                f"remote task timed out after {task.timeout_s:.0f}s "
                f"(last state: {last_state})"
            ),
            duration_ms=(time.monotonic() - started) * 1000.0,
        )


def _from_remote(
    task: TaskSpec,
    worker_id: str,
    entry: dict[str, Any],
    duration_ms: float,
) -> TaskExecution:
    state = str(entry.get("state", "failed"))
    if state != "completed":
        return TaskExecution(
            task_id=task.task_id,
            status="cancelled" if state == "cancelled" else "failed",
            worker_id=worker_id,
            error=str(entry.get("error") or f"remote worker reported state '{state}'"),
            duration_ms=duration_ms,
        )

    result = entry.get("result_data") or {}
    text = ""
    if isinstance(result, dict):
        text = str(result.get("response") or result.get("text") or "").strip()
    if not text:
        return TaskExecution(
            task_id=task.task_id,
            status="failed",
            worker_id=worker_id,
            error="remote worker returned an empty result",
            duration_ms=duration_ms,
        )

    return TaskExecution(
        task_id=task.task_id,
        status="completed",
        worker_id=worker_id,
        output=text[:MAX_OUTPUT_CHARS],
        duration_ms=duration_ms,
        runtime=str(result.get("runtime") or "remote"),
        truncated=len(text) > MAX_OUTPUT_CHARS,
        usage={
            "prompt_tokens": result.get("prompt_eval_count"),
            "completion_tokens": result.get("eval_count"),
            "reported": result.get("prompt_eval_count") is not None,
        },
    )


def _task_prompt(task: TaskSpec, dependency_outputs: dict[str, str]) -> str:
    parts = [f"Task: {task.title}", "", task.instruction]
    if dependency_outputs:
        parts.append("")
        parts.append("Completed dependency outputs:")
        budget = MAX_CONTEXT_CHARS
        for dep_id, output in dependency_outputs.items():
            if budget <= 0:
                break
            snippet = output[: min(4000, budget)]
            budget -= len(snippet)
            parts.append(f"--- {dep_id} ---")
            parts.append(snippet)
    return "\n".join(parts)[: MAX_CONTEXT_CHARS + len(task.instruction) + 200]


def _default_engine_factory():
    from racon.services.engine_service import get_engine

    return get_engine()


def _default_http_client(timeout=None):
    import httpx

    return httpx.AsyncClient(timeout=timeout)
