"""Tests for the agentic orchestration layer.

These tests never need a real model runtime: planning, dispatch, and synthesis are
exercised through explicit doubles, while persistence and DAG logic are real.
"""
from __future__ import annotations

import asyncio
import functools
import json

import pytest

from backend.racon.orchestration.dag import (
    DagValidationError,
    ready_tasks,
    topological_order,
    validate_plan,
)
from backend.racon.orchestration.executor import (
    DispatchContext,
    LocalDispatcher,
    TaskDispatcher,
    TaskExecution,
)
from backend.racon.orchestration.planner import Planner, PlanParseError, parse_plan
from backend.racon.orchestration.schemas import (
    PlanRequest,
    PlanSpec,
    TaskSpec,
    single_task_plan,
)
from backend.racon.orchestration.selector import (
    LOCAL_ORIGIN,
    SelectionWeights,
    WorkerTelemetry,
    evaluate_worker,
    select_worker,
)
from backend.racon.orchestration.service import OrchestrationConfig, OrchestrationService
from backend.racon.orchestration.synthesizer import SynthesisResult, Synthesizer
from racon.runtime.types import ChatChunk, ChatResult, ChatUsage, RuntimeUnavailableError

# --------------------------------------------------------------------------- common doubles


def async_test(fn):
    """Run a coroutine test without adding pytest-asyncio as a dependency."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def _task(task_id: str, depends_on: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        title=f"Task {task_id}",
        instruction=f"Do {task_id}",
        depends_on=depends_on or [],
    )


def _plan(*tasks: TaskSpec) -> PlanSpec:
    return PlanSpec(objective="test", tasks=list(tasks), synthesis="combine")


class FakePlanner:
    def __init__(self, plan: PlanSpec) -> None:
        self.plan_spec = plan
        self.calls = 0

    async def plan(self, request: PlanRequest) -> PlanSpec:
        self.calls += 1
        return self.plan_spec


class FakeDispatcher(TaskDispatcher):
    def __init__(self, handler=None, worker_id: str = LOCAL_ORIGIN) -> None:
        self.worker_id = worker_id
        self.handler = handler or self._default
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.max_active = 0
        self._active = 0

    async def list_workers(self) -> list[WorkerTelemetry]:
        return [
            WorkerTelemetry(
                worker_id=self.worker_id,
                origin=LOCAL_ORIGIN,
                status="available",
                capabilities=frozenset({"text_generation"}),
                ram_total_mb=8192.0,
                ram_available_mb=4096.0,
            )
        ]

    async def _default(self, task, context, deps, worker_id):
        self._active += 1
        self.max_active = max(self.max_active, self._active)
        await asyncio.sleep(0.02)
        self._active -= 1
        return TaskExecution(
            task_id=task.task_id,
            status="completed",
            worker_id=worker_id,
            output=f"output of {task.task_id}",
        )

    async def dispatch(self, task, context, dependency_outputs, worker_id):
        self.calls.append((task.task_id, dict(dependency_outputs)))
        return await self.handler(task, context, dependency_outputs, worker_id)


class FakeSynthesizer:
    def __init__(self, text: str | None = "final answer") -> None:
        self.text = text
        self.calls = 0
        self.last_missing: list[dict] = []

    async def synthesize(self, request, plan, executions) -> SynthesisResult:
        self.calls += 1
        missing = [
            {"task_id": ex.task_id, "error": ex.error or ex.status}
            for ex in executions.values()
            if not ex.succeeded
        ]
        self.last_missing = missing
        if not any(ex.succeeded for ex in executions.values()):
            return SynthesisResult(
                text=None,
                synthesis_available=True,
                complete=False,
                reason="No task produced a usable result; nothing could be synthesized.",
                missing=missing,
            )
        return SynthesisResult(
            text=self.text,
            synthesis_available=self.text is not None,
            complete=not missing,
            reason=None if self.text else "synthesis unavailable",
            missing=missing,
        )


def _request(**overrides) -> PlanRequest:
    data = {
        "prompt": "Research and compare options",
        "model": "test-model",
        "max_tokens": 128,
        "decompose": True,
        "execution_mode": "local",
        "max_parallel_tasks": 3,
    }
    data.update(overrides)
    return PlanRequest(**data)


async def _wait_terminal(service: OrchestrationService, job_id: str, timeout: float = 8.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        job = service.get_job(job_id)
        if job and job["status"] in ("completed", "partial", "failed", "cancelled"):
            return job
        await asyncio.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish: {service.get_job(job_id)}")


def _service(plan, dispatcher, synthesizer=None, db=None, parallel=3):
    return OrchestrationService(
        planner=FakePlanner(plan),
        local_dispatcher=dispatcher,
        synthesizer=synthesizer or FakeSynthesizer(),
        database=db,
        config=OrchestrationConfig(coordinator_admin_url=""),
    )


# --------------------------------------------------------------------------- DAG


def test_topological_order_respects_dependencies():
    plan = _plan(_task("a"), _task("b"), _task("c", ["a", "b"]))
    order = topological_order(plan)
    assert order.index("a") < order.index("c")
    assert order.index("b") < order.index("c")


def test_cycle_is_rejected():
    plan = _plan(_task("a", ["b"]), _task("b", ["a"]))
    with pytest.raises(DagValidationError, match="cycle"):
        validate_plan(plan)


def test_unknown_dependency_is_rejected():
    plan = _plan(_task("a", ["missing"]))
    with pytest.raises(DagValidationError, match="unknown task"):
        validate_plan(plan)


def test_self_dependency_is_rejected():
    plan = _plan(_task("a", ["a"]))
    with pytest.raises(DagValidationError, match="depends on itself"):
        validate_plan(plan)


def test_ready_tasks_only_returns_satisfied_nodes():
    plan = _plan(_task("a"), _task("b", ["a"]), _task("c"))
    assert ready_tasks(plan, {"a": "pending", "b": "pending", "c": "pending"}) == ["a", "c"]
    assert ready_tasks(plan, {"a": "completed", "b": "pending", "c": "completed"}) == ["b"]
    assert ready_tasks(plan, {"a": "failed", "b": "pending", "c": "completed"}) == []


def test_duplicate_task_ids_rejected_by_schema():
    with pytest.raises(Exception):
        _plan(_task("a"), _task("a"))


def test_single_task_plan_is_explicit_fallback():
    plan = single_task_plan("do everything", reason="no runtime", max_tokens=64)
    assert plan.planner_mode == "fallback_single"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].max_tokens == 64
    assert "no runtime" in plan.planner_note


class RecordingEngine:
    def __init__(self) -> None:
        self.kwargs = None

    async def chat(self, messages, **kwargs):
        self.kwargs = kwargs
        return ChatResult(text="ok", model=kwargs.get("model", "m"), runtime="fake")


def test_local_dispatcher_respects_job_token_cap():
    engine = RecordingEngine()
    dispatcher = LocalDispatcher(engine_factory=lambda: engine)
    task = _task("a")
    task.max_tokens = 512

    execution = asyncio.run(
        dispatcher.dispatch(
            task,
            DispatchContext(job_id="job", model="m", max_tokens=48),
            {},
            "local",
        )
    )

    assert execution.status == "completed"
    assert engine.kwargs["max_tokens"] == 48


# --------------------------------------------------------------------------- planner


def test_parse_plan_accepts_fenced_json():
    payload = {
        "objective": "obj",
        "tasks": [{"task_id": "a", "title": "A", "instruction": "do a"}],
        "synthesis": "combine",
    }
    text = "Here is the plan:\n```json\n" + json.dumps(payload) + "\n```\n"
    plan = parse_plan(text)
    assert plan.tasks[0].task_id == "a"
    assert plan.planner_mode == "llm"


def test_parse_plan_rejects_prose_only():
    with pytest.raises(PlanParseError):
        parse_plan("I cannot produce JSON today.")


def test_parse_plan_normalises_model_written_task_ids():
    payload = {
        "objective": "obj",
        "tasks": [
            {"task_id": "Research Models", "title": "Research", "instruction": "research"},
            {"task_id": "2", "title": "Compare", "instruction": "compare", "depends_on": ["Research Models"]},
        ],
    }
    plan = parse_plan(json.dumps(payload))

    assert [task.task_id for task in plan.tasks] == ["research-models", "task-2"]
    assert plan.tasks[1].depends_on == ["research-models"]


def test_parse_plan_deduplicates_ids_and_fills_optional_fields():
    payload = {
        "tasks": [
            {"task_id": "Task 1", "instruction": "first"},
            {"task_id": "task.1", "instruction": "second", "depends_on": "Task 1", "requirements": None},
        ]
    }
    plan = parse_plan(json.dumps(payload))

    assert [task.task_id for task in plan.tasks] == ["task-1", "task-1-2"]
    assert plan.tasks[0].title == "first"
    assert plan.tasks[1].depends_on == ["task-1"]


def test_parse_plan_rejects_invalid_schema():
    with pytest.raises(PlanParseError):
        parse_plan('{"tasks": [{"task_id": "Bad Id!", "title": "", "instruction": ""}]}')


def test_parse_plan_breaks_a_reciprocal_dependency_and_reports_it():
    payload = {
        "tasks": [
            {"task_id": "a", "title": "A", "instruction": "do", "depends_on": ["b"]},
            {"task_id": "b", "title": "B", "instruction": "do", "depends_on": ["a"]},
        ]
    }
    plan = parse_plan(json.dumps(payload))

    assert sum(len(task.depends_on) for task in plan.tasks) == 1
    assert "cycle" in plan.planner_note.lower()
    assert plan.planner_mode == "llm"


def test_parse_plan_breaks_a_longer_cycle():
    payload = {
        "tasks": [
            {"task_id": "a", "title": "A", "instruction": "do", "depends_on": ["b"]},
            {"task_id": "b", "title": "B", "instruction": "do", "depends_on": ["c"]},
            {"task_id": "c", "title": "C", "instruction": "do", "depends_on": ["a"]},
        ]
    }
    plan = parse_plan(json.dumps(payload))
    order = topological_order(plan)

    assert len(order) == 3
    assert "cycle" in plan.planner_note.lower()


class ScriptedEngine:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(text=reply, model=kwargs.get("model", "m"), runtime="fake")


@async_test
async def test_planner_falls_back_when_runtime_unavailable():
    engine = ScriptedEngine([RuntimeUnavailableError("no runtime")])
    planner = Planner(engine_factory=lambda: engine)
    plan = await planner.plan(_request())
    assert plan.planner_mode == "fallback_single"
    assert "unavailable" in plan.planner_note.lower()


@async_test
async def test_planner_repairs_one_invalid_response():
    valid = json.dumps(
        {"tasks": [{"task_id": "a", "title": "A", "instruction": "do a"}]}
    )
    engine = ScriptedEngine(["not json", valid])
    planner = Planner(engine_factory=lambda: engine)
    plan = await planner.plan(_request(prompt="a request long enough to plan"))
    assert plan.planner_mode == "llm"
    assert engine.calls == 2


@async_test
async def test_planner_falls_back_after_two_invalid_responses():
    engine = ScriptedEngine(["nope", "still nope"])
    planner = Planner(engine_factory=lambda: engine)
    plan = await planner.plan(_request(prompt="a request long enough to plan"))
    assert plan.planner_mode == "fallback_single"


class SlowEngine:
    async def chat(self, messages, **kwargs):
        await asyncio.sleep(5)
        raise AssertionError("the timeout should have cancelled this call")


@async_test
async def test_planner_times_out_to_an_honest_single_task():
    planner = Planner(engine_factory=lambda: SlowEngine(), plan_timeout_s=0.05)

    plan = await planner.plan(_request(prompt="a request long enough to plan"))

    assert plan.planner_mode == "fallback_single"
    assert "budget" in plan.planner_note


# --------------------------------------------------------------------------- selector


def test_selector_rejects_missing_capability():
    task = _task("a")
    task.requirements.capabilities = ["vision"]
    worker = WorkerTelemetry(worker_id="w1")
    score = evaluate_worker(task, worker)
    assert not score.feasible
    assert "capabilities" in score.rejection_reason


def test_selector_rejects_offline_and_low_memory():
    task = _task("a")
    task.requirements.min_ram_mb = 4096
    offline = evaluate_worker(task, WorkerTelemetry(worker_id="w1", status="unavailable"))
    assert not offline.feasible
    small = evaluate_worker(
        task,
        WorkerTelemetry(worker_id="w2", ram_available_mb=512.0),
    )
    assert not small.feasible
    assert "insufficient RAM" in small.rejection_reason


def test_selector_prefers_less_loaded_worker_and_is_deterministic():
    task = _task("a")
    idle = WorkerTelemetry(worker_id="idle", workload_pct=5.0, ram_available_mb=4096)
    busy = WorkerTelemetry(worker_id="busy", workload_pct=90.0, ram_available_mb=4096)
    best = select_worker(task, [busy, idle])
    assert best.worker_id == "idle"

    tie_a = WorkerTelemetry(worker_id="a", workload_pct=0.0)
    tie_b = WorkerTelemetry(worker_id="b", workload_pct=0.0)
    assert select_worker(task, [tie_b, tie_a]).worker_id == "a"


def test_selector_marks_unmeasured_components_instead_of_inventing_values():
    score = evaluate_worker(_task("a"), WorkerTelemetry(worker_id="w"))
    assert "reliability" in score.unmeasured
    assert "network" in score.unmeasured
    assert "load" in score.unmeasured
    assert score.components["reliability"] == pytest.approx(0.5)


def test_selector_weights_are_configurable():
    task = _task("a")
    worker = WorkerTelemetry(worker_id="w", workload_pct=80.0)
    default = evaluate_worker(task, worker, SelectionWeights())
    load_heavy = evaluate_worker(task, worker, SelectionWeights(load=1.0))
    assert load_heavy.score < default.score


# --------------------------------------------------------------------------- service


@async_test
async def test_job_runs_dependencies_and_parallel_tasks(isolated_db):
    plan = _plan(_task("a"), _task("b"), _task("c", ["a", "b"]))
    dispatcher = FakeDispatcher()
    service = _service(plan, dispatcher, db=isolated_db)

    job = await service.submit(_request())
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "completed"
    assert job["execution_mode"] == "local"
    assert job["result"] == "final answer"
    assert job["tasks_total"] == 3
    assert job["tasks_completed"] == 3
    states = {task["task_id"]: task["state"] for task in job["tasks"]}
    assert states == {"a": "completed", "b": "completed", "c": "completed"}

    # Independent tasks overlapped; the dependent task saw both outputs.
    assert dispatcher.max_active >= 2
    dep_calls = dict(dispatcher.calls)
    assert set(dep_calls["c"]) == {"a", "b"}


@async_test
async def test_planner_tasks_are_pinned_to_the_job_model(isolated_db):
    """A task must not be dispatched to a worker that cannot serve the job's model."""
    seen: list[str | None] = []

    async def handler(task, context, deps, worker_id):
        seen.append(task.requirements.model)
        return TaskExecution(
            task_id=task.task_id,
            status="completed",
            worker_id=worker_id,
            output="ok",
        )

    service = _service(_plan(_task("a")), FakeDispatcher(handler=handler), db=isolated_db)
    job = await service.submit(_request(model="SmolLM2-360M-Instruct-Q4_K_M"))
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "completed"
    assert seen == ["SmolLM2-360M-Instruct-Q4_K_M"]
    stored = job["plan"]["tasks"][0]["requirements"]["model"]
    assert stored == "SmolLM2-360M-Instruct-Q4_K_M"


