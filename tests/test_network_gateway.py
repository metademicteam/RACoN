"""Intent, broker, task progress, and network-gateway streaming tests."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from backend.racon.network.broker import DeltaBroker
from backend.racon.network.intent import choose_intent
from racon.coordinator.tasks import TaskManager
from protocols import TaskType, generate_keypair


class FakeNode:
    node_id = "node-test"
    models = ["model-a"]
    features = ["text_generation", "streaming_chat"]
    active_tasks = []
    cpu_util_pct = 2.0
    ram_available_mb = 8000.0
    capabilities = SimpleNamespace(ram_available_mb=8000.0)


def _request(**overrides):
    values = {
        "messages": [{"role": "user", "content": "Say hello."}],
        "model": "racon",
        "max_tokens": 64,
        "temperature": 0.7,
        "top_p": 0.95,
        "top_k": None,
        "stop": None,
        "tools": None,
        "tool_choice": None,
        "response_format": None,
        "seed": None,
        "presence_penalty": None,
        "frequency_penalty": None,
        "parallel_tool_calls": None,
        "stream": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


async def _collect(response):
    parts = []
    async for part in response.body_iterator:
        if isinstance(part, str):
            part = part.encode()
        parts.append(part)
    return b"".join(parts).decode()


def test_intent_quick_chat_is_single():
    decision = choose_intent("racon", [{"role": "user", "content": "Hi, how are you?"}])
    assert decision.mode == "single"


def test_intent_complex_prompt_is_task():
    prompt = (
        "Compare CPU and GPU inference for a small team. Research the trade-offs, "
        "analyze cost and latency, and recommend a plan with several deliverables."
    )
    decision = choose_intent("racon", [{"role": "user", "content": prompt}])
    assert decision.mode == "task"


def test_intent_tools_force_single():
    messages = [{"role": "user", "content": "Read the file and fix the bug."}]
    decision = choose_intent("racon-task", messages, tools=[{"type": "function"}])
    assert decision.mode == "single"
    assert "tool" in decision.reason


def test_intent_aliases_force_mode():
    assert choose_intent("racon-single", [{"role": "user", "content": "compare"}]).mode == "single"
    assert choose_intent("racon-task", [{"role": "user", "content": "hi"}]).mode == "task"


def test_prefixed_aliases_pin_the_model():
    single = choose_intent("racon-single:qwen2.5:0.5b", [{"role": "user", "content": "hi"}])
    task = choose_intent("racon-task:qwen2.5:0.5b", [{"role": "user", "content": "compare"}])
    assert single.mode == "single"
    assert single.model == "qwen2.5:0.5b"
    assert task.mode == "task"
    assert task.model == "qwen2.5:0.5b"


def test_explicit_model_stays_single():
    decision = choose_intent("some-model", [{"role": "user", "content": "hi"}])
    assert decision.mode == "single"
    assert decision.model == "some-model"


def test_broker_replay_and_order():
    async def scenario():
        broker = DeltaBroker()
        queue, replay = await broker.subscribe("task:1")
        assert replay == []
        broker.publish("task:1", {"seq": 0, "text": "a"})
        broker.publish("task:1", {"seq": 1, "text": "b"})
        first = await queue.get()
        second = await queue.get()
        assert (first["seq"], second["seq"]) == (0, 1)

        late, late_replay = await broker.subscribe("task:1")
        assert [item["seq"] for item in late_replay] == [0, 1]
        broker.publish("task:1", {"seq": 2, "text": "c"})
        assert (await late.get())["seq"] == 2

    asyncio.run(scenario())


def test_task_manager_rejects_duplicate_progress():
    private, _public = generate_keypair()
    manager = TaskManager(private)
    envelope = manager.create_task("node-test", TaskType.CHAT_COMPLETION, "model-a", {})
    assert manager.record_progress("node-test", envelope.task_id, 0, {"content": "a"}) is True
    assert manager.record_progress("node-test", envelope.task_id, 0, {"content": "a"}) is False
    assert manager.record_progress("node-test", envelope.task_id, 2, {"content": "c"}) is True
    assert manager.record_progress("node-test", envelope.task_id, 1, {"content": "b"}) is False
    assert [item["seq"] for item in manager.get_progress(envelope.task_id)] == [0, 2]
    assert manager.record_progress("node-other", envelope.task_id, 3, {"content": "x"}) is False


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    async def open_stream(self, **kwargs):
        self.calls.append(kwargs)
        yield {"event": "started", "task_id": "task-x", "node_id": kwargs["worker_id"]}
        yield {"event": "delta", "delta": {"content": "Hello "}}
        yield {"event": "delta", "delta": {"content": "from the worker"}}
        yield {
            "event": "done",
            "state": "completed",
            "result_data": {
                "response": "Hello from the worker",
                "usage": {"prompt_tokens": 3, "completion_tokens": 4, "reported": True},
                "finish_reason": "stop",
            },
            "execution_time_ms": 12.0,
        }


def test_gateway_single_stream(monkeypatch):
    from backend.racon.network import gateway as gateway_module

    monkeypatch.setattr(gateway_module, "online_workers", lambda: [FakeNode()])
    gw = gateway_module.NetworkGateway()
    gw._dispatcher = FakeDispatcher()

    async def no_local():
        return []

    monkeypatch.setattr(gw, "_local_model_ids", no_local)
    response = asyncio.run(gw.handle_chat(_request()))
    assert response.status_code == 200
    body = asyncio.run(_collect(response))
    assert "Hello " in body
    assert "from the worker" in body
    assert "data: [DONE]" in body
    assert '"mode":"single"' in body


def test_gateway_single_non_stream(monkeypatch):
    from backend.racon.network import gateway as gateway_module

    monkeypatch.setattr(gateway_module, "online_workers", lambda: [FakeNode()])
    gw = gateway_module.NetworkGateway()
    gw._dispatcher = FakeDispatcher()

    async def no_local():
        return []

    monkeypatch.setattr(gw, "_local_model_ids", no_local)
    result = asyncio.run(gw.handle_chat(_request(stream=False)))
    assert result["choices"][0]["message"]["content"] == "Hello from the worker"
    assert result["racon"]["mode"] == "single"


def test_gateway_no_target_returns_503(monkeypatch):
    from backend.racon.network import gateway as gateway_module

    monkeypatch.setattr(gateway_module, "online_workers", lambda: [])
    gw = gateway_module.NetworkGateway()

    async def no_local():
        return []

    monkeypatch.setattr(gw, "_local_model_ids", no_local)
    response = asyncio.run(gw.handle_chat(_request()))
    assert response.status_code == 503
    assert json.loads(response.body)["error"]["code"] == "no_worker"


class FakeService:
    def __init__(self, items):
        self.queue = asyncio.Queue()
        for item in items:
            self.queue.put_nowait(item)
        self.cancelled = []

    async def submit_stream(self, request, name=None):
        return {"id": "job-test", "status": "queued"}, self.queue, []

    def unsubscribe_job_stream(self, job_id, queue):
        return None

    async def cancel(self, job_id):
        self.cancelled.append(job_id)
        return {"status": "cancelled"}


def test_worker_progress_route_publishes_delta(monkeypatch):
    from fastapi.testclient import TestClient

    from backend.racon.api import routes_worker
    from backend.racon.api.app import app
    from backend.racon.network.broker import get_delta_broker, task_channel

    class FakeTasks:
        def __init__(self):
            self.calls = []

        def record_progress(self, node_id, task_id, seq, delta, finish_reason=None):
            self.calls.append((node_id, task_id, seq, delta, finish_reason))
            return True

    class FakeRegistry:
        def authenticate_session(self, node_id, token):
            return node_id == "node-test" and token == "session-token-123"

    class FakeState:
        registry = FakeRegistry()
        tasks = FakeTasks()

    state = FakeState()
    monkeypatch.setattr(routes_worker, "get_coordinator_state", lambda: state)
    get_delta_broker().clear()

    with TestClient(app) as client:
        accepted = client.post(
            "/tasks/progress",
            json={
                "node_id": "node-test",
                "task_id": "task-progress-1",
                "session_token": "session-token-123",
                "seq": 0,
                "delta": {"content": "hello"},
            },
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["accepted"] is True

        rejected = client.post(
            "/tasks/progress",
            json={
                "node_id": "node-test",
                "task_id": "task-progress-1",
                "session_token": "wrong-session-token-123",
                "seq": 1,
                "delta": {"content": "nope"},
            },
        )
        assert rejected.status_code == 401

    history = get_delta_broker().history(task_channel("task-progress-1"))
    assert len(history) == 1
    assert history[0]["delta"]["content"] == "hello"
    assert state.tasks.calls == [("node-test", "task-progress-1", 0, {"content": "hello"}, None)]


def test_gateway_task_stream(monkeypatch):
    from backend.racon.network import gateway as gateway_module

    monkeypatch.setattr(gateway_module, "online_workers", lambda: [FakeNode()])
    service = FakeService(
        [
            {
                "event": "task_delta",
                "task_id": "task-1",
                "title": "Research",
                "delta": {"content": "worker progress"},
            },
            {"event": "synthesis_delta", "delta": "Merged "},
            {"event": "synthesis_delta", "delta": "answer."},
            {
                "event": "job_done",
                "status": "completed",
                "result": "Merged answer.",
                "error": None,
                "missing": [],
            },
        ]
    )
    monkeypatch.setattr(gateway_module, "get_orchestration_service", lambda: service)
    gw = gateway_module.NetworkGateway()

    async def no_local():
        return []

    monkeypatch.setattr(gw, "_local_model_ids", no_local)
    response = asyncio.run(
        gw.handle_chat(
            _request(
                model="racon-task",
                messages=[{"role": "user", "content": "Research and compare several options."}],
            )
        )
    )
    body = asyncio.run(_collect(response))
    assert "worker progress" in body
    assert "Merged " in body
    assert '"mode":"task"' in body
    assert '"status":"completed"' in body
    assert "data: [DONE]" in body
