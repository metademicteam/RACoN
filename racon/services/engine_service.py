"""Local engine: the one place that binds a runtime, the model store and history.

The HTTP API and the desktop UI talk to this service. It never fabricates a result: if the
runtime fails, the failure is returned and recorded as a failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Optional

from racon.models.catalog import ollama_tag_for
from racon.models.manager import ModelManager, ModelStorageError
from racon.runtime.base import InferenceRuntime
from racon.runtime.registry import available_runtimes, create_runtime, runtime_capabilities
from racon.runtime.types import (
    ChatChunk,
    ChatMessage,
    ChatResult,
    ChatUsage,
    GenerationOptions,
    RaconRuntimeError,
    RuntimeHealth,
)
from racon.settings import Settings, get_settings
from racon.storage.database import Database

logger = logging.getLogger(__name__)


class EngineService:
    """Owns the process-wide inference runtime for the local engine."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        database: Optional[Database] = None,
        model_manager: Optional[ModelManager] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._db = database
        self._models = model_manager
        self._runtime: Optional[InferenceRuntime] = None
        self._runtime_name: Optional[str] = None
        self._extra_runtimes: dict[str, InferenceRuntime] = {}
        self._loaded_models: dict[str, str] = {}
        self._loaded_model: Optional[str] = None
        self._load_lock = asyncio.Lock()

    # -- collaborators -----------------------------------------------------

    @property
    def database(self) -> Database:
        if self._db is None:
            self._db = Database(self.settings.db_path)
            self._db.initialize()
        return self._db

    @property
    def models(self) -> ModelManager:
        if self._models is None:
            self._models = ModelManager(
                self.settings.resolved_models_dir,
                extra_search_dirs=[self.settings.data_dir / "models"],
            )
        return self._models

    @property
    def runtime_name(self) -> str:
        return self._runtime_name or self.settings.runtime

    def runtime(self) -> InferenceRuntime:
        """Return the configured runtime, creating it on first use."""
        if self._runtime is None:
            self._runtime = create_runtime(
                self.settings.runtime,
                models_dir=self.models.models_dir,
                ollama_url=self.settings.ollama_url,
                context_size=self.settings.context_size,
                n_gpu_layers=self.settings.n_gpu_layers,
                threads=self.settings.threads,
            )
            self._runtime_name = self.settings.runtime
        return self._runtime

    def _runtime_for_name(self, name: str) -> InferenceRuntime:
        key = (name or "").strip().lower() or self.runtime_name
        if key == self.runtime_name:
            return self.runtime()
        runtime = self._extra_runtimes.get(key)
        if runtime is None:
            runtime = create_runtime(
                key,
                models_dir=self.models.models_dir,
                ollama_url=self.settings.ollama_url,
                context_size=self.settings.context_size,
                n_gpu_layers=self.settings.n_gpu_layers,
                threads=self.settings.threads,
            )
            self._extra_runtimes[key] = runtime
        return runtime

    def _installed_for_ollama_tag(self, tag: str):
        """Return the installed GGUF whose catalog identity is this Ollama tag."""
        from racon.models.catalog import ollama_tag_for

        wanted = (tag or "").strip().lower()
        if not wanted:
            return None
        try:
            installed = self.models.installed()
        except Exception:
            return None
        for model in installed:
            if (ollama_tag_for(model.id) or "").lower() == wanted:
                return model
        return None

    def resolve_runtime(self, model: str) -> tuple[str, str]:
        """Return ``(runtime_name, model_id)`` for a requested model.

        The configured runtime is the default, not a global override: a GGUF installed on
        this machine is served by llama.cpp even when Ollama is the configured default.
        Ollama tags (``qwen2.5:0.5b`` and ``ollama:qwen2.5:0.5b``) also resolve to an
        installed GGUF with the same catalog identity, so the API keeps working on hosts
        that do not run an Ollama daemon.
        """
        raw = (model or "").strip()
        if not raw:
            return self.runtime_name, raw
        lowered = raw.lower()
        if lowered.startswith("ollama:"):
            tag = raw.split(":", 1)[1].strip()
            installed = self._installed_for_ollama_tag(tag)
            if installed is not None:
                return "llama_cpp", installed.id
            return "ollama", tag
        # Ollama names carry a tag separator; GGUF ids from filenames do not.
        if ":" in raw:
            installed = self._installed_for_ollama_tag(raw)
            if installed is not None:
                return "llama_cpp", installed.id
            return "ollama", raw
        if lowered.endswith(".gguf"):
            return "llama_cpp", raw
        try:
            if self.models.find_installed(raw) is not None:
                return "llama_cpp", raw
        except Exception:
            pass
        return self.runtime_name, raw

    def resolve_runtime_name(self, model: str) -> str:
        return self.resolve_runtime(model)[0]

    def resolve_target(self, model: str) -> tuple[InferenceRuntime, str, str]:
        """Return ``(runtime, runtime_name, model_id)`` ready for loading/generation."""
        runtime_name, resolved_model = self.resolve_runtime(model)
        return self._runtime_for_name(runtime_name), runtime_name, resolved_model

    def runtime_for_model(self, model: str) -> tuple[InferenceRuntime, str]:
        name, _ = self.resolve_runtime(model)
        return self._runtime_for_name(name), name

    @staticmethod
    def _runtime_model_id(model: str, runtime_name: str) -> str:
        if runtime_name == "ollama":
            return ollama_tag_for(model) or model
        return model

    async def aclose(self) -> None:
        runtimes = list(self._extra_runtimes.values())
        if self._runtime is not None:
            runtimes.append(self._runtime)
        for runtime in runtimes:
            try:
                await runtime.aclose()
            except Exception:
                logger.debug("Runtime shutdown failed", exc_info=True)
        self._extra_runtimes.clear()
        self._runtime = None
        self._loaded_models.clear()
        self._loaded_model = None

    # -- status ------------------------------------------------------------

    async def status(self) -> dict[str, Any]:
        runtime = self.runtime()
        health: RuntimeHealth = await runtime.health()
        installed = await self._installed_safe()
        return {
            "runtime": runtime_capabilities(self.runtime_name),
            "health": health.to_dict(),
            "loaded_model": self._loaded_model,
            "models": [m.to_dict() for m in installed],
            "models_dir": str(self.models.models_dir),
            "available_runtimes": await available_runtimes(
                models_dir=self.models.models_dir, ollama_url=self.settings.ollama_url
            ),
        }

    async def _installed_safe(self) -> list[Any]:
        import asyncio

        try:
            return await asyncio.to_thread(self.models.installed)
        except Exception as exc:  # never let a scan failure break the status endpoint
            logger.warning("Model scan failed: %s", exc)
            return []

    # -- model lifecycle ---------------------------------------------------

    async def load_model(self, model_id: str) -> dict[str, Any]:
        # Two concurrent first requests must not spawn two llama-server processes for the
        # same model; the loser would fail and be torn down while the winner keeps the port.
        async with self._load_lock:
            return await self._load_model_locked(model_id)

    async def _load_model_locked(self, model_id: str) -> dict[str, Any]:
        runtime, runtime_name, resolved_model = await asyncio.to_thread(
            self.resolve_target, model_id
        )
        installed = await asyncio.to_thread(self.models.find_installed, resolved_model)
        started = time.monotonic()

        if installed is None:
            if runtime_name != "ollama":
                raise ModelStorageError(
                    f"Model '{model_id}' is not installed. Install it from the Model Hub first.",
                    {"model": model_id},
                )
            ollama_model = self._runtime_model_id(resolved_model, runtime_name)
            available = {model.id for model in await runtime.list_models()}
            if ollama_model not in available:
                raise ModelStorageError(
                    f"Model '{model_id}' is not installed in Ollama. Pull '{ollama_model}' first.",
                    {"model": model_id, "ollama_model": ollama_model},
                )
            self._loaded_models[runtime_name] = ollama_model
            self._loaded_model = ollama_model
            return {
                "model": {
                    "id": ollama_model,
                    "filename": ollama_model,
                    "path": "",
                    "size_bytes": 0,
                    "runtime": runtime_name,
                },
                "load_ms": 0.0,
                "estimate": None,
            }

        await runtime.load(str(installed.path))
        self._loaded_models[runtime_name] = installed.id
        self._loaded_model = installed.id
        elapsed = (time.monotonic() - started) * 1000.0
        self._record_event("runtime", "info", f"Loaded model {installed.id}", {"ms": round(elapsed, 1)})
        return {
            "model": installed.to_dict(),
            "load_ms": round(elapsed, 1),
            "estimate": self.models.estimate(installed.id, self.settings.context_size),
        }

    async def unload_model(self) -> dict[str, Any]:
        previous = self._loaded_model
        runtimes = list(self._extra_runtimes.values())
        if self._runtime is not None:
            runtimes.append(self._runtime)
        for runtime in runtimes:
            try:
                await runtime.unload()
            except Exception:
                logger.debug("Runtime unload failed", exc_info=True)
        self._loaded_models.clear()
        self._loaded_model = None
        self._record_event("runtime", "info", f"Unloaded model {previous or '(none)'}")
        return {"unloaded": previous}

    # -- generation --------------------------------------------------------

    def _prepare(self, model: str) -> GenerationOptions:
        return GenerationOptions(
            model=model,
            context_size=self.settings.context_size,
        )

    def _options(
        self,
        model: str,
        *,
        max_tokens: int,
        temperature: float,
        top_p: float,
        stop: Optional[list[str]],
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[dict[str, Any]] = None,
        seed: Optional[int] = None,
        presence_penalty: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        parallel_tool_calls: Optional[bool] = None,
        top_k: Optional[int] = None,
    ) -> GenerationOptions:
        options = self._prepare(model)
        options.max_tokens = max_tokens
        options.temperature = temperature
        options.top_p = top_p
        options.stop = stop or []
        options.tools = list(tools or [])
        options.tool_choice = tool_choice
        options.response_format = response_format
        options.seed = seed
        options.presence_penalty = presence_penalty
        options.frequency_penalty = frequency_penalty
        options.parallel_tool_calls = parallel_tool_calls
        if top_k is not None:
            options.top_k = top_k
        return options

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.95,
        stop: Optional[list[str]] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[dict[str, Any]] = None,
        seed: Optional[int] = None,
        presence_penalty: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        parallel_tool_calls: Optional[bool] = None,
        top_k: Optional[int] = None,
    ) -> ChatResult:
        runtime, runtime_name, resolved_model = await asyncio.to_thread(
            self.resolve_target, model
        )
        runtime_model = self._runtime_model_id(resolved_model, runtime_name)
        if runtime.requires_explicit_load:
            installed = await asyncio.to_thread(self.models.find_installed, resolved_model)
            desired = installed.id if installed is not None else runtime_model
            if self._loaded_models.get(runtime_name) != desired:
                await self.load_model(resolved_model)

        options = self._options(
            runtime_model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            seed=seed,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            parallel_tool_calls=parallel_tool_calls,
            top_k=top_k,
        )

        request_id = uuid.uuid4().hex
        started = time.monotonic()
        try:
            result = await runtime.chat(messages, options)
        except RaconRuntimeError as exc:
            self._record_history(
                request_id, model, "failed", runtime=runtime_name,
                duration_ms=(time.monotonic() - started) * 1000.0,
                error_code=exc.code,
            )
            self._record_event("inference", "error", exc.message, {"code": exc.code})
            raise

        self._record_history(
            request_id,
            model,
            "completed",
            runtime=runtime_name,
            usage=result.usage,
            duration_ms=result.duration_ms,
            ttft_ms=result.time_to_first_token_ms,
            tokens_per_second=result.generation_tokens_per_second,
        )
        return result

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        *,
        model: str,
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.95,
        stop: Optional[list[str]] = None,
        tools: Optional[list[dict[str, Any]]] = None,
        tool_choice: Any = None,
        response_format: Optional[dict[str, Any]] = None,
        seed: Optional[int] = None,
        presence_penalty: Optional[float] = None,
        frequency_penalty: Optional[float] = None,
        parallel_tool_calls: Optional[bool] = None,
        top_k: Optional[int] = None,
    ) -> AsyncIterator[ChatChunk]:
        runtime, runtime_name, resolved_model = await asyncio.to_thread(
            self.resolve_target, model
        )
        runtime_model = self._runtime_model_id(resolved_model, runtime_name)
        if runtime.requires_explicit_load:
            installed = await asyncio.to_thread(self.models.find_installed, resolved_model)
            desired = installed.id if installed is not None else runtime_model
            if self._loaded_models.get(runtime_name) != desired:
                await self.load_model(resolved_model)

        options = self._options(
            runtime_model,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=stop,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            seed=seed,
            presence_penalty=presence_penalty,
            frequency_penalty=frequency_penalty,
            parallel_tool_calls=parallel_tool_calls,
            top_k=top_k,
        )

        request_id = uuid.uuid4().hex
        started = time.monotonic()
        first_delta_at: Optional[float] = None
        usage = ChatUsage(reported=False)
        status = "completed"
        error_code: Optional[str] = None

        try:
            async for chunk in runtime.chat_stream(messages, options):
                if chunk.delta and first_delta_at is None:
                    first_delta_at = time.monotonic()
                if chunk.done and chunk.usage is not None:
                    usage = chunk.usage
                yield chunk
        except RaconRuntimeError as exc:
            status = "cancelled" if exc.code == "inference_cancelled" else "failed"
            error_code = exc.code
            self._record_event("inference", "error", exc.message, {"code": exc.code})
            raise
        finally:
            duration_ms = (time.monotonic() - started) * 1000.0
            ttft_ms = (
                (first_delta_at - started) * 1000.0 if first_delta_at is not None else None
            )
            tps = None
            if usage.completion_tokens and ttft_ms is not None:
                generation_ms = duration_ms - ttft_ms
                if generation_ms > 0:
                    tps = round(usage.completion_tokens / (generation_ms / 1000.0), 2)
            self._record_history(
                request_id,
                model,
                status,
                runtime=runtime_name,
                usage=usage,
                duration_ms=duration_ms,
                ttft_ms=ttft_ms,
                tokens_per_second=tps,
                error_code=error_code,
            )

    async def cancel(self) -> bool:
        cancelled = False
        runtimes = list(self._extra_runtimes.values())
        if self._runtime is not None:
            runtimes.append(self._runtime)
        for runtime in runtimes:
            try:
                cancelled = await runtime.cancel() or cancelled
            except Exception:
                logger.debug("Runtime cancel failed", exc_info=True)
        return cancelled

    # -- persistence -------------------------------------------------------

    def _record_history(
        self,
        request_id: str,
        model: str,
        status: str,
        *,
        runtime: Optional[str] = None,
        usage: Optional[ChatUsage] = None,
        duration_ms: float = 0.0,
        ttft_ms: Optional[float] = None,
        tokens_per_second: Optional[float] = None,
        error_code: Optional[str] = None,
    ) -> None:
        try:
            conn = self.database.connect()
            with self.database.write_lock:
                conn.execute(
                    """
                    INSERT INTO inference_history (
                        id, request_id, model, runtime, worker_id,
                        prompt_tokens, completion_tokens, usage_reported,
                        duration_ms, ttft_ms, tokens_per_second, status, error_code
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uuid.uuid4().hex,
                        request_id,
                        model,
                        runtime or self.runtime_name,
                        None,
                        usage.prompt_tokens if usage else 0,
                        usage.completion_tokens if usage else 0,
                        1 if (usage and usage.reported) else 0,
                        round(duration_ms, 2),
                        round(ttft_ms, 2) if ttft_ms is not None else None,
                        tokens_per_second,
                        status,
                        error_code,
                    ),
                )
                conn.commit()
        except Exception as exc:  # history must never break inference
            logger.warning("Could not record inference history: %s", exc)

    def _record_event(self, category: str, level: str, message: str, detail: Optional[dict[str, Any]] = None) -> None:
        import json

        try:
            conn = self.database.connect()
            with self.database.write_lock:
                conn.execute(
                    "INSERT INTO engine_events (category, level, message, detail) VALUES (?, ?, ?, ?)",
                    (category, level, message, json.dumps(detail) if detail else None),
                )
                conn.commit()
        except Exception as exc:
            logger.debug("Could not record engine event: %s", exc)

    def recent_history(self, limit: int = 50) -> list[dict[str, Any]]:
        try:
            conn = self.database.connect()
            rows = conn.execute(
                "SELECT * FROM inference_history ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning("Could not read inference history: %s", exc)
            return []

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        try:
            conn = self.database.connect()
            rows = conn.execute(
                "SELECT * FROM engine_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(row) for row in rows]
        except Exception as exc:
            logger.warning("Could not read engine events: %s", exc)
            return []

    def usage_summary(self) -> dict[str, Any]:
        """Aggregate real metered usage from the local history."""
        try:
            conn = self.database.connect()
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                    COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                    COUNT(*) AS requests,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN usage_reported = 0 THEN 1 ELSE 0 END), 0) AS unmeasured
                FROM inference_history
                """
            ).fetchone()
            if row is None:
                return {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "failed": 0, "unmeasured": 0}
            return dict(row)
        except Exception as exc:
            logger.warning("Could not aggregate usage: %s", exc)
            return {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "failed": 0, "unmeasured": 0}


_engine: Optional[EngineService] = None


def get_engine() -> EngineService:
    """Process-wide engine singleton used by the API layer."""
    global _engine
    if _engine is None:
        _engine = EngineService()
    return _engine


def reset_engine() -> None:
    global _engine
    _engine = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
