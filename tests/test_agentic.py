"""Agentic scheduler tests: decision, placement, tool planning, execution, and merge."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from backend.racon.agentic import service as service_module
from backend.racon.agentic import selection as selection_module
from backend.racon.agentic.decision import decide, is_task_candidate
from backend.racon.agentic.schemas import AgentDecision, AgentTask
from backend.racon.agentic.service import AgenticService


class FakeSettings:
    TOGETHER_ENABLED = True
    TOGETHER_API_KEY = "test-key"
    TOGETHER_BASE_URL = "https://example.invalid/v1"
    TOGETHER_AGENT_MODEL = "agent-model"
    TOGETHER_REASONING_EFFORT = "none"
    TOGETHER_TIMEOUT_S = 10.0
    TOGETHER_DECISION_MAX_TOKENS = 16
    TOGETHER_AGENT_MAX_TOKENS = 512
    RACON_AGENTIC_MAX_TASKS = 4
    RACON_AGENTIC_MAX_PARALLEL = 2
    RACON_AGENTIC_MERGE_MAX_TOKENS = 128
    RACON_AGENTIC_WORKER_MODEL = ""


class FakeClient:
    def __init__(self, chat_responses=None, stream_chunks=None):
        self.chat_responses = list(chat_responses or [])
        self.stream_chunks = list(stream_chunks or [])
        self.chat_calls = []
        self.stream_calls = []

    async def chat(self, messages, **kwargs):
        self.chat_calls.append({"messages": messages, **kwargs})
        if not self.chat_responses:
            return {"text": "", "tool_calls": [], "usage": {}, "finish_reason": "stop"}
        return self.chat_responses.pop(0)

    async def chat_stream(self, messages, **kwargs):
        self.stream_calls.append({"messages": messages, **kwargs})
        for chunk in self.stream_chunks:
            yield chunk


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    async def open_stream(self, **kwargs):
        self.calls.append(kwargs)
        index = len(self.calls)
        yield {"event": "started", "task_id": f"worker-task-{index}"}
        yield {"event": "delta", "delta": {"content": "working "}}
        yield {"event": "delta", "delta": {"content": "output"}}
        yield {
            "event": "done",
            "state": "completed",
            "result_data": {
                "response": f"output from {kwargs['worker_id']}",
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "reported": True,
                },
                "finish_reason": "stop",
            },
            "execution_time_ms": 15.0,
        }


def _request(**overrides):
    values = {
        "messages": [{"role": "user", "content": "hello"}],
        "model": "racon",
        "max_tokens": 256,
        "temperature": 0.7,
        "top_p": 0.95,
        "stop": None,
        "stream": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _task_decision() -> AgentDecision:
    return AgentDecision(
        mode="task",
        confidence=0.9,
        reason="needs multiple deliverables",
        source="together",
        model="decision-model",
    )


def _worker(worker_id: str, model: str, **overrides):
    values = {
        "node_id": worker_id,
        "models": [model],
        "features": ["text_generation", "streaming_chat"],
        "active_tasks": [],
        "cpu_util_pct": 5.0,
        "ram_available_mb": 8000.0,
        "capabilities": SimpleNamespace(gpu_name=None, gpu_vram_mb=None, ram_available_mb=8000.0),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_decision_model_parses_json():
    client = FakeClient(
        chat_responses=[
            {
                "text": '{"mode":"task","confidence":0.93,"reason":"two independent sections"}',
                "tool_calls": [],
                "usage": {"prompt_tokens": 5, "completion_tokens": 8, "reported": True},
                "finish_reason": "stop",
            }
        ]
    )
    decision = asyncio.run(
        decide(
            client,
            model="decision-model",
            messages=[
                {
                    "role": "user",
                    "content": "Build a small REST API with tests and deploy it.",
                }
            ],
        )
    )
    assert decision.mode == "task"
    assert decision.source == "minimax-router"
    assert decision.confidence == 0.93


def test_decision_invalid_json_uses_heuristic():
    client = FakeClient(chat_responses=[{"text": "not json", "tool_calls": [], "usage": {}}])
    decision = asyncio.run(
        decide(
            client,
            model="decision-model",
            messages=[
                {
                    "role": "user",
                    "content": "Build a small REST API with tests and deploy it.",
                }
            ],
        )
    )
    assert decision.source == "heuristic"


def test_minimax_router_handles_simple_chat():
    client = FakeClient(
        chat_responses=[
            {
                "text": "chat",
                "tool_calls": [],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "reported": True},
                "finish_reason": "stop",
            }
        ]
    )
    decision = asyncio.run(
        decide(
            client,
            model="agent-model",
            reasoning={"effort": "none"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a coding agent. Build and implement codebase features, "
                        "refactor repositories, and deploy production-ready services."
                    ),
                },
                {
                    "role": "user",
                    "content": "hey can you suggest new 2026 hollywood thriller movies",
                },
            ],
        )
    )
    assert decision.mode == "chat"
    assert decision.source == "minimax-router"
    assert len(client.chat_calls) == 1
    assert client.chat_calls[0]["reasoning"] == {"effort": "none"}
    # The system prompt's coding language must not be routed on.
    assert "codebase" not in client.chat_calls[0]["messages"][1]["content"]


def test_task_gate_only_accepts_long_horizon_or_build_work():
    movie, _ = is_task_candidate(
        [{"role": "user", "content": "suggest new 2026 hollywood thriller movies"}]
    )
    build, _ = is_task_candidate(
        [{"role": "user", "content": "build me a todo app with React and a backend"}]
    )
    short_compare, _ = is_task_candidate(
        [{"role": "user", "content": "compare CPU and GPU inference and write a report"}]
    )
    long_research, _ = is_task_candidate(
        [
            {
                "role": "user",
                "content": (
                    "Research the trade-offs of CPU and GPU inference for a small team. "
                    + "Include a comprehensive report with several sections and deliverables. "
                    * 8
                ),
            }
        ]
    )
    assert movie is False
    assert build is True
    assert short_compare is False
    assert long_research is True


def test_selection_prefers_measured_fast_worker():
    selection_module.get_worker_telemetry().reset()
    fast = _worker("node-fast", "model-a")
    slow = _worker("node-slow", "model-b")
    original = selection_module.online_workers
    selection_module.online_workers = lambda: [fast, slow]
    try:
        selection_module.get_worker_telemetry().record_claim("node-fast", 40.0)
        selection_module.get_worker_telemetry().record_claim("node-slow", 1200.0)
        candidate, model, error = selection_module.resolve_placement(
            requested_worker=None,
            requested_model=None,
            preferred_model="",
        )
        assert error is None
        assert candidate.node_id == "node-fast"
        assert model == "model-a"
    finally:
        selection_module.online_workers = original
        selection_module.get_worker_telemetry().reset()


def test_model_rank_avoids_tiny_completion_models():
    ranked = sorted(
        ["smollm2:135m", "qwen2.5:0.5b", "qwen2.5:1.5b", "phi3.5:latest"],
        key=selection_module._model_rank,
    )
    assert ranked[0] == "qwen2.5:0.5b"
    assert ranked[-1] in ("smollm2:135m", "phi3.5:latest")


def test_dependency_cycle_is_broken():
    tasks = [
        AgentTask(task_id="a", title="A", instruction="do a", depends_on=["b"]),
        AgentTask(task_id="b", title="B", instruction="do b", depends_on=["a"]),
    ]
    notes = service_module._normalise_dependencies(tasks)
    assert notes
    assert all(not task.depends_on for task in tasks)


def test_task_plan_dispatches_in_parallel_and_merges(monkeypatch):
    dispatcher = FakeDispatcher()
    client = FakeClient(
        chat_responses=[
            {
                "text": "I will split this into two tasks.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "dispatch_task",
                            "arguments": json.dumps(
                                {
                                    "task_id": "analysis",
                                    "title": "Analyze A",
                                    "instruction": "Analyze A carefully.",
                                    "worker_id": "node-a",
                                    "model": "model-a",
                                }
                            ),
                        },
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "dispatch_task",
                            "arguments": json.dumps(
                                {
                                    "task_id": "review",
                                    "title": "Review B",
                                    "instruction": "Review B carefully.",
                                    "worker_id": "node-b",
                                    "model": "model-b",
                                }
                            ),
                        },
                    },
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "reported": True},
                "finish_reason": "tool_calls",
            },
            {
                "text": "Plan ready.",
                "tool_calls": [],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "reported": True},
                "finish_reason": "stop",
            },
        ],
        stream_chunks=[
            {"content": "merged "},
            {"content": "answer"},
            {"usage": {"prompt_tokens": 8, "completion_tokens": 9, "reported": True}},
            {"done": True},
        ],
    )
    service = AgenticService(
        settings=FakeSettings(), client=client, dispatcher=dispatcher
    )
    environment = {
        "workers": [
            {"worker_id": "node-a", "models": ["model-a"], "active_tasks": 0, "score": 0.9},
            {"worker_id": "node-b", "models": ["model-b"], "active_tasks": 0, "score": 0.8},
        ],
        "worker_count": 2,
    }
    monkeypatch.setattr(service_module, "build_environment", lambda preferred: environment)

    def fake_resolve(requested_worker=None, requested_model=None, preferred_model="", exclude=None):
        node = requested_worker or "node-a"
        model = requested_model or "model-a"
        return (
            SimpleNamespace(
                node_id=node,
                score=0.9,
                latency_ms=20.0,
                active_tasks=0,
            ),
            model,
            None,
        )

    monkeypatch.setattr(service_module, "resolve_placement", fake_resolve)

    result = asyncio.run(
        service.complete(
            _task_decision(),
            _request(),
            [{"role": "user", "content": "Analyze A and review B."}],
        )
    )
    assert result["text"] == "merged answer"
    assert result["effective_mode"] == "task"
    assert len(result["tasks"]) == 2
    assert {task["status"] for task in result["tasks"]} == {"completed"}
    assert len(dispatcher.calls) == 2
    assert {call["worker_id"] for call in dispatcher.calls} == {"node-a", "node-b"}
    assert result["usage"]["completion_tokens"] >= 9


def test_chat_mode_streams_agent_answer():
    client = FakeClient(
        stream_chunks=[
            {"content": "Hello"},
            {"content": "!"},
            {"usage": {"prompt_tokens": 3, "completion_tokens": 2, "reported": True}},
            {"done": True},
        ]
    )
    service = AgenticService(settings=FakeSettings(), client=client)
    decision = AgentDecision(mode="chat", confidence=0.99, reason="greeting")
    result = asyncio.run(
        service.complete(decision, _request(), [{"role": "user", "content": "hi"}])
    )
    assert result["text"] == "Hello!"
    assert result["effective_mode"] == "chat"
    assert result["tasks"] == []
    assert client.stream_calls[0]["reasoning"] == {"effort": "none"}


def test_no_workers_falls_back_to_direct_chat(monkeypatch):
    client = FakeClient(
        stream_chunks=[
            {"content": "direct answer"},
            {"usage": {"prompt_tokens": 1, "completion_tokens": 2, "reported": True}},
            {"done": True},
        ]
    )
    service = AgenticService(settings=FakeSettings(), client=client)
    monkeypatch.setattr(
        service_module, "build_environment", lambda preferred: {"workers": [], "worker_count": 0}
    )

    result = asyncio.run(
        service.complete(
            _task_decision(),
            _request(),
            [{"role": "user", "content": "Build a small report."}],
        )
    )
    assert result["text"] == "direct answer"
    assert result["effective_mode"] == "chat"


def test_agent_alias_selects_agentic_intent():
    from backend.racon.network.intent import choose_intent

    decision = choose_intent("racon-agent", [{"role": "user", "content": "Hi"}])
    assert decision.mode == "agent"


def test_agent_messages_preserves_tool_results():
    from backend.racon.agentic.service import _agent_messages

    normalized = _agent_messages(
        [
            {"role": "user", "content": "Read a.txt"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "hello world",
            },
        ]
    )
    assert [item["role"] for item in normalized] == ["user", "assistant", "tool"]
    assert normalized[-1]["tool_call_id"] == "call_1"
    assert normalized[-1]["content"] == "hello world"


def test_chat_stream_forwards_tool_calls_and_assembles_them():
    client = FakeClient(
        stream_chunks=[
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path"'},
                    }
                ]
            },
            {
                "tool_calls": [
                    {"index": 0, "function": {"arguments": ':"a.txt"}'}}
                ]
            },
            {"done": True},
        ]
    )
    service = AgenticService(settings=FakeSettings(), client=client)
    decision = AgentDecision(mode="chat", confidence=0.9, reason="tool loop")
    request = _request(
        tools=[{"type": "function", "function": {"name": "read_file"}}],
        tool_choice="auto",
        parallel_tool_calls=False,
    )
    result = asyncio.run(
        service.complete(decision, request, [{"role": "user", "content": "read a.txt"}])
    )
    assert result["text"] == ""
    assert result["finish_reason"] == "tool_calls"
    assert len(result["tool_calls"]) == 1
    assert result["tool_calls"][0]["function"]["name"] == "read_file"
    assert result["tool_calls"][0]["function"]["arguments"] == '{"path":"a.txt"}'
    assert client.stream_calls[0]["tools"] == request.tools
    assert client.stream_calls[0]["parallel_tool_calls"] is False


def test_agent_sse_forwards_tool_calls_with_tool_finish_reason():
    from backend.racon.network.gateway import NetworkGateway

    class FakeToolService:
        async def stream(self, decision, request, messages):
            yield {
                "event": "tool_calls",
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path":"a.txt"}'},
                    }
                ],
            }
            yield {
                "event": "done",
                "text": "",
                "tool_calls": True,
                "usage": {},
                "effective_mode": "chat",
                "tasks": [],
                "degraded": False,
            }

    gateway = NetworkGateway()

    async def collect() -> str:
        parts = []
        async for part in gateway._agent_sse(
            FakeToolService(),
            AgentDecision(mode="chat", confidence=0.9, reason="tool loop"),
            _request(tools=[{"type": "function"}]),
            [{"role": "user", "content": "read a.txt"}],
            None,
        ):
            parts.append(part if isinstance(part, bytes) else part.encode())
        return b"".join(parts).decode()

    body = asyncio.run(collect())
    assert '"tool_calls"' in body
    assert '"finish_reason":"tool_calls"' in body
    assert "data: [DONE]" in body


def test_agent_sse_shows_visible_status_and_keepalive():
    from backend.racon.network.gateway import NetworkGateway

    class FakeStreamingService:
        async def stream(self, decision, request, messages):
            yield {
                "event": "visible_status",
                "message": "I'm planning this with 1 worker node.",
            }
            yield {"event": "heartbeat"}
            yield {
                "event": "done",
                "text": "final merged answer",
                "usage": {},
                "effective_mode": "task",
                "tasks": [],
                "merge_model": "agent-model",
                "degraded": False,
            }

    gateway = NetworkGateway()

    async def collect() -> str:
        parts = []
        async for part in gateway._agent_sse(
            FakeStreamingService(),
            _task_decision(),
            _request(),
            [{"role": "user", "content": "build a small app"}],
            None,
        ):
            parts.append(part if isinstance(part, bytes) else part.encode())
        return b"".join(parts).decode()

    body = asyncio.run(collect())
    assert "I'm planning this with 1 worker node." in body
    assert ": keepalive" in body
    assert "final merged answer" in body
    assert body.count("final merged answer") == 1
    assert "data: [DONE]" in body


def test_merge_retries_when_reasoning_consumes_budget():
    from backend.racon.agentic.merge import merge_outputs
    from backend.racon.agentic.schemas import TaskOutcome

    class FakeMergeClient:
        def __init__(self):
            self.budgets = []

        async def chat_stream(self, *args, **kwargs):
            if False:
                yield {}

        async def chat(self, *args, **kwargs):
            self.budgets.append(kwargs["max_tokens"])
            if len(self.budgets) == 1:
                return {
                    "text": "",
                    "tool_calls": [],
                    "finish_reason": "length",
                    "usage": {"completion_tokens": 512, "reported": True},
                }
            return {
                "text": "merged answer",
                "tool_calls": [],
                "finish_reason": "stop",
                "usage": {"completion_tokens": 40, "reported": True},
            }

    client = FakeMergeClient()
    outcomes = [
        TaskOutcome(
            task_id="a", title="A", status="completed", output="out a", worker_id="w"
        ),
        TaskOutcome(
            task_id="b", title="B", status="completed", output="out b", worker_id="w"
        ),
    ]
    result = asyncio.run(
        merge_outputs(
            client,
            model="agent-model",
            messages=[{"role": "user", "content": "compare a and b"}],
            outcomes=outcomes,
            max_tokens=512,
        )
    )
    assert result.text == "merged answer"
    assert client.budgets == [512, 1536]


def test_worker_local_engine_fallback_streams(monkeypatch):
    """Regression: the async engine fallback used `buffer` without `nonlocal`.

    With Ollama down, every worker task failed with UnboundLocalError before reporting
    any output. The fallback must stream through the local engine instead.
    """
    from racon.agent import worker as worker_module

    agent = worker_module.RaconAgent(coordinator_url="http://127.0.0.1:9")
    monkeypatch.setattr(
        agent,
        "_ollama_chat_stream",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            worker_module._OllamaUnavailable("Ollama unreachable")
        ),
    )

    class FakeChunk:
        def __init__(self, delta="", tool_calls=None, usage=None, finish_reason=None):
            self.delta = delta
            self.tool_calls = tool_calls
            self.usage = usage
            self.finish_reason = finish_reason

    class FakeEngine:
        runtime_name = "fake"

        async def chat_stream(self, messages, **kwargs):
            yield FakeChunk(delta="hello ")
            yield FakeChunk(delta="world", finish_reason="stop")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return SimpleNamespace(status_code=200)

    monkeypatch.setattr(
        "racon.services.engine_service.get_engine", lambda: FakeEngine()
    )
    monkeypatch.setattr(worker_module.httpx, "AsyncClient", FakeAsyncClient)

    result, error = agent._chat_completion(
        "model-a",
        {
            "messages": [{"role": "user", "content": "Say hello."}],
            "options": {"max_tokens": 16},
        },
        "task-fallback-test",
    )
    assert error is None
    assert result["response"] == "hello world"
    assert result["finish_reason"] == "stop"
