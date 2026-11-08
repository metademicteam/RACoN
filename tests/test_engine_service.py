"""Engine service tests: real metering, honest failure recording, no invented values."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import racon.services.engine_service as engine_module
from racon.models.catalog import build_catalog, find_entry, ollama_tag_for
from racon.runtime.base import InferenceRuntime
from racon.runtime.types import (
    ChatChunk,
    ChatResult,
    ChatUsage,
    GenerationOptions,
    RuntimeHealth,
    RuntimeUnavailableError,
)
from racon.services.engine_service import EngineService
from racon.settings import Settings


class _FakeRuntime(InferenceRuntime):
    name = "fake"
    requires_explicit_load = False

    def __init__(self, *, fail: bool = False, reported: bool = True):
        self.fail = fail
        self.reported = reported
        self.cancelled = False

    async def available(self) -> bool:
        return True

    async def list_models(self):
        return []

    async def chat(self, messages, options: GenerationOptions) -> ChatResult:
        if self.fail:
            raise RuntimeUnavailableError("no runtime", {"hint": "install llama.cpp"})
        return ChatResult(
            text="answer",
            model=options.model,
            runtime=self.name,
            usage=ChatUsage(4, 2, reported=self.reported),
            duration_ms=50.0,
            time_to_first_token_ms=10.0,
            finish_reason="stop",
        )

    async def chat_stream(self, messages, options: GenerationOptions):
        if self.fail:
            raise RuntimeUnavailableError("no runtime")
        yield ChatChunk(delta="ans")
        yield ChatChunk(delta="wer")
        yield ChatChunk(done=True, finish_reason="stop", usage=ChatUsage(5, 2, reported=self.reported))

    async def cancel(self) -> bool:
        self.cancelled = True
        return True

    async def health(self) -> RuntimeHealth:
        return RuntimeHealth(runtime=self.name, available=True, load_state="ready")


def _engine(tmp_path, runtime: _FakeRuntime) -> EngineService:
    engine = EngineService(settings=Settings(data_dir=tmp_path))
    engine._runtime = runtime
    engine._runtime_name = runtime.name
    return engine


class _RecordingRuntime(_FakeRuntime):
    def __init__(self, name: str, *, explicit_load: bool = False) -> None:
        super().__init__()
        self.name = name
        self.requires_explicit_load = explicit_load
        self.loaded: list[str] = []
        self.chat_models: list[str] = []

    async def load(self, model: str) -> None:
        self.loaded.append(str(model))

    async def chat(self, messages, options: GenerationOptions):
        self.chat_models.append(options.model)
        return await super().chat(messages, options)


class _InstalledModel:
    def __init__(self, model_id: str, path: Path) -> None:
        self.id = model_id
        self.path = path
        self.filename = path.name

    def to_dict(self):
        return {"id": self.id, "path": str(self.path)}


class _FakeModelManager:
    def __init__(self, installed: _InstalledModel | None = None) -> None:
        self._installed = installed
        self.models_dir = Path(".")

    def find_installed(self, model_id: str):
        if self._installed is None:
            return None
        if model_id in (self._installed.id, self._installed.filename, str(self._installed.path)):
            return self._installed
        return None

    def installed(self):
        return [self._installed] if self._installed else []

    def estimate(self, *args, **kwargs):
        return {}


def _routing_engine(tmp_path, monkeypatch, *, configured_runtime, installed=None):
    manager = _FakeModelManager(installed)
    engine = EngineService(
        settings=Settings(data_dir=tmp_path, runtime=configured_runtime),
        model_manager=manager,
    )
    created: dict[str, _RecordingRuntime] = {}

    def fake_create(name: str, **kwargs):
        runtime = created.get(name)
        if runtime is None:
            runtime = _RecordingRuntime(name, explicit_load=name == "llama_cpp")
            created[name] = runtime
        return runtime

    monkeypatch.setattr(engine_module, "create_runtime", fake_create)
    return engine, created


def test_installed_gguf_uses_llama_cpp_when_ollama_is_configured(tmp_path, monkeypatch):
    path = tmp_path / "SmolLM2-360M-Instruct-Q4_K_M.gguf"
    path.write_bytes(b"GGUF")
    installed = _InstalledModel("SmolLM2-360M-Instruct-Q4_K_M", path)
    engine, created = _routing_engine(
        tmp_path, monkeypatch, configured_runtime="ollama", installed=installed
    )

    result = asyncio.run(
        engine.chat([], model="SmolLM2-360M-Instruct-Q4_K_M")
    )

    assert result.text == "answer"
    assert created["llama_cpp"].loaded == [str(path)]
    assert created["llama_cpp"].chat_models == ["SmolLM2-360M-Instruct-Q4_K_M"]
    assert "ollama" not in created or created["ollama"].chat_models == []


def test_ollama_tag_uses_ollama_when_llama_cpp_is_configured(tmp_path, monkeypatch):
    engine, created = _routing_engine(
        tmp_path, monkeypatch, configured_runtime="llama_cpp"
    )

    asyncio.run(engine.chat([], model="qwen2.5:0.5b"))

    assert created["ollama"].chat_models == ["qwen2.5:0.5b"]
    assert "llama_cpp" not in created


def test_ollama_tag_prefers_an_installed_gguf_equivalent(tmp_path, monkeypatch):
    path = tmp_path / "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
    path.write_bytes(b"GGUF")
    installed = _InstalledModel("Qwen2.5-0.5B-Instruct-Q4_K_M", path)
    engine, created = _routing_engine(
        tmp_path, monkeypatch, configured_runtime="llama_cpp", installed=installed
    )

    asyncio.run(engine.chat([], model="qwen2.5:0.5b"))

    assert created["llama_cpp"].loaded == [str(path)]
    assert created["llama_cpp"].chat_models == ["Qwen2.5-0.5B-Instruct-Q4_K_M"]
    assert "ollama" not in created


def test_prefixed_ollama_tag_resolves_to_the_installed_gguf(tmp_path, monkeypatch):
    path = tmp_path / "Qwen2.5-0.5B-Instruct-Q4_K_M.gguf"
    path.write_bytes(b"GGUF")
    installed = _InstalledModel("Qwen2.5-0.5B-Instruct-Q4_K_M", path)
    engine, _ = _routing_engine(
        tmp_path, monkeypatch, configured_runtime="llama_cpp", installed=installed
    )

    assert engine.resolve_runtime("ollama:qwen2.5:0.5b") == (
        "llama_cpp",
        "Qwen2.5-0.5B-Instruct-Q4_K_M",
    )
    assert engine.resolve_runtime("qwen2.5:0.5b") == (
        "llama_cpp",
        "Qwen2.5-0.5B-Instruct-Q4_K_M",
    )


def test_engine_unwraps_prefixed_ollama_catalog_ids(tmp_path):
    engine = EngineService(settings=Settings(data_dir=tmp_path, runtime="llama_cpp"))

    assert engine.resolve_runtime_name("ollama:qwen2.5:0.5b") == "ollama"
    assert engine.runtime_for_model("ollama:qwen2.5:0.5b")[1] == "ollama"
    assert engine._runtime_model_id("ollama:qwen2.5:0.5b", "ollama") == "qwen2.5:0.5b"


def test_known_gguf_ids_map_to_published_ollama_tags():
    assert ollama_tag_for("SmolLM2-360M-Instruct-Q4_K_M") == "smollm2:360m"
    assert ollama_tag_for("Qwen2.5-0.5B-Instruct-Q4_K_M.gguf") == "qwen2.5:0.5b"
    assert ollama_tag_for("ollama:llama3.2:1b") == "llama3.2:1b"
    assert ollama_tag_for("my-custom-model") is None


def test_tag_lookup_still_resolves_the_ollama_catalog_entry():
    entry = find_entry(build_catalog(), "smollm2:360m")
    assert entry is not None
    assert entry.runtime == "ollama"
    assert entry.id == "ollama:smollm2:360m"


def test_successful_chat_is_recorded_with_reported_usage(tmp_path):
    engine = _engine(tmp_path, _FakeRuntime())

    async def run():
        return await engine.chat([], model="m")

    result = asyncio.run(run())
    assert result.text == "answer"

    history = engine.recent_history()
    assert len(history) == 1
    row = history[0]
    assert row["status"] == "completed"
    assert row["prompt_tokens"] == 4
    assert row["completion_tokens"] == 2
    assert row["usage_reported"] == 1
    assert row["ttft_ms"] == 10.0


def test_usage_is_flagged_unmeasured_when_runtime_omits_it(tmp_path):
    engine = _engine(tmp_path, _FakeRuntime(reported=False))

    asyncio.run(engine.chat([], model="m"))
    row = engine.recent_history()[0]
    assert row["usage_reported"] == 0

    summary = engine.usage_summary()
    assert summary["unmeasured"] == 1
    assert summary["requests"] == 1


def test_failed_generation_is_recorded_and_reraised(tmp_path):
    engine = _engine(tmp_path, _FakeRuntime(fail=True))

    with pytest.raises(RuntimeUnavailableError):
        asyncio.run(engine.chat([], model="m"))

    row = engine.recent_history()[0]
    assert row["status"] == "failed"
    assert row["error_code"] == "runtime_unavailable"


def test_streaming_records_time_to_first_token_and_tokens(tmp_path):
    engine = _engine(tmp_path, _FakeRuntime())

    async def run():
        return [chunk async for chunk in engine.chat_stream([], model="m")]

    chunks = asyncio.run(run())
    assert "".join(c.delta for c in chunks) == "answer"
    assert chunks[-1].done

    row = engine.recent_history()[0]
    assert row["status"] == "completed"
    assert row["completion_tokens"] == 2
    assert row["ttft_ms"] is not None


def test_usage_summary_starts_at_zero(tmp_path):
    engine = _engine(tmp_path, _FakeRuntime())
    assert engine.usage_summary() == {
        "requests": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "failed": 0,
        "unmeasured": 0,
    }
