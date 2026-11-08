"""Regression tests for the latency budgets and the single-task fast path.

These tests exercise the scheduling decisions without a real model runtime.
"""
from __future__ import annotations

import asyncio
import functools
import json
import time

from backend.racon.orchestration.executor import TaskDispatcher, TaskExecution
from backend.racon.orchestration.planner import parse_plan
from backend.racon.orchestration.schemas import PlanRequest, PlanSpec, TaskSpec
from backend.racon.orchestration.selector import LOCAL_ORIGIN, WorkerTelemetry
from backend.racon.orchestration.service import OrchestrationConfig, OrchestrationService
from backend.racon.orchestration.synthesizer import Synthesizer
from racon.models.manager import ModelManager
from racon.runtime.types import ChatChunk, ChatUsage


def async_test(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


class RecordingStreamingEngine:
    def __init__(self) -> None:
        self.max_tokens = None

    async def chat_stream(self, messages, **kwargs):
        self.max_tokens = kwargs.get("max_tokens")
        yield ChatChunk(delta="answer")
        yield ChatChunk(
            done=True,
            finish_reason="stop",
            usage=ChatUsage(prompt_tokens=1, completion_tokens=1),
        )

    async def chat(self, *args, **kwargs):
        raise AssertionError("streaming synthesis must use chat_stream")


def _single_task_plan() -> PlanSpec:
    return PlanSpec(
        objective="test",
        tasks=[TaskSpec(task_id="a", title="A", instruction="do a")],
        synthesis="combine",
    )


class StaticPlanner:
    def __init__(self, plan: PlanSpec) -> None:
        self._plan = plan

    async def plan(self, request):
        return self._plan


class StaticDispatcher(TaskDispatcher):
    def __init__(self, output: str = "task answer") -> None:
        self.output = output

    async def list_workers(self):
        return [
            WorkerTelemetry(
                worker_id=LOCAL_ORIGIN,
                origin=LOCAL_ORIGIN,
                status="available",
                capabilities=frozenset({"text_generation"}),
            )
        ]

    async def dispatch(self, task, context, dependency_outputs, worker_id):
        return TaskExecution(
            task_id=task.task_id,
            status="completed",
            worker_id=worker_id or LOCAL_ORIGIN,
            output=self.output,
            runtime="llama_cpp",
        )


class ExplodingSynthesizer:
    supports_streaming = True

    async def synthesize(self, *args, **kwargs):
        raise AssertionError("single-task plans must not run the synthesizer")


async def _wait_terminal(service: OrchestrationService, job_id: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = service.get_job(job_id)
        if job and job.get("status") in ("completed", "partial", "failed", "cancelled"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {service.get_job(job_id)}")


async def _noop(text: str) -> None:
    return None


@async_test
async def test_synthesis_cap_limits_the_merge_budget():
    engine = RecordingStreamingEngine()
    synthesizer = Synthesizer(engine_factory=lambda: engine, cap_tokens=512)
    request = PlanRequest(prompt="p", model="m", max_tokens=2048)
    executions = {
        "a": TaskExecution(
            task_id="a",
            status="completed",
            worker_id=LOCAL_ORIGIN,
            output="task output",
        )
    }

    result = await synthesizer.synthesize(
        request, _single_task_plan(), executions, on_delta=_noop
    )

    assert result.text == "answer"
    assert engine.max_tokens == 512


@async_test
async def test_single_task_output_is_reused_without_synthesis(isolated_db):
    service = OrchestrationService(
        planner=StaticPlanner(_single_task_plan()),
        local_dispatcher=StaticDispatcher("task answer"),
        synthesizer=ExplodingSynthesizer(),
        database=isolated_db,
        config=OrchestrationConfig(
            coordinator_admin_url="",
            reuse_single_task_output=True,
        ),
    )

    job = await service.submit(PlanRequest(prompt="do the thing", model="m", max_tokens=256))
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "completed"
    assert job["result"] == "task answer"
    assert any(event["event_type"] == "synthesis_reused" for event in service.events(job["id"]))


@async_test
async def test_single_task_reuse_is_opt_in(isolated_db):
    class PassthroughSynthesizer:
        supports_streaming = False

        async def synthesize(self, *args, **kwargs):
            from backend.racon.orchestration.synthesizer import SynthesisResult

            return SynthesisResult(
                text="synthesized", synthesis_available=True, complete=True
            )

    service = OrchestrationService(
        planner=StaticPlanner(_single_task_plan()),
        local_dispatcher=StaticDispatcher("task answer"),
        synthesizer=PassthroughSynthesizer(),
        database=isolated_db,
        config=OrchestrationConfig(coordinator_admin_url=""),
    )

    job = await service.submit(PlanRequest(prompt="do the thing", model="m", max_tokens=256))
    job = await _wait_terminal(service, job["id"])

    assert job["result"] == "synthesized"


def test_parse_plan_drops_undefined_dependencies():
    payload = {
        "objective": "test",
        "tasks": [
            {
                "task_id": "a",
                "title": "A",
                "instruction": "do a",
                "depends_on": ["never-defined"],
            }
        ],
    }

    plan = parse_plan(json.dumps(payload))

    assert plan.tasks[0].depends_on == []
    assert "undefined dependency" in plan.planner_note.lower()


def test_model_scan_is_cached_and_invalidated(tmp_path, monkeypatch):
    import racon.models.manager as manager_module

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "tiny.gguf").write_bytes(b"GGUF")

    calls = {"count": 0}

    def fake_metadata(path):
        calls["count"] += 1
        return {"architecture": "test", "context_length": 2048}

    monkeypatch.setattr(manager_module, "inspect_gguf_metadata", fake_metadata)

    manager = ModelManager(models_dir)
    monkeypatch.setattr(manager, "_search_roots", lambda: [models_dir])

    assert [model.id for model in manager.installed()] == ["tiny"]
    assert calls["count"] == 1

    manager.installed()
    assert calls["count"] == 1

    manager.invalidate_cache()
    manager.installed()
    assert calls["count"] == 2
