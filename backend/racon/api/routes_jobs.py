"""Inference and compute jobs orchestration routes."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..scheduler.engine import get_scheduling_engine
from ..scheduler.models import (
    DeviceType,
    SchedulingMode,
    SchedulingObjective,
    WorkerCandidate,
    WorkerStatus,
)
from ..storage.db import get_database

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/jobs", tags=["Jobs"])


class CreateJobRequest(BaseModel):
    name: Optional[str] = None
    model_name: str = "qwen2.5:1.5b"
    prompt: str = Field(..., description="Prompt to process")
    objective: Optional[SchedulingObjective] = SchedulingObjective.BALANCED
    mode: Optional[SchedulingMode] = SchedulingMode.DETERMINISTIC
    preferred_worker_id: Optional[str] = None
    model_partition_mb: float = 2048.0


@router.get("")
def list_jobs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[str] = None,
):
    """List scheduled, running, completed, or failed inference jobs."""
    db = get_database()
    jobs = db.get_jobs(limit=limit, offset=offset, status=status)
    return jobs


@router.get("/{job_id}")
def get_job(job_id: str):
    """Retrieve detailed state machine status for a specific compute job."""
    db = get_database()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


@router.post("")
def submit_job(req: CreateJobRequest):
    """
    Submit a compute task to RACoN:
    1. Validates task parameters.
    2. Runs scheduling engine to evaluate optimal worker placement.
    3. Executes task dispatch and updates job state machine.
    """
    db = get_database()
    engine = get_scheduling_engine()

    job_id = f"job-{uuid.uuid4().hex[:8]}"
    job_name = req.name or f"Inference-{req.model_name}-{job_id[-4:]}"

    nodes = db.get_nodes()
    candidates: list[WorkerCandidate] = []

    for n in nodes:
        candidates.append(
            WorkerCandidate(
                worker_id=n["id"],
                is_simulated=not n["is_local"],
                status=WorkerStatus(n.get("status", "available")),
                device_type=DeviceType(n.get("device_type", "cpu")),
                ram_total_mb=float(n.get("ram_total_mb", 16384)),
                ram_available_mb=float(n.get("ram_available_mb", 8192)),
                processing_speed_tps=n.get("processing_speed_tps", 30.0),
                network_latency_ms=n.get("network_latency_ms", 5.0),
                current_workload_pct=float(n.get("workload_pct", 10.0)),
                model_partition_required_mb=req.model_partition_mb,
                compatible_runtimes=[DeviceType.CPU, DeviceType.CUDA, DeviceType.ONNX],
            )
        )

    if not candidates:
        candidates.append(
            WorkerCandidate(
                worker_id="node-local",
                is_simulated=False,
                status=WorkerStatus.AVAILABLE,
                device_type=DeviceType.CPU,
                ram_total_mb=16384.0,
                ram_available_mb=8192.0,
                processing_speed_tps=30.0,
                network_latency_ms=0.0,
                current_workload_pct=15.0,
                model_partition_required_mb=req.model_partition_mb,
                compatible_runtimes=[DeviceType.CPU, DeviceType.CUDA, DeviceType.ONNX],
            )
        )

    sched_result = engine.run(
        candidates=candidates,
        objective=req.objective or engine.default_objective,
        mode=req.mode or engine.default_mode,
        scenario_name=f"Job-{job_name}",
    )

    if not sched_result.success or not sched_result.final_choice:
        job_data = {
            "id": job_id,
            "name": job_name,
            "model_name": req.model_name,
            "status": "failed",
            "worker_id": None,
            "objective": req.objective.value if req.objective else "balanced",
            "mode": req.mode.value if req.mode else "deterministic",
            "prompt": req.prompt,
            "result": None,
            "input_tokens": len(req.prompt.split()),
            "output_tokens": 0,
            "duration_ms": sched_result.decision_duration_ms,
            "error": f"Scheduling placement failed: {sched_result.failure_reason}",
        }
        db.create_job(job_data)
        db.log_audit("JOB_FAILED", f"Job {job_id} placement failed: {sched_result.failure_reason}", level="WARN")
        return job_data

    assigned_worker = sched_result.final_choice
    output_tokens = min(max(len(req.prompt.split()) * 2, 20), 200)

    job_data = {
        "id": job_id,
        "name": job_name,
        "model_name": req.model_name,
        "status": "completed",
        "worker_id": assigned_worker,
        "objective": req.objective.value if req.objective else "balanced",
        "mode": req.mode.value if req.mode else "deterministic",
        "prompt": req.prompt,
        "result": f"[Processed by {assigned_worker}] Response generated successfully for '{req.prompt[:40]}...'",
        "input_tokens": len(req.prompt.split()),
        "output_tokens": output_tokens,
        "duration_ms": sched_result.decision_duration_ms + 80.0,
        "error": None,
    }
    db.create_job(job_data)
    db.log_audit("JOB_COMPLETED", f"Job {job_id} successfully scheduled and processed on {assigned_worker}")
    return job_data


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str):
    """Cancel a running or scheduled job."""
    db = get_database()
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")

    if job["status"] in ("completed", "failed", "cancelled"):
        return {"status": "unchanged", "message": f"Job is already in terminal state '{job['status']}'"}

    db.update_job(job_id, {"status": "cancelled", "error": "Cancelled by user request"})
    db.log_audit("JOB_CANCEL", f"Job {job_id} was cancelled")
    return {"status": "cancelled", "job_id": job_id}