@async_test
async def test_single_task_plan_uses_job_token_budget(isolated_db):
    service = _service(_plan(_task("a")), FakeDispatcher(), db=isolated_db)

    job = await service.submit(_request(max_tokens=2048))
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "completed"
    assert job["tasks"][0]["max_tokens"] == 2048


@async_test
async def test_task_retries_then_succeeds(isolated_db):
    attempts = {"count": 0}

    async def flaky(task, context, deps, worker_id):
        attempts["count"] += 1
        if attempts["count"] == 1:
            return TaskExecution(task_id=task.task_id, status="failed", worker_id=worker_id, error="boom")
        return TaskExecution(task_id=task.task_id, status="completed", worker_id=worker_id, output="ok")

    task = _task("a")
    task.retries = 1
    service = _service(_plan(task), FakeDispatcher(handler=flaky), db=isolated_db)

    job = await service.submit(_request())
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "completed"
    assert job["tasks"][0]["attempts"] == 2
    assert attempts["count"] == 2


@async_test
async def test_failed_task_blocks_dependent_and_reports_partial(isolated_db):
    async def handler(task, context, deps, worker_id):
        if task.task_id == "a":
            return TaskExecution(task_id="a", status="failed", worker_id=worker_id, error="cannot do a")
        return TaskExecution(task_id=task.task_id, status="completed", worker_id=worker_id, output="ok")

    plan = _plan(_task("a"), _task("b", ["a"]), _task("c"))
    synth = FakeSynthesizer()
    service = _service(plan, FakeDispatcher(handler=handler), synth, db=isolated_db)

    job = await service.submit(_request())
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "partial"
    states = {task["task_id"]: task["state"] for task in job["tasks"]}
    assert states["a"] == "failed"
    assert states["b"] == "failed"
    assert states["c"] == "completed"
    assert "dependency did not complete" in job["tasks"][1]["error"]
    assert synth.last_missing  # synthesis was told what was missing


