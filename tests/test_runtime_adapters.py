"""Contract tests for the unified runtime adapters.

Both adapters are exercised against mocked HTTP transports, so the suite needs no
llama.cpp binary and no Ollama daemon.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from racon.runtime.llamacpp import LlamaCppRuntime, build_server_command, describe_exit
from racon.runtime.ollama import OllamaRuntime
from racon.runtime.registry import create_runtime, runtime_capabilities
from racon.runtime.types import (
    ChatMessage,
    GenerationOptions,
    InferenceCancelledError,
    ModelNotLoadedError,
    RuntimeUnavailableError,
)


class _FakeProcess:
    """Stand-in for subprocess.Popen.

    ``alive_calls`` lets a test model a process that is alive when the request starts and
    dies while it is in flight.
    """

    def __init__(self, returncode=None, pid=4242, alive_calls=None):
        self._returncode = returncode
        self.pid = pid
        self._alive_calls = alive_calls
        self._polls = 0

    def poll(self):
        self._polls += 1
        if self._alive_calls is not None and self._polls <= self._alive_calls:
            return None
        return self._returncode

    @property
    def returncode(self):
        return self._returncode

    def terminate(self):
        self._returncode = 0

    def kill(self):
        self._returncode = -9

    def wait(self, timeout=None):
        return self._returncode or 0


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def test_build_server_command_includes_safety_flags():
    cmd = build_server_command(
        "llama-server",
        "model.gguf",
        "127.0.0.1",
        9999,
        context_size=8192,
        n_gpu_layers=24,
        threads=6,
        api_key="secret",
    )
    assert cmd[0] == "llama-server"
    assert "-m" in cmd and "model.gguf" in cmd
    assert "-c" in cmd and "8192" in cmd
    assert "-ngl" in cmd and "24" in cmd
    assert "--no-webui" in cmd
    assert "--api-key" in cmd


def test_describe_exit_explains_common_crashes():
    assert "normally" in describe_exit(0)
    assert "SIGILL" in describe_exit(0xC000001D - (1 << 32))
    assert "code 3" in describe_exit(3)
    assert "still running" in describe_exit(None)


# ---------------------------------------------------------------------------
# llama.cpp adapter
# ---------------------------------------------------------------------------

def _llama_runtime(handler, *, respond_health=True):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9999")
    runtime = LlamaCppRuntime(client=client)
    runtime.port = 9999
    runtime._process = _FakeProcess()
    runtime._model_path = "model.gguf"
    return runtime


def test_llamacpp_chat_returns_runtime_reported_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "model": "gemma",
                "choices": [{"message": {"content": "hello"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    runtime = _llama_runtime(handler)
    result = run(runtime.chat([ChatMessage("user", "hi")], GenerationOptions(model="gemma")))
    assert result.text == "hello"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 4
    assert result.usage.reported is True


def test_llamacpp_marks_usage_unreported_when_server_omits_it():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
        )

    runtime = _llama_runtime(handler)
    result = run(runtime.chat([ChatMessage("user", "hi")], GenerationOptions(model="gemma")))
    assert result.usage.reported is False
    assert result.usage.completion_tokens == 0


def test_llamacpp_stream_emits_deltas_and_final_usage():
    body = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"}}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":3,"completion_tokens":2}}',
            "data: [DONE]",
            "",
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    runtime = _llama_runtime(handler)

    async def collect():
        chunks = []
        async for chunk in runtime.chat_stream(
            [ChatMessage("user", "hi")], GenerationOptions(model="gemma")
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())
    assert "".join(c.delta for c in chunks) == "Hello"
    assert chunks[-1].done is True
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.completion_tokens == 2


def test_llamacpp_requires_a_loaded_model():
    runtime = LlamaCppRuntime()
    with pytest.raises(ModelNotLoadedError):
        run(runtime.chat([ChatMessage("user", "hi")], GenerationOptions(model="x")))


def test_llamacpp_reports_process_death_instead_of_hanging():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    runtime = _llama_runtime(handler)
    # Alive when the request starts, dead by the time the transport error is classified.
    runtime._process = _FakeProcess(returncode=0xC000001D - (1 << 32), alive_calls=1)
    with pytest.raises(RuntimeUnavailableError) as exc:
        run(runtime.chat([ChatMessage("user", "hi")], GenerationOptions(model="gemma")))
    assert "SIGILL" in exc.value.message


def test_llamacpp_health_reports_missing_binary(monkeypatch):
    runtime = LlamaCppRuntime(server_bin=None)
    monkeypatch.setattr(runtime, "resolve_server_binary", lambda: None)
    health = run(runtime.health())
    assert health.available is False
    assert "llama.cpp" in health.detail or "llama-server" in health.detail


def test_llamacpp_scan_models_reads_gguf_directory(tmp_path):
    (tmp_path / "a.gguf").write_bytes(b"GGUF")
    runtime = LlamaCppRuntime(models_dir=tmp_path)
    models = run(runtime.list_models())
    assert [m.id for m in models] == ["a"]


# ---------------------------------------------------------------------------
# Ollama adapter
# ---------------------------------------------------------------------------

def _ollama(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:11434")
    return OllamaRuntime(client=client)


def test_ollama_chat_uses_chat_endpoint_and_reports_usage():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(
            200,
            json={
                "model": "qwen2.5:0.5b",
                "message": {"role": "assistant", "content": "hi there"},
                "prompt_eval_count": 9,
                "eval_count": 3,
                "done": True,
            },
        )

    runtime = _ollama(handler)
    result = run(runtime.chat([ChatMessage("user", "hi")], GenerationOptions(model="qwen2.5:0.5b")))
    assert result.text == "hi there"
    assert result.usage.prompt_tokens == 9
    assert result.usage.completion_tokens == 3


def test_ollama_stream_parses_ndjson():
    lines = [
        json.dumps({"message": {"content": "he"}, "done": False}),
        json.dumps({"message": {"content": "llo"}, "done": False}),
        json.dumps({"message": {"content": ""}, "done": True, "prompt_eval_count": 4, "eval_count": 2}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="\n".join(lines))

    runtime = _ollama(handler)

    async def collect():
        return [c async for c in runtime.chat_stream([ChatMessage("user", "hi")], GenerationOptions(model="m"))]

    chunks = asyncio.run(collect())
    assert "".join(c.delta for c in chunks) == "hello"
    assert chunks[-1].done and chunks[-1].usage.completion_tokens == 2


def test_ollama_cancel_raises_cancelled_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="\n".join(
                [json.dumps({"message": {"content": "x"}, "done": False}) for _ in range(5)]
            ),
        )

    runtime = _ollama(handler)

    async def collect():
        async for _ in runtime.chat_stream([ChatMessage("user", "hi")], GenerationOptions(model="m")):
            await runtime.cancel()

    with pytest.raises(InferenceCancelledError):
        asyncio.run(collect())


def test_ollama_unavailable_is_false_not_an_exception():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    runtime = _ollama(handler)
    assert run(runtime.available()) is False
    health = run(runtime.health())
    assert health.available is False


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_builds_both_adapters():
    assert isinstance(create_runtime("llama_cpp"), LlamaCppRuntime)
    assert isinstance(create_runtime("ollama"), OllamaRuntime)
    with pytest.raises(ValueError):
        create_runtime("nonexistent")


def test_registry_reports_capabilities():
    caps = runtime_capabilities("llama_cpp")
    assert caps["requires_explicit_load"] is True
    assert runtime_capabilities("ollama")["requires_explicit_load"] is False
