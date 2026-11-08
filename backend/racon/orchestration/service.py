"""Job lifecycle orchestration.

One submitted request becomes a persisted job with a validated task DAG. Independent tasks
run concurrently; dependent tasks wait for their inputs. Every transition is written to the
database and to an append-only event stream that the UI can follow over SSE.

Failure policy:

* A task is retried up to ``task.retries`` times, preferring a different worker.
* A task whose dependency failed is marked failed with the blocking dependency named.
* A job is ``completed`` only if every task completed and synthesis produced an answer.
* A job is ``partial`` when an answer exists but some work could not be completed, or when
  tasks completed and synthesis was unavailable.
* A job is ``failed`` when no usable output exists.
* A job interrupted by process exit is marked failed on the next start. RACoN never claims
  to resume work it cannot prove completed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from backend.racon.orchestration.dag import blocked_by, ready_tasks, validate_plan
from backend.racon.orchestration.executor import (
    TASK_WORKER_SYSTEM_PROMPT,
    CoordinatorDispatcher,
    DispatchContext,
    LocalDispatcher,
    TaskDispatcher,
    TaskExecution,
    _task_prompt,
)
from backend.racon.orchestration.planner import Planner
from backend.racon.orchestration.schemas import MAX_OUTPUT_CHARS, PlanRequest, PlanSpec
from backend.racon.orchestration.selector import (
    LOCAL_ORIGIN,
    SelectionWeights,
    WorkerTelemetry,
    select_worker,
)
from backend.racon.orchestration.synthesizer import SynthesisResult, Synthesizer
from backend.racon.network.broker import get_delta_broker, job_channel
from backend.racon.storage.db import Database, get_database, utc_now

logger = logging.getLogger(__name__)


@dataclass
class OrchestrationConfig:
    coordinator_admin_url: str = "http://127.0.0.1:8766"
    weights: SelectionWeights = field(default_factory=SelectionWeights)
    max_job_seconds: float = 1800.0
    dispatch_grace_seconds: float = 5.0
    #: When a validated plan contains exactly one task, that task already answers the
    #: user's request. Re-running the synthesizer duplicates the entire generation, which
    #: is the single largest avoidable cost on CPU hosts. Disabled by default so embedders
    #: and tests keep the explicit two-stage pipeline; the product service enables it.
    reuse_single_task_output: bool = False


class OrchestrationService:
    """Owns job submission, execution, synthesis, cancellation, and recovery."""

    def __init__(
        self,
        *,
        planner: Optional[Planner] = None,
        local_dispatcher: Optional[TaskDispatcher] = None,
        coordinator_dispatcher: Optional[TaskDispatcher] = None,
        synthesizer: Optional[Synthesizer] = None,
        database: Optional[Database] = None,
        config: Optional[OrchestrationConfig] = None,
    ) -> None:
        self.db = database or get_database()
        self.planner = planner or Planner()
        self.local_dispatcher = local_dispatcher or LocalDispatcher()
        self.config = config or OrchestrationConfig()
        self.coordinator_dispatcher = coordinator_dispatcher
        self.synthesizer = synthesizer or Synthesizer()
        self._running: dict[str, asyncio.Task] = {}
        self._synthesis_subscribers: dict[str, set[asyncio.Queue[str]]] = {}

    # ------------------------------------------------------------------ public API

    def _create_job(self, request: PlanRequest, name: Optional[str] = None) -> str:
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        job_name = (name or f"{request.prompt[:48].strip()}...")[:160]
        self.db.create_orchestration_job(
            {
                "id": job_id,
                "name": job_name,
                "prompt": request.prompt,
                "model_name": request.model,
                "status": "queued",
                "requested_mode": request.execution_mode,
                "max_tokens": request.max_tokens,
                "max_parallel_tasks": request.max_parallel_tasks,
            }
        )
        self._event(job_id, "job_queued", "Job accepted and queued.")
        try:
            self.db.log_routing_event(
                "job_submitted",
                source="client",
                target="coordinator",
                job_id=job_id,
                state="queued",
                message=f"New job from client: {job_name}",
                detail={"model": request.model, "mode": request.execution_mode},
            )
        except Exception:
            pass
        return job_id

    def _schedule_job(self, job_id: str, request: PlanRequest) -> None:
        runner = asyncio.create_task(self._run_job(job_id, request), name=f"racon-job-{job_id}")
        self._running[job_id] = runner
        runner.add_done_callback(lambda _task, jid=job_id: self._running.pop(jid, None))

    async def submit(self, request: PlanRequest, name: Optional[str] = None) -> dict[str, Any]:
        job_id = self._create_job(request, name)
        self._schedule_job(job_id, request)
        return self.get_job(job_id) or {"id": job_id, "status": "queued"}

    async def submit_stream(
        self, request: PlanRequest, name: Optional[str] = None
    ) -> tuple[dict[str, Any], asyncio.Queue, list[dict[str, Any]]]:
        """Submit a job and subscribe to its deltas before the runner starts."""
        job_id = self._create_job(request, name)
        channel = job_channel(job_id)
        queue, replay = await get_delta_broker().subscribe(channel)
        self._schedule_job(job_id, request)
        job = self.get_job(job_id) or {"id": job_id, "status": "queued"}
        return job, queue, replay

    async def subscribe_job_stream(self, job_id: str) -> tuple[asyncio.Queue, list[dict[str, Any]]]:
        return await get_delta_broker().subscribe(job_channel(job_id))

    def unsubscribe_job_stream(self, job_id: str, queue: asyncio.Queue) -> None:
        get_delta_broker().unsubscribe(job_channel(job_id), queue)

    def _publish_job(self, job_id: str, payload: dict[str, Any]) -> None:
        get_delta_broker().publish(job_channel(job_id), payload)

    async def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_orchestration_job(job_id, include_tasks=False)
        if not job:
            return {"status": "not_found", "job_id": job_id}
        if job["status"] in ("completed", "partial", "failed", "cancelled"):
            return {"status": "unchanged", "job_id": job_id, "job_status": job["status"]}

        runner = self._running.get(job_id)
        if runner and not runner.done():
            runner.cancel()
            try:
                await runner
            except asyncio.CancelledError:
                pass
        else:
            self._finalise_cancelled(job_id)
        return {"status": "cancelled", "job_id": job_id}

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        return self.db.get_orchestration_job(job_id)

    def list_jobs(self, limit: int = 50, offset: int = 0, status: Optional[str] = None) -> list[dict]:
        return self.db.list_orchestration_jobs(limit=limit, offset=offset, status=status)

    def events(self, job_id: str, after_id: int = 0, limit: int = 500) -> list[dict]:
        return self.db.list_job_events(job_id, limit=limit, after_id=after_id)

    def subscribe_synthesis(self, job_id: str) -> asyncio.Queue[str]:
        """Subscribe to synthesis token deltas for one job (SSE consumers)."""
        queue: asyncio.Queue[str] = asyncio.Queue()
        self._synthesis_subscribers.setdefault(job_id, set()).add(queue)
        return queue

    def unsubscribe_synthesis(self, job_id: str, queue: asyncio.Queue[str]) -> None:
        subscribers = self._synthesis_subscribers.get(job_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if not subscribers:
            self._synthesis_subscribers.pop(job_id, None)

    def _publish_synthesis(self, job_id: str, delta: str) -> None:
        for queue in list(self._synthesis_subscribers.get(job_id, ())):
            try:
                queue.put_nowait(delta)
            except asyncio.QueueFull:  # pragma: no cover - queues are unbounded
                logger.debug("Dropping synthesis delta for a slow subscriber of %s", job_id)

    def recover_interrupted(self) -> int:
        """Fail jobs left non-terminal by a previous process. Called at application start."""
        return self.db.mark_interrupted_jobs()

    # ------------------------------------------------------------------ execution

    async def _run_job(self, job_id: str, request: PlanRequest) -> None:
        started = time.monotonic()
        try:
            self._set_job_status(job_id, "planning", started_at=utc_now())
            self._event(job_id, "job_planning", "Planner is decomposing the request.")

            plan = await self.planner.plan(request)
            validate_plan(plan)

            # The plan schema deliberately lets the planner choose the work, not the model.
            # Pin every task to the job's model so a GGUF task is never dispatched to a
            # worker that only advertises Ollama tags (or no model at all).
            job_model = (request.model or "").strip()
            if job_model and job_model.lower() != "auto":
                for task in plan.tasks:
                    if not task.requirements.model:
                        task.requirements.model = job_model

            # A single-task plan is the answer itself, so it may use the job's full
            # output budget. A multi-task plan distributes the budget across tasks; the
            # synthesizer keeps the larger merge budget.
            if len(plan.tasks) == 1:
                plan.tasks[0].max_tokens = max(1, min(int(request.max_tokens), 4096))
            else:
                per_task_budget = max(
                    96, min(512, int(request.max_tokens) // max(1, len(plan.tasks)))
                )
                for task in plan.tasks:
                    task.max_tokens = min(task.max_tokens, per_task_budget)

            plan_payload = plan.model_dump()
            if request.router:
                plan_payload["router"] = request.router
            self.db.update_orchestration_job(
                job_id,
                {
                    "planner_mode": plan.planner_mode,
                    "plan_json": json.dumps(plan_payload, ensure_ascii=False),
                    "tasks_total": len(plan.tasks),
                },
            )
            for task in plan.tasks:
                self.db.upsert_orchestration_task(
                    job_id,
                    {
                        "task_id": task.task_id,
                        "title": task.title,
                        "instruction": task.instruction,
                        "depends_on_json": json.dumps(task.depends_on),
                        "state": "pending",
                        "max_tokens": task.max_tokens,
                        "timeout_s": task.timeout_s,
                        "retries": task.retries,
                    },
                )
            self._event(
                job_id,
                "job_planned",
                f"Plan ready: {len(plan.tasks)} task(s), planner={plan.planner_mode}.",
                detail={
                    "tasks": [task.task_id for task in plan.tasks],
                    "planner_note": plan.planner_note,
                },
            )

            workers, execution_mode = await self._resolve_workers(
                request.execution_mode, require_streaming=request.stream
            )
            self.db.update_orchestration_job(job_id, {"execution_mode": execution_mode})
            if not workers:
                raise OrchestrationError(
                    "No worker is available for this job. "
                    + (
                        "The distributed coordinator has no approved workers."
                        if request.execution_mode == "distributed"
                        else "Start a local runtime or register a worker."
                    )
                )

            self._set_job_status(job_id, "running")
            self._event(
                job_id,
                "job_running",
                f"Dispatching across {len(workers)} worker(s) in {execution_mode} mode.",
                detail={"workers": [worker.worker_id for worker in workers]},
            )

            async def _on_task_delta(task_id: str, title: str, delta: dict[str, Any]) -> None:
                self._publish_job(
                    job_id,
                    {
                        "event": "task_delta",
                        "job_id": job_id,
                        "task_id": task_id,
                        "title": title,
                        "delta": delta,
                    },
                )

            executions = await self._execute_plan(
                job_id,
                request,
                plan,
                workers,
                started,
                on_task_delta=_on_task_delta if request.stream else None,
            )

            self._set_job_status(job_id, "synthesizing")
            self._event(job_id, "job_synthesizing", "Synthesizing the final answer.")
            synthesis = self._reuse_single_task_output(job_id, plan, executions)
            if synthesis is None:
                if getattr(self.synthesizer, "supports_streaming", False):
                    async def _on_delta(delta: str) -> None:
                        self._publish_synthesis(job_id, delta)
                        self._publish_job(
                            job_id,
                            {"event": "synthesis_delta", "job_id": job_id, "delta": delta},
                        )

                    synthesis = await self.synthesizer.synthesize(
                        request, plan, executions, on_delta=_on_delta
                    )
                else:
                    synthesis = await self.synthesizer.synthesize(request, plan, executions)

            self._finish_job(job_id, plan, executions, synthesis, started)

        except asyncio.CancelledError:
            logger.info("Job %s cancelled", job_id)
            self._finalise_cancelled(job_id)
        except OrchestrationError as exc:
            self._fail_job(job_id, str(exc), started)
        except Exception as exc:
            logger.exception("Job %s failed unexpectedly", job_id)
            self._fail_job(job_id, f"{type(exc).__name__}: {exc}", started)

    def _reuse_single_task_output(
        self,
        job_id: str,
        plan: PlanSpec,
        executions: dict[str, TaskExecution],
    ) -> Optional[SynthesisResult]:
        """Return the single task's answer instead of paying for a second generation.

        A one-task plan is the common outcome for direct requests and for the planner's
        honest fallback. Synthesizing it again regenerates the same content from scratch
        at the full output budget, which can double wall-clock latency on CPU hosts.
        """
        if not self.config.reuse_single_task_output or len(plan.tasks) != 1:
            return None
        execution = executions.get(plan.tasks[0].task_id)
        if execution is None or not execution.succeeded:
            return None
        text = (execution.output or "").strip()
        if not text:
            return None

        # Publish on the same channel a streaming synthesizer would use so SSE consumers
        # receive the answer instead of waiting for the terminal snapshot.
        self._publish_synthesis(job_id, text)
        self._publish_job(
            job_id,
            {"event": "synthesis_delta", "job_id": job_id, "delta": text},
        )
        self._event(
            job_id,
            "synthesis_reused",
            "Single task output is the final answer; synthesis was skipped.",
        )
        return SynthesisResult(
            text=text,
            synthesis_available=True,
            complete=True,
            model=execution.runtime,
        )

    async def _execute_plan(
        self,
        job_id: str,
        request: PlanRequest,
        plan: PlanSpec,
        workers: list[WorkerTelemetry],
        started: float,
        *,
        on_task_delta: Optional[Callable[[str, str, dict[str, Any]], Awaitable[None]]] = None,
    ) -> dict[str, TaskExecution]:
        semaphore = asyncio.Semaphore(request.max_parallel_tasks)
        states: dict[str, str] = {task.task_id: "pending" for task in plan.tasks}
        executions: dict[str, TaskExecution] = {}
        inflight: dict[asyncio.Task, str] = {}

        while True:
            if time.monotonic() - started > self.config.max_job_seconds:
                raise OrchestrationError(
                    f"Job exceeded the {self.config.max_job_seconds:.0f}s execution budget."
                )

            for task_id in ready_tasks(plan, states):
                if task_id in executions:
                    continue
                states[task_id] = "running"
                dep_outputs = {
                    dep: executions[dep].output or ""
                    for dep in plan.task_map()[task_id].depends_on
                    if dep in executions and executions[dep].output
                }
                runner = asyncio.create_task(
                    self._execute_task(
                        job_id=job_id,
                        task=plan.task_map()[task_id],
                        request=request,
                        workers=workers,
                        dependency_outputs=dep_outputs,
                        semaphore=semaphore,
                        on_delta=on_task_delta,
                    )
                )
                inflight[runner] = task_id

            if inflight:
                try:
                    done, _ = await asyncio.wait(
                        inflight.keys(), return_when=asyncio.FIRST_COMPLETED
                    )
                except asyncio.CancelledError:
                    # The job was cancelled: stop every in-flight task before unwinding.
                    for runner in inflight:
                        runner.cancel()
                    if inflight:
                        await asyncio.gather(*inflight.keys(), return_exceptions=True)
                    inflight.clear()
                    raise
                for finished in done:
                    task_id = inflight.pop(finished)
                    execution = self._execution_from_task(task_id, finished)
                    executions[task_id] = execution
                    states[task_id] = execution.status
                    self._update_task_counts(job_id, plan, executions)
                continue

            pending = [task.task_id for task in plan.tasks if states.get(task.task_id) == "pending"]
            if not pending:
                break

            for task_id in pending:
                blockers = blocked_by(plan, task_id, states)
                execution = TaskExecution(
                    task_id=task_id,
                    status="failed",
                    worker_id=None,
                    error=f"dependency did not complete: {', '.join(blockers) or 'unknown'}",
                )
                executions[task_id] = execution
                states[task_id] = "failed"
                self.db.upsert_orchestration_task(
                    job_id,
                    {
                        "task_id": task_id,
                        "state": "failed",
                        "error": execution.error,
                        "completed_at": utc_now(),
                    },
                )
                self._event(
                    job_id,
                    "task_failed",
                    f"Task '{task_id}' was not executed: {execution.error}",
                    task_id=task_id,
                    level="error",
                )
            self._update_task_counts(job_id, plan, executions)

        return executions

    async def _execute_task(
        self,
        *,
        job_id: str,
        task,
        request: PlanRequest,
        workers: list[WorkerTelemetry],
        dependency_outputs: dict[str, str],
        semaphore: asyncio.Semaphore,
        on_delta: Optional[Callable[[str, str, dict[str, Any]], Awaitable[None]]] = None,
    ) -> TaskExecution:
        context = DispatchContext(job_id=job_id, model=request.model, max_tokens=request.max_tokens)
        excluded: set[str] = set()
        last_error: Optional[str] = None

        for attempt in range(task.retries + 1):
            selection = select_worker(task, workers, self.config.weights, exclude=excluded)
            if selection is None and excluded:
                # A single-worker pool must still retry; reuse the worker already tried.
                selection = select_worker(task, workers, self.config.weights)
            if selection is None:
                return TaskExecution(
                    task_id=task.task_id,
                    status="failed",
                    worker_id=None,
                    error=last_error or "no worker satisfies this task's requirements",
                )

            worker_id = selection.worker_id
            dispatcher = self._dispatcher_for(worker_id)
            self.db.upsert_orchestration_task(
                job_id,
                {
                    "task_id": task.task_id,
                    "state": "assigned",
                    "worker_id": worker_id,
                    "attempts": attempt + 1,
                    "started_at": utc_now(),
                },
            )
            self._event(
                job_id,
                "task_assigned",
                f"Task '{task.task_id}' assigned to {worker_id} (attempt {attempt + 1}).",
                task_id=task.task_id,
                detail={"score": selection.score, "components": selection.components},
            )
            try:
                self.db.log_routing_event(
                    "job_task_assigned",
                    source="coordinator",
                    target=worker_id,
                    job_id=job_id,
                    task_id=task.task_id,
                    state="assigned",
                    message=(
                        f"Task '{task.task_id}' assigned to {worker_id} "
                        f"(score {selection.score:.3f}, attempt {attempt + 1})"
                    ),
                    detail={
                        "score": selection.score,
                        "components": selection.components,
                        "unmeasured": list(selection.unmeasured),
                    },
                )
            except Exception:
                pass

            async with semaphore:
                self.db.upsert_orchestration_task(job_id, {"task_id": task.task_id, "state": "running"})
                try:
                    timeout = task.timeout_s + self.config.dispatch_grace_seconds
                    if (
                        on_delta is not None
                        and worker_id != LOCAL_ORIGIN
                        and hasattr(dispatcher, "open_stream")
                    ):
                        execution = await asyncio.wait_for(
                            self._dispatch_streaming(
                                dispatcher=dispatcher,
                                task=task,
                                context=context,
                                dependency_outputs=dependency_outputs,
                                worker_id=worker_id,
                                job_id=job_id,
                                on_delta=on_delta,
                            ),
                            timeout=timeout,
                        )
                    else:
                        execution = await asyncio.wait_for(
                            dispatcher.dispatch(task, context, dependency_outputs, worker_id),
                            timeout=timeout,
                        )
                except asyncio.TimeoutError:
                    execution = TaskExecution(
                        task_id=task.task_id,
                        status="failed",
                        worker_id=worker_id,
                        error=f"task timed out after {task.timeout_s:.0f}s",
                    )
                except Exception as exc:
                    execution = TaskExecution(
                        task_id=task.task_id,
                        status="failed",
                        worker_id=worker_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )

            if execution.succeeded:
                self.db.upsert_orchestration_task(
                    job_id,
                    {
                        "task_id": task.task_id,
                        "state": "completed",
                        "output": execution.output,
                        "error": None,
                        "duration_ms": execution.duration_ms,
                        "completed_at": utc_now(),
                    },
                )
                self._event(
                    job_id,
                    "task_completed",
                    f"Task '{task.task_id}' completed on {worker_id} "
                    f"({execution.duration_ms:.0f} ms).",
                    task_id=task.task_id,
                )
                try:
                    self.db.log_routing_event(
                        "job_task_completed",
                        source=worker_id,
                        target="coordinator",
                        job_id=job_id,
                        task_id=task.task_id,
                        state="completed",
                        message=f"Task '{task.task_id}' completed on {worker_id}",
                        detail={"duration_ms": execution.duration_ms},
                    )
                except Exception:
                    pass
                return execution

            last_error = execution.error or "worker returned no usable output"
            excluded.add(worker_id)
            self.db.upsert_orchestration_task(
                job_id,
                {
                    "task_id": task.task_id,
                    "state": "pending" if attempt < task.retries else "failed",
                    "error": last_error,
                    "duration_ms": execution.duration_ms,
                },
            )
            self._event(
                job_id,
                "task_retrying" if attempt < task.retries else "task_failed",
                f"Task '{task.task_id}' attempt {attempt + 1} failed: {last_error}",
                task_id=task.task_id,
                level="warning" if attempt < task.retries else "error",
            )
            try:
                self.db.log_routing_event(
                    "job_task_failed",
                    source=worker_id,
                    target="coordinator",
                    job_id=job_id,
                    task_id=task.task_id,
                    state="failed" if attempt >= task.retries else "retrying",
                    message=f"Task '{task.task_id}' attempt {attempt + 1} failed on {worker_id}",
                    detail={"error": last_error, "attempt": attempt + 1},
                )
            except Exception:
                pass

        return TaskExecution(
            task_id=task.task_id,
            status="failed",
            worker_id=None,
            error=last_error or "task failed after all retries",
        )

    async def _dispatch_streaming(
        self,
        *,
        dispatcher: TaskDispatcher,
        task,
        context: DispatchContext,
        dependency_outputs: dict[str, str],
        worker_id: str,
        job_id: str,
        on_delta: Callable[[str, str, dict[str, Any]], Awaitable[None]],
    ) -> TaskExecution:
        messages = [
            {"role": "system", "content": TASK_WORKER_SYSTEM_PROMPT},
            {"role": "user", "content": _task_prompt(task, dependency_outputs)},
        ]
        options = {
            "max_tokens": max(1, min(task.max_tokens, context.max_tokens, 4096)),
            "temperature": 0.4,
        }
        async for event in dispatcher.open_stream(  # type: ignore[attr-defined]
            worker_id=worker_id,
            model_name=task.requirements.model or context.model,
            messages=messages,
            options=options,
            timeout_s=task.timeout_s,
            first_token_timeout_s=min(180.0, task.timeout_s),
            job_id=job_id,
        ):
            event_type = str(event.get("event") or "")
            if event_type == "delta":
                delta = event.get("delta")
                if isinstance(delta, dict) and delta:
                    await on_delta(task.task_id, task.title, delta)
                continue
            if event_type == "error":
                return TaskExecution(
                    task_id=task.task_id,
                    status="failed",
                    worker_id=worker_id,
                    error=str(event.get("error") or "worker stream failed"),
                )
            if event_type == "done":
                state = str(event.get("state") or "failed")
                result = event.get("result_data") or {}
                duration_ms = float(event.get("execution_time_ms") or 0.0)
                if state != "completed":
                    return TaskExecution(
                        task_id=task.task_id,
                        status="cancelled" if state == "cancelled" else "failed",
                        worker_id=worker_id,
                        error=str(event.get("error") or f"worker reported {state}"),
                        duration_ms=duration_ms,
                    )
                text = str(
                    (result.get("response") if isinstance(result, dict) else "")
                    or (result.get("text") if isinstance(result, dict) else "")
                    or ""
                ).strip()
                if not text:
                    return TaskExecution(
                        task_id=task.task_id,
                        status="failed",
                        worker_id=worker_id,
                        error="worker returned an empty result",
                        duration_ms=duration_ms,
                    )
                usage = result.get("usage") if isinstance(result, dict) else {}
                return TaskExecution(
                    task_id=task.task_id,
                    status="completed",
                    worker_id=worker_id,
                    output=text[:MAX_OUTPUT_CHARS],
                    duration_ms=duration_ms,
                    runtime=str(result.get("runtime") or "remote"),
                    truncated=len(text) > MAX_OUTPUT_CHARS,
                    usage=usage or {},
                )
        return TaskExecution(
            task_id=task.task_id,
            status="failed",
            worker_id=worker_id,
            error="worker stream ended without a result",
        )

    # ------------------------------------------------------------------ helpers

    async def _resolve_workers(
        self, requested_mode: str, *, require_streaming: bool = False
    ) -> tuple[list[WorkerTelemetry], str]:
        local_workers: list[WorkerTelemetry] = []
        distributed_workers: list[WorkerTelemetry] = []

        if requested_mode in ("auto", "local"):
            local_workers = await self.local_dispatcher.list_workers()

        if requested_mode in ("auto", "distributed"):
            dispatcher = self._get_coordinator_dispatcher()
            if dispatcher is not None:
                try:
                    distributed_workers = await dispatcher.list_workers()
                except Exception as exc:
                    logger.info("Distributed worker discovery failed: %s", exc)
                    distributed_workers = []

        if require_streaming:
            distributed_workers = [
                worker
                for worker in distributed_workers
                if "streaming_chat" in worker.capabilities
            ]

        if requested_mode == "distributed":
            return distributed_workers, "distributed"
        if requested_mode == "local":
            return local_workers, "local"

        if distributed_workers and local_workers:
            return distributed_workers + local_workers, "hybrid"
        if distributed_workers:
            return distributed_workers, "distributed"
        return local_workers, "local"

    def _get_coordinator_dispatcher(self) -> Optional[TaskDispatcher]:
        if self.coordinator_dispatcher is not None:
            return self.coordinator_dispatcher
        url = self.config.coordinator_admin_url
        if not url:
            return None
        self.coordinator_dispatcher = CoordinatorDispatcher(url)
        return self.coordinator_dispatcher

    def _dispatcher_for(self, worker_id: str) -> TaskDispatcher:
        if worker_id == LOCAL_ORIGIN:
            return self.local_dispatcher
        dispatcher = self._get_coordinator_dispatcher()
        if dispatcher is None:
            raise OrchestrationError("A distributed worker was selected but no coordinator is configured.")
        return dispatcher

    @staticmethod
    def _execution_from_task(task_id: str, finished: asyncio.Task) -> TaskExecution:
        try:
            return finished.result()
        except asyncio.CancelledError:
            return TaskExecution(
                task_id=task_id,
                status="cancelled",
                worker_id=None,
                error="task cancelled",
            )
        except Exception as exc:
            return TaskExecution(
                task_id=task_id,
                status="failed",
                worker_id=None,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _update_task_counts(
        self,
        job_id: str,
        plan: PlanSpec,
        executions: dict[str, TaskExecution],
    ) -> None:
        completed = sum(1 for item in executions.values() if item.succeeded)
        failed = sum(
            1
            for item in executions.values()
            if not item.succeeded and item.status in ("failed", "cancelled")
        )
        self.db.update_orchestration_job(
            job_id,
            {
                "tasks_completed": completed,
                "tasks_failed": failed,
            },
        )

    def _finish_job(
        self,
        job_id: str,
        plan: PlanSpec,
        executions: dict[str, TaskExecution],
        synthesis: SynthesisResult,
        started: float,
    ) -> None:
        duration_ms = (time.monotonic() - started) * 1000.0
        any_completed = any(item.succeeded for item in executions.values())
        all_completed = all(item.succeeded for item in executions.values())

        if synthesis.text:
            status = "completed" if synthesis.complete and all_completed else "partial"
            error = None
            if not synthesis.complete:
                names = ", ".join(item["task_id"] for item in synthesis.missing)
                error = f"Some tasks did not complete: {names}"
        elif any_completed:
            status = "partial"
            error = synthesis.reason or "Synthesis did not produce a final answer."
        else:
            status = "failed"
            error = synthesis.reason or "No task produced a usable result."

        self.db.update_orchestration_job(
            job_id,
            {
                "status": status,
                "result_text": synthesis.text,
                "error": error,
                "completed_at": utc_now(),
                "duration_ms": round(duration_ms, 2),
            },
        )
        self._event(
            job_id,
            f"job_{status}",
            f"Job finished with status '{status}'.",
            level="info" if status in ("completed", "partial") else "error",
            detail={
                "synthesis_available": synthesis.synthesis_available,
                "missing": synthesis.missing,
                "duration_ms": round(duration_ms, 2),
            },
        )
        self._publish_job(
            job_id,
            {
                "event": "job_done",
                "job_id": job_id,
                "status": status,
                "result": synthesis.text,
                "error": error,
                "missing": synthesis.missing,
                "duration_ms": round(duration_ms, 2),
            },
        )

    def _fail_job(self, job_id: str, message: str, started: float) -> None:
        duration_ms = (time.monotonic() - started) * 1000.0
        self.db.update_orchestration_job(
            job_id,
            {
                "status": "failed",
                "error": message,
                "completed_at": utc_now(),
                "duration_ms": round(duration_ms, 2),
            },
        )
        self._event(job_id, "job_failed", message, level="error")
        self._publish_job(
            job_id,
            {
                "event": "job_done",
                "job_id": job_id,
                "status": "failed",
                "result": None,
                "error": message,
                "missing": [],
                "duration_ms": round(duration_ms, 2),
            },
        )

    def _finalise_cancelled(self, job_id: str) -> None:
        now = utc_now()
        self.db.update_orchestration_job(
            job_id,
            {
                "status": "cancelled",
                "error": "Cancelled by user request.",
                "completed_at": now,
            },
        )
        for task in self.db.list_orchestration_tasks(job_id):
            if task["state"] not in ("completed", "failed", "cancelled"):
                self.db.upsert_orchestration_task(
                    job_id,
                    {
                        "task_id": task["task_id"],
                        "state": "cancelled",
                        "error": "Cancelled by user request.",
                        "completed_at": now,
                    },
                )
        self._event(job_id, "job_cancelled", "Job cancelled by user request.", level="warning")
        self._publish_job(
            job_id,
            {
                "event": "job_done",
                "job_id": job_id,
                "status": "cancelled",
                "result": None,
                "error": "Cancelled by user request.",
                "missing": [],
            },
        )

    def _set_job_status(
        self,
        job_id: str,
        status: str,
        *,
        started_at: Optional[datetime] = None,
    ) -> None:
        updates: dict[str, Any] = {"status": status}
        if started_at is not None:
            updates["started_at"] = started_at
        self.db.update_orchestration_job(job_id, updates)

    def _event(
        self,
        job_id: str,
        event_type: str,
        message: str,
        *,
        task_id: Optional[str] = None,
        level: str = "info",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        try:
            self.db.add_job_event(
                job_id,
                event_type,
                message,
                task_id=task_id,
                level=level,
                detail=detail,
            )
        except Exception as exc:  # event persistence must not break execution
            logger.warning("Could not persist job event for %s: %s", job_id, exc)


class OrchestrationError(RuntimeError):
    """A job-level failure that should be shown to the user verbatim."""


_GLOBAL_SERVICE: Optional[OrchestrationService] = None


def _env_positive_int(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def get_orchestration_service() -> OrchestrationService:
    global _GLOBAL_SERVICE
    if _GLOBAL_SERVICE is None:
        # The product service keeps the two-stage planner/synthesizer pipeline for real
        # multi-task plans, but avoids the duplicate generation for single-task plans.
        # Planning output only needs a compact JSON object; a 512-token ceiling prevents
        # a rambling planner from spending the whole latency budget before work starts.
        _GLOBAL_SERVICE = OrchestrationService(
            planner=Planner(max_plan_tokens=_env_positive_int("RACON_PLANNER_MAX_TOKENS", 512)),
            synthesizer=Synthesizer(
                cap_tokens=_env_positive_int("RACON_SYNTHESIS_MAX_TOKENS", 1024)
            ),
            config=OrchestrationConfig(reuse_single_task_output=True),
        )
    return _GLOBAL_SERVICE


def reset_orchestration_service() -> None:
    global _GLOBAL_SERVICE
    _GLOBAL_SERVICE = None