@async_test
async def test_all_tasks_failing_marks_job_failed_without_fake_answer(isolated_db):
    async def handler(task, context, deps, worker_id):
        return TaskExecution(task_id=task.task_id, status="failed", worker_id=worker_id, error="nope")

    service = _service(_plan(_task("a")), FakeDispatcher(handler=handler), db=isolated_db)
    job = await service.submit(_request())
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "failed"
    assert job["result"] is None
    assert "nothing could be synthesized" in job["error"]


@async_test
async def test_synthesis_failure_keeps_structured_results(isolated_db):
    service = _service(
        _plan(_task("a")),
        FakeDispatcher(),
        FakeSynthesizer(text=None),
        db=isolated_db,
    )
    job = await service.submit(_request())
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "partial"
    assert job["result"] is None
    assert job["tasks"][0]["output"] == "output of a"
    assert job["error"]


@async_test
async def test_cancel_stops_running_job(isolated_db):
    started = asyncio.Event()
    handler_finished = {"value": False}

    async def slow(task, context, deps, worker_id):
        started.set()
        try:
            await asyncio.sleep(5)
            return TaskExecution(task_id=task.task_id, status="completed", worker_id=worker_id, output="late")
        finally:
            handler_finished["value"] = True

    service = _service(_plan(_task("a")), FakeDispatcher(handler=slow), db=isolated_db)
    job = await service.submit(_request())

    await asyncio.wait_for(started.wait(), timeout=3)
    result = await service.cancel(job["id"])
    assert result["status"] == "cancelled"
    final = service.get_job(job["id"])
    assert final["status"] == "cancelled"
    assert final["tasks"][0]["state"] == "cancelled"

    await asyncio.sleep(0.05)
    assert handler_finished["value"], "in-flight task was not cancelled"


