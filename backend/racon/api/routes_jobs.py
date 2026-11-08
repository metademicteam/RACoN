"""Agentic job orchestration routes.

A job is accepted asynchronously, decomposed into a validated DAG, dispatched to workers,
and synthesized. ``GET /api/v1/jobs/{id}`` exposes the plan and per-task state;
``GET /api/v1/jobs/{id}/events`` streams lifecycle events over SSE.

No route reports a job as completed unless a runtime produced output and the synthesizer
produced a final answer; partial and failed jobs carry an explicit reason.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from backend.racon.orchestration.model_router import (
    build_profiles_from_engine,
    route_request,
)
from backend.racon.orchestration.schemas import MAX_INSTRUCTION_CHARS, MAX_TASKS, PlanRequest
from backend.racon.orchestration.service import get_orchestration_service
from racon.services.engine_service import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])

TERMINAL_JOB_STATES = frozenset({"completed", "partial", "failed", "cancelled"})


def _sse_frame(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class CreateJobRequest(BaseModel):
    """Request accepted by the orchestration layer.

    Legacy fields accepted by the previous synchronous route (``objective``, ``mode``,
    ``preferred_worker_id``, ``model_partition_mb``) are ignored rather than rejected, so
    older clients keep working.
    """

    name: Optional[str] = Field(default=None, max_length=160)
    model_name: str = Field(default="auto", max_length=128)
    prompt: str = Field(..., min_length=1, max_length=MAX_INSTRUCTION_CHARS)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    decompose: bool = True
    execution_mode: Literal["auto", "local", "distributed"] = "auto"
    max_parallel_tasks: int = Field(default=3, ge=1, le=MAX_TASKS)


@router.get("")
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
):
    """List orchestration jobs, most recent first."""
    return get_orchestration_service().list_jobs(limit=limit, offset=offset, status=status)


@router.post("", status_code=202)
async def submit_job(req: CreateJobRequest):
    """Accept a request, plan it, and begin execution asynchronously."""
    service = get_orchestration_service()
    model_name = req.model_name
    router_payload: dict = {}

    if model_name.strip().lower() == "auto":
        profiles = await build_profiles_from_engine(get_engine())
        decision = route_request(
            [{"role": "user", "content": req.prompt}],
            requested_model="auto",
            profiles=profiles,
        )
        router_payload = decision.to_dict()
        if not decision.selected_model:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "no_route",
                        "message": decision.reason,
                        "details": decision.to_dict(),
                    }
                },
            )
        model_name = decision.selected_model

    plan_request = PlanRequest(
        prompt=req.prompt,
        model=model_name,
        max_tokens=req.max_tokens,
        decompose=req.decompose,
        execution_mode=req.execution_mode,
        max_parallel_tasks=req.max_parallel_tasks,
        router=router_payload,
    )
    try:
        job = await service.submit(plan_request, name=req.name)
    except Exception as exc:
        logger.exception("Job submission failed")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "job_submission_failed",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                }
            },
        )
    return job


@router.get("/{job_id}")
def get_job(job_id: str):
    """Return the job plan, per-task state, and final result."""
    job = get_orchestration_service().get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.get("/{job_id}/stream")
async def stream_job(job_id: str, request: Request):
    """Stream job state and synthesis tokens over SSE until the job is terminal."""
    service = get_orchestration_service()
    if not service.get_job(job_id):
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    queue = service.subscribe_synthesis(job_id)
    broker_queue, replay = await service.subscribe_job_stream(job_id)

    def task_frames() -> list[str]:
        frames: list[str] = []
        while replay:
            item = replay.pop(0)
            if item.get("event") == "task_delta":
                frames.append(_sse_frame({"type": "task_delta", **item}))
        while True:
            try:
                item = broker_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if item.get("event") == "task_delta":
                frames.append(_sse_frame({"type": "task_delta", **item}))
        return frames

    async def event_generator():
        last_signature = None
        idle_ticks = 0
        try:
            while True:
                if await request.is_disconnected():
                    return

                for frame in task_frames():
                    yield frame

                while True:
                    try:
                        delta = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    yield _sse_frame({"type": "delta", "text": delta})

                job = service.get_job(job_id) or {}
                tasks = job.get("tasks") or []
                signature = (
                    job.get("status"),
                    job.get("tasks_completed"),
                    job.get("tasks_failed"),
                    tuple((item.get("task_id"), item.get("state")) for item in tasks),
                )
                if signature != last_signature:
                    yield _sse_frame({"type": "job", "job": job})
                    last_signature = signature

                if job.get("status") in TERMINAL_JOB_STATES:
                    while True:
                        try:
                            delta = queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                        yield _sse_frame({"type": "delta", "text": delta})
                    yield _sse_frame({"type": "done", "job": job})
                    return

                idle_ticks += 1
                if idle_ticks % 50 == 0:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.2)
        finally:
            service.unsubscribe_synthesis(job_id, queue)
            service.unsubscribe_job_stream(job_id, broker_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str):
    """Cancel a queued or running job; dispatch stops and tasks are marked cancelled."""
    service = get_orchestration_service()
    if not service.get_job(job_id):
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return await service.cancel(job_id)


@router.get("/{job_id}/events")
async def stream_job_events(
    job_id: str,
    request: Request,
    after_id: int = Query(0, ge=0),
):
    """Server-sent stream of lifecycle events until the job reaches a terminal state."""
    service = get_orchestration_service()
    if not service.get_job(job_id):
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    async def event_generator():
        cursor = after_id
        idle_ticks = 0
        while True:
            if await request.is_disconnected():
                return

            events = service.events(job_id, after_id=cursor)
            for event in events:
                cursor = max(cursor, int(event["id"]))
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                idle_ticks = 0

            job = service.get_job(job_id) or {}
            if job.get("status") in TERMINAL_JOB_STATES and not events:
                yield f"data: {json.dumps({'event_type': 'stream_end', 'job_id': job_id, 'status': job.get('status')})}\n\n"
                return

            idle_ticks += 1
            if idle_ticks % 20 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
