"""Parallel execution of agent-planned tasks on RACoN workers.

Workers receive a signed task envelope through the existing coordinator transport. Only the
final output of each task returns to this layer; streaming token deltas are consumed here and
are not forwarded to the API client.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable, Optional

from backend.racon.orchestration.executor import DispatchContext
from backend.racon.orchestration.schemas import TaskRequirements, TaskSpec

from .prompts import task_messages
from .schemas import AgentTask, TaskOutcome
from .selection import WorkerTelemetryStore, get_worker_telemetry

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None]]


class AgentTaskRunner:
    def __init__(
        self,
        dispatcher,
        *,
        telemetry: Optional[WorkerTelemetryStore] = None,
        job_id: str = "",
    ) -> None:
        self.dispatcher = dispatcher
        self.telemetry = telemetry or get_worker_telemetry()
        self.job_id = job_id

    async def execute(
        self,
        tasks: list[AgentTask],
        *,
        max_parallel: int,
        on_event: Optional[ProgressCallback] = None,
    ) -> dict[str, TaskOutcome]:
        """Run the DAG in dependency order, parallelising every ready wave."""
        remaining = {task.task_id: task for task in tasks}
        outcomes: dict[str, TaskOutcome] = {}

        while remaining:
            ready: list[AgentTask] = []
            for task in list(remaining.values()):
                if not all(dep in outcomes for dep in task.depends_on):
                    continue
                failed_deps = [
                    dep for dep in task.depends_on if not outcomes[dep].succeeded
                ]
                if failed_deps:
                    outcome = TaskOutcome(
                        task_id=task.task_id,
                        title=task.title,
                        status="failed",
                        worker_id=task.worker_id,
                        model=task.model,
                        error=(
                            "dependency did not produce output: "
                            + ", ".join(failed_deps)
                        ),
                    )
                    outcomes[task.task_id] = outcome
                    remaining.pop(task.task_id, None)
                    if on_event:
                        await on_event(
                            {
                                "event": "task_done",
                                **outcome.to_dict(include_output=False),
                                "output_chars": 0,
                            }
                        )
                    continue
                ready.append(task)

            if not ready:
                for task in remaining.values():
                    blocked = [
                        dep
                        for dep in task.depends_on
                        if dep not in outcomes or not outcomes[dep].succeeded
                    ]
                    outcomes[task.task_id] = TaskOutcome(
                        task_id=task.task_id,
                        title=task.title,
                        status="failed",
                        worker_id=task.worker_id,
                        model=task.model,
                        error=(
                            "dependency did not complete: "
                            + (", ".join(blocked) if blocked else "unknown")
                        ),
                    )
                break

            semaphore = asyncio.Semaphore(max(1, int(max_parallel)))

            async def run_one(task: AgentTask) -> TaskOutcome:
                dependency_outputs = {
                    dep: outcomes[dep].output
                    for dep in task.depends_on
                    if dep in outcomes and outcomes[dep].succeeded
                }
                async with semaphore:
                    outcome = await self._run_task(task, dependency_outputs, on_event)
                    if on_event:
                        await on_event(
                            {
                                "event": "task_done",
                                **outcome.to_dict(include_output=False),
                                "output_chars": len(outcome.output or ""),
                            }
                        )
                    return outcome

            results = await asyncio.gather(*(run_one(task) for task in ready))
            for task, outcome in zip(ready, results):
                outcomes[task.task_id] = outcome
                remaining.pop(task.task_id, None)

        return outcomes

    async def _run_task(
        self,
        task: AgentTask,
        dependency_outputs: dict[str, str],
        on_event: Optional[ProgressCallback],
    ) -> TaskOutcome:
        if not task.worker_id or not task.model:
            return TaskOutcome(
                task_id=task.task_id,
                title=task.title,
                status="failed",
                worker_id=task.worker_id,
                model=task.model,
                error="no worker/model placement was resolved for this task",
            )

        if on_event:
            await on_event(
                {
                    "event": "task_started",
                    "task_id": task.task_id,
                    "title": task.title,
                    "worker_id": task.worker_id,
                    "model": task.model,
                }
            )

        started = time.monotonic()
        first_delta_at: Optional[float] = None
        text_parts: list[str] = []
        usage: dict[str, Any] = {}
        claim_watcher: Optional[asyncio.Task] = None

        try:
            if hasattr(self.dispatcher, "open_stream"):
                async for event in self.dispatcher.open_stream(
                    worker_id=task.worker_id,
                    model_name=task.model,
                    messages=task_messages(
                        task.title, task.instruction, dependency_outputs
                    ),
                    options={
                        "max_tokens": max(1, min(int(task.max_tokens), 2048)),
                        "temperature": 0.4,
                    },
                    timeout_s=task.timeout_s,
                    first_token_timeout_s=min(180.0, task.timeout_s),
                    job_id=self.job_id or None,
                ):
                    event_type = str(event.get("event") or "")
                    if event_type == "started":
                        claim_watcher = asyncio.create_task(
                            self._watch_claim(
                                task.worker_id,
                                str(event.get("task_id") or ""),
                                started,
                            )
                        )
                    elif event_type == "delta":
                        delta = event.get("delta") or {}
                        content = delta.get("content") if isinstance(delta, dict) else None
                        if content:
                            if first_delta_at is None:
                                first_delta_at = time.monotonic()
                                self.telemetry.record_ttft(
                                    task.worker_id, (first_delta_at - started) * 1000.0
                                )
                            text_parts.append(str(content))
                    elif event_type == "error":
                        return TaskOutcome(
                            task_id=task.task_id,
                            title=task.title,
                            status="failed",
                            worker_id=task.worker_id,
                            model=task.model,
                            error=str(event.get("error") or "worker stream failed"),
                            duration_ms=(time.monotonic() - started) * 1000.0,
                            ttft_ms=(
                                (first_delta_at - started) * 1000.0
                                if first_delta_at
                                else None
                            ),
                        )
                    elif event_type == "done":
                        return self._from_done(
                            task, event, text_parts, usage, started, first_delta_at
                        )
            else:
                execution = await self.dispatcher.dispatch(
                    self._task_spec(task),
                    DispatchContext(
                        job_id=self.job_id,
                        model=task.model,
                        max_tokens=task.max_tokens,
                    ),
                    dependency_outputs,
                    task.worker_id,
                )
                return TaskOutcome(
                    task_id=task.task_id,
                    title=task.title,
                    status="completed" if execution.succeeded else "failed",
                    worker_id=task.worker_id,
                    model=task.model,
                    output=execution.output or "",
                    error=execution.error,
                    duration_ms=execution.duration_ms,
                    usage=execution.usage or {},
                )

            return TaskOutcome(
                task_id=task.task_id,
                title=task.title,
                status="failed",
                worker_id=task.worker_id,
                model=task.model,
                output="".join(text_parts),
                error="worker stream ended without a final result",
                duration_ms=(time.monotonic() - started) * 1000.0,
                ttft_ms=(
                    (first_delta_at - started) * 1000.0 if first_delta_at else None
                ),
            )
        except asyncio.TimeoutError:
            return TaskOutcome(
                task_id=task.task_id,
                title=task.title,
                status="failed",
                worker_id=task.worker_id,
                model=task.model,
                error=f"task timed out after {task.timeout_s:.0f}s",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("Agent task %s failed", task.task_id)
            return TaskOutcome(
                task_id=task.task_id,
                title=task.title,
                status="failed",
                worker_id=task.worker_id,
                model=task.model,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.monotonic() - started) * 1000.0,
            )
        finally:
            if claim_watcher is not None:
                claim_watcher.cancel()

    def _from_done(
        self,
        task: AgentTask,
        event: dict[str, Any],
        text_parts: list[str],
        usage: dict[str, Any],
        started: float,
        first_delta_at: Optional[float],
    ) -> TaskOutcome:
        duration_ms = float(event.get("execution_time_ms") or 0.0) or (
            (time.monotonic() - started) * 1000.0
        )
        state = str(event.get("state") or "failed")
        result = event.get("result_data") or {}
        if isinstance(result, dict):
            usage = result.get("usage") or usage
        text = (
            str(
                (result.get("response") if isinstance(result, dict) else "")
                or (result.get("text") if isinstance(result, dict) else "")
                or "".join(text_parts)
            ).strip()
        )
        if state != "completed":
            return TaskOutcome(
                task_id=task.task_id,
                title=task.title,
                status="cancelled" if state == "cancelled" else "failed",
                worker_id=task.worker_id,
                model=task.model,
                output=text,
                error=str(event.get("error") or f"worker reported {state}"),
                duration_ms=duration_ms,
                ttft_ms=(
                    (first_delta_at - started) * 1000.0 if first_delta_at else None
                ),
                usage=usage,
            )
        if not text:
            return TaskOutcome(
                task_id=task.task_id,
                title=task.title,
                status="failed",
                worker_id=task.worker_id,
                model=task.model,
                error="worker returned an empty output",
                duration_ms=duration_ms,
                usage=usage,
            )

        completion_tokens = 0
        try:
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            pass
        if completion_tokens > 0 and duration_ms > 0:
            self.telemetry.record_throughput(
                task.worker_id, completion_tokens / (duration_ms / 1000.0)
            )
        return TaskOutcome(
            task_id=task.task_id,
            title=task.title,
            status="completed",
            worker_id=task.worker_id,
            model=task.model,
            output=text,
            duration_ms=duration_ms,
            ttft_ms=(first_delta_at - started) * 1000.0 if first_delta_at else None,
            claim_ms=self.telemetry.claim_ms(task.worker_id),
            usage=usage,
        )

    async def _watch_claim(
        self, worker_id: str, task_id: str, started: float
    ) -> None:
        """Measure how long a worker takes to claim the signed task envelope."""
        if not task_id:
            return
        state = getattr(self.dispatcher, "state", None)
        if state is None:
            return
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            try:
                entry = state.tasks.get_task(task_id)
            except Exception:
                return
            if entry and str(entry.get("state")) in (
                "running",
                "completed",
                "failed",
                "cancelled",
            ):
                self.telemetry.record_claim(
                    worker_id, (time.monotonic() - started) * 1000.0
                )
                return
            await asyncio.sleep(0.05)

    @staticmethod
    def _task_spec(task: AgentTask) -> TaskSpec:
        return TaskSpec(
            task_id=task.task_id,
            title=task.title[:120],
            instruction=task.instruction[:6000],
            depends_on=list(task.depends_on),
            requirements=TaskRequirements(model=task.model),
            max_tokens=max(1, min(int(task.max_tokens), 4096)),
            timeout_s=task.timeout_s,
            retries=0,
        )