@async_test
async def test_no_worker_produces_explicit_failure(isolated_db):
    class EmptyDispatcher(FakeDispatcher):
        async def list_workers(self):
            return []

    service = _service(_plan(_task("a")), EmptyDispatcher(), db=isolated_db)
    job = await service.submit(_request(execution_mode="distributed"))
    job = await _wait_terminal(service, job["id"])

    assert job["status"] == "failed"
    assert "No worker is available" in job["error"]


def test_interrupted_jobs_are_failed_on_restart(isolated_db):
    isolated_db.create_orchestration_job(
        {
            "id": "job-interrupted",
            "prompt": "p",
            "model_name": "m",
            "status": "running",
            "tasks_total": 1,
        }
    )
    isolated_db.upsert_orchestration_task(
        "job-interrupted",
        {"task_id": "a", "state": "running", "instruction": "x"},
    )

    assert isolated_db.mark_interrupted_jobs() == 1

    job = isolated_db.get_orchestration_job("job-interrupted")
    assert job["status"] == "failed"
    assert "restart" in job["error"]
    assert job["tasks"][0]["state"] == "failed"
    events = isolated_db.list_job_events("job-interrupted")
    assert any(event["event_type"] == "job_interrupted" for event in events)


@async_test
async def test_job_events_are_persisted(isolated_db):
    service = _service(_plan(_task("a")), FakeDispatcher(), db=isolated_db)
    job = await service.submit(_request())
    await _wait_terminal(service, job["id"])

    events = service.events(job["id"])
    types = [event["event_type"] for event in events]
    assert "job_queued" in types
    assert "job_planned" in types
    assert "task_assigned" in types
    assert "task_completed" in types
    assert "job_completed" in types


