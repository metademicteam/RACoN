"""OpenAI compatibility: request fields, tool calls, SSE and error shape."""
import json

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from backend.racon.api.app import app
from racon.runtime.tool_calls import normalise_tool_calls
from racon.runtime.types import ChatChunk, ChatResult, ChatUsage

ADMIN_HEADERS = {"X-RACON-TOKEN": "racon-local-dev-token"}


class CapturingEngine:
    runtime_name = "llama_cpp"

    def __init__(self) -> None:
        self.messages = None
        self.kwargs = None

    async def chat(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        return ChatResult(
            text='{"ok": true}',
            model=kwargs.get("model", "model-a"),
            runtime="llama_cpp",
            usage=ChatUsage(4, 3),
            duration_ms=10.0,
            finish_reason="stop",
        )

    async def chat_stream(self, messages, **kwargs):
        self.messages = messages
        self.kwargs = kwargs
        yield ChatChunk(
            tool_calls=[
                {
                    "index": 0,
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"q":"x"}'},
                }
            ]
        )
        yield ChatChunk(done=True, finish_reason="tool_calls", usage=ChatUsage(2, 5))


@pytest.fixture(scope="module")
def client():
    with TestClient(app, headers=ADMIN_HEADERS) as c:
        yield c


@pytest.fixture(scope="module")
def anon_client():
    with TestClient(app) as c:
        yield c


def _patch_engine(monkeypatch, engine):
    from backend.racon.api import routes_engine

    monkeypatch.setattr(routes_engine, "get_engine", lambda: engine)


def test_tools_and_response_format_reach_the_runtime(client, monkeypatch):
    engine = CapturingEngine()
    _patch_engine(monkeypatch, engine)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "model-a",
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": "be terse"}]},
                {"role": "user", "content": "look it up"},
            ],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            "tool_choice": "auto",
            "response_format": {"type": "json_object"},
            "seed": 7,
            "parallel_tool_calls": False,
        },
    )
    assert response.status_code == 200, response.text
    assert engine.kwargs["tools"][0]["function"]["name"] == "lookup"
    assert engine.kwargs["tool_choice"] == "auto"
    assert engine.kwargs["response_format"] == {"type": "json_object"}
    assert engine.kwargs["seed"] == 7
    assert engine.kwargs["parallel_tool_calls"] is False
    assert engine.messages[0].content == [{"type": "text", "text": "be terse"}]


def test_streamed_tool_calls_use_openai_chunk_shape(client, monkeypatch):
    engine = CapturingEngine()
    _patch_engine(monkeypatch, engine)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "model-a",
            "messages": [{"role": "user", "content": "use a tool"}],
            "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
            "stream": True,
        },
    ) as response:
        body = "".join(response.iter_text())

    assert response.status_code == 200
    assert '"tool_calls"' in body
    assert '"name":"lookup"' in body
    assert '"finish_reason":"tool_calls"' in body
    assert "data: [DONE]" in body


def test_invalid_n_returns_openai_error_shape(client):
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "model-a",
            "messages": [{"role": "user", "content": "hi"}],
            "n": 2,
        },
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["code"] == "invalid_request"


def test_empty_messages_returns_openai_error(client):
    response = client.post(
        "/v1/chat/completions",
        json={"model": "model-a", "messages": []},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_messages"


def test_ollama_tool_calls_are_normalised_to_openai_shape():
    raw = [
        {
            "id": "call_abc",
            "function": {"index": 0, "name": "get_weather", "arguments": {"city": "Paris"}},
        }
    ]
    normalised = normalise_tool_calls(raw)
    assert normalised[0]["index"] == 0
    assert normalised[0]["id"] == "call_abc"
    assert normalised[0]["type"] == "function"
    assert normalised[0]["function"]["name"] == "get_weather"
    assert normalised[0]["function"]["arguments"] == '{"city": "Paris"}'
    assert normalise_tool_calls(normalised) == normalised


def test_api_key_requests_reach_the_network_gateway(anon_client, monkeypatch):
    created = anon_client.post(
        "/api/v1/admin/keys",
        json={"name": "compat", "rate_limit_per_min": 60},
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 200, created.text
    token = created.json()["key"]

    called = {}

    class FakeGateway:
        async def handle_chat(self, request, *, api_key_id=None):
            called["model"] = request.model
            called["api_key_id"] = api_key_id
            return JSONResponse(
                status_code=200,
                content={
                    "id": "chatcmpl-test",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "network answer"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

    from backend.racon.network import gateway as gateway_module

    monkeypatch.setattr(gateway_module, "get_network_gateway", lambda: FakeGateway())

    response = anon_client.post(
        "/v1/chat/completions",
        json={"model": "racon", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "network answer"
    assert called["model"] == "racon"
    assert called["api_key_id"]