# --------------------------------------------------------------------------- API


class StubService:
    def __init__(self) -> None:
        self.job = {
            "id": "job-abc",
            "status": "queued",
            "prompt": "hello",
            "tasks": [],
        }

    def list_jobs(self, limit=50, offset=0, status=None):
        return [self.job]

    async def submit(self, request, name=None):
        return self.job

    def get_job(self, job_id):
        return self.job if job_id == "job-abc" else None

    async def cancel(self, job_id):
        return {"status": "cancelled", "job_id": job_id}

    def events(self, job_id, after_id=0, limit=500):
        return []


def test_jobs_api_accepts_async_submission(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.racon.api import routes_jobs
    from backend.racon.api.app import app

    monkeypatch.setattr(routes_jobs, "get_orchestration_service", lambda: StubService())
    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        response = client.post(
            "/api/v1/jobs",
            json={"prompt": "do something useful", "model_name": "m", "execution_mode": "auto"},
        )
        assert response.status_code == 202
        assert response.json()["id"] == "job-abc"

        detail = client.get("/api/v1/jobs/job-abc")
        assert detail.status_code == 200
        assert detail.json()["status"] == "queued"

        assert client.get("/api/v1/jobs/missing").status_code == 404

        cancelled = client.post("/api/v1/jobs/job-abc/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"


def test_jobs_api_validates_prompt_and_parallelism(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.racon.api import routes_jobs
    from backend.racon.api.app import app

    monkeypatch.setattr(routes_jobs, "get_orchestration_service", lambda: StubService())
    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        assert client.post("/api/v1/jobs", json={"prompt": ""}).status_code == 422
        assert (
            client.post(
                "/api/v1/jobs",
                json={"prompt": "x", "max_parallel_tasks": 99},
            ).status_code
            == 422
        )


def test_jobs_api_runs_a_real_orchestration_job(isolated_db, monkeypatch):
    """The HTTP surface must drive the real service, not a canned response."""
    import time

    from fastapi.testclient import TestClient

    from backend.racon.api import routes_jobs
    from backend.racon.api.app import app

    plan = _plan(_task("a"), _task("b", ["a"]))
    service = _service(plan, FakeDispatcher(), db=isolated_db)
    monkeypatch.setattr(routes_jobs, "get_orchestration_service", lambda: service)

    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        submitted = client.post(
            "/api/v1/jobs",
            json={"prompt": "plan and execute", "model_name": "test-model", "execution_mode": "local"},
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]

        deadline = time.time() + 10
        detail = None
        while time.time() < deadline:
            detail = client.get(f"/api/v1/jobs/{job_id}").json()
            if detail["status"] in ("completed", "partial", "failed", "cancelled"):
                break
            time.sleep(0.05)

        assert detail["status"] == "completed", detail
        assert detail["result"] == "final answer"
        assert {task["state"] for task in detail["tasks"]} == {"completed"}

        listing = client.get("/api/v1/jobs").json()
        assert any(item["id"] == job_id for item in listing)


class FakeStreamingEngine:
    def __init__(self) -> None:
        self.max_tokens = None

    async def chat(self, *args, **kwargs):
        raise AssertionError("streaming synthesis must use chat_stream")

    async def chat_stream(self, messages, **kwargs):
        self.max_tokens = kwargs.get("max_tokens")
        for token in ("Hello", " ", "world"):
            yield ChatChunk(delta=token)
        yield ChatChunk(
            done=True,
            finish_reason="stop",
            usage=ChatUsage(prompt_tokens=3, completion_tokens=3),
        )


@async_test
async def test_synthesizer_streams_tokens_and_uses_full_budget():
    engine = FakeStreamingEngine()
    synthesizer = Synthesizer(engine_factory=lambda: engine)
    request = _request(max_tokens=2048)
    plan = _plan(_task("a"))
    executions = {
        "a": TaskExecution(
            task_id="a",
            status="completed",
            worker_id=LOCAL_ORIGIN,
            output="task output",
        )
    }
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    result = await synthesizer.synthesize(
        request, plan, executions, on_delta=on_delta
    )

    assert result.text == "Hello world"
    assert deltas == ["Hello", " ", "world"]
    assert engine.max_tokens == 2048


def test_job_stream_replays_terminal_job(isolated_db, monkeypatch):
    """A late subscriber receives the job snapshot and the final answer."""
    import time

    from fastapi.testclient import TestClient

    from backend.racon.api import routes_jobs
    from backend.racon.api.app import app

    plan = _plan(_task("a"))
    service = _service(plan, FakeDispatcher(), db=isolated_db)
    monkeypatch.setattr(routes_jobs, "get_orchestration_service", lambda: service)

    with TestClient(app, headers={"X-RACON-TOKEN": "racon-local-dev-token"}) as client:
        submitted = client.post(
            "/api/v1/jobs",
            json={
                "prompt": "plan and execute",
                "model_name": "test-model",
                "execution_mode": "local",
                "max_tokens": 2048,
            },
        )
        assert submitted.status_code == 202
        job_id = submitted.json()["id"]

        deadline = time.time() + 10
        while time.time() < deadline:
            detail = client.get(f"/api/v1/jobs/{job_id}").json()
            if detail["status"] in ("completed", "partial", "failed", "cancelled"):
                break
            time.sleep(0.05)

        assert detail["status"] == "completed", detail

        with client.stream("GET", f"/api/v1/jobs/{job_id}/stream") as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

    frames = [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith("data:")
    ]
    assert any(frame.get("type") == "job" for frame in frames)
    assert frames[-1]["type"] == "done"
    assert frames[-1]["job"]["result"] == "final answer"
