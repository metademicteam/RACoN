"""llama.cpp runtime adapter.

Owns a ``llama-server`` child process and speaks its OpenAI-compatible HTTP API. Every
number reported here (tokens, timings, layers) comes from llama.cpp itself; when llama.cpp
does not report a value the adapter says so instead of inventing one.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx

from racon.runtime.base import InferenceRuntime
from racon.runtime.distributed_gguf import (
    find_llama_tool,
    inspect_gguf_metadata,
    llama_install_hint,
    llama_process_env,
)
from racon.runtime.types import (
    ChatChunk,
    ChatMessage,
    ChatResult,
    ChatUsage,
    GenerationOptions,
    InferenceCancelledError,
    InferenceExecutionError,
    ModelInfo,
    ModelNotFoundError,
    ModelNotLoadedError,
    RuntimeCompatibilityError,
    RuntimeHealth,
    RuntimeUnavailableError,
)

logger = logging.getLogger(__name__)

DEFAULT_STARTUP_TIMEOUT = 300.0
DEFAULT_CONTEXT_SIZE = 4096


class LlamaCppProcessError(RuntimeUnavailableError):
    """llama-server exited or failed to start."""


def free_port(host: str = "127.0.0.1") -> int:
    """Reserve an ephemeral loopback port for llama-server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


def describe_exit(code: Optional[int]) -> str:
    """Human-readable reason for a llama.cpp process exit code."""
    if code is None:
        return "llama-server is still running"
    if code == 0:
        return "llama-server exited normally"
    # Windows NTSTATUS inside the 32-bit integer space
    if code in (-1073741795, 0xC000001D - (1 << 32)):
        return "llama-server crashed with SIGILL (illegal instruction) - this CPU lacks an instruction the build requires"
    if code == 3221225477 - (1 << 32) or code == 0xC0000005:
        return "llama-server crashed with an access violation"
    return f"llama-server exited with code {code}"


def build_server_command(
    server_bin: str,
    model_path: str,
    host: str,
    port: int,
    *,
    context_size: int = DEFAULT_CONTEXT_SIZE,
    n_gpu_layers: int = 0,
    threads: int = 4,
    alias: str = "racon",
    api_key: Optional[str] = None,
    extra_args: Optional[list[str]] = None,
) -> list[str]:
    """Build the llama-server argument list. Kept separate so it can be unit tested."""
    cmd = [
        server_bin,
        "-m", model_path,
        "--alias", alias,
        "--host", host,
        "--port", str(port),
        "-c", str(context_size),
        "-ngl", str(n_gpu_layers),
        "-t", str(threads),
        "--no-webui",
        "--jinja",
    ]
    if api_key:
        cmd.extend(["--api-key", api_key])
    if extra_args:
        cmd.extend(extra_args)
    return cmd


class LlamaCppRuntime(InferenceRuntime):
    """Supervises one llama-server instance for one GGUF model at a time."""

    name = "llama_cpp"
    requires_explicit_load = True

    def __init__(
        self,
        model_path: Optional[str] = None,
        *,
        models_dir: Optional[Path] = None,
        search_dirs: Optional[list[Path]] = None,
        host: str = "127.0.0.1",
        port: int = 0,
        context_size: int = DEFAULT_CONTEXT_SIZE,
        n_gpu_layers: int = 0,
        threads: Optional[int] = None,
        startup_timeout: float = DEFAULT_STARTUP_TIMEOUT,
        server_bin: Optional[str] = None,
        api_key: Optional[str] = None,
        extra_args: Optional[list[str]] = None,
        client: Optional[httpx.AsyncClient] = None,
        process_factory: Any = None,
    ) -> None:
        self.host = host
        self.port = port
        self.context_size = context_size
        self.n_gpu_layers = n_gpu_layers
        self.threads = threads or max(1, (os.cpu_count() or 4) - 1)
        self.startup_timeout = startup_timeout
        self.server_bin = server_bin
        self.api_key = api_key
        self.extra_args = extra_args or []
        self.models_dir = Path(models_dir) if models_dir else None
        self.search_dirs = [Path(p) for p in (search_dirs or [])]
        self._external_client = client is not None
        self._client = client
        self._process_factory = process_factory or subprocess.Popen
        self._process: Optional[subprocess.Popen] = None
        self._model_path: Optional[str] = str(model_path) if model_path else None
        self._active_response: Optional[httpx.Response] = None
        self._cancelled = False
        self._log_path: Optional[str] = None
        self._log_handle: Any = None
        self.last_load_error: Optional[str] = None

    # -- state -------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def loaded_model(self) -> Optional[str]:
        return self._model_path if self.is_running else None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=httpx.Timeout(None, connect=10.0),
                headers=self._headers(),
            )
        return self._client

    # -- discovery ---------------------------------------------------------

    def resolve_server_binary(self) -> Optional[str]:
        return self.server_bin or find_llama_tool("llama-server")

    async def available(self) -> bool:
        return self.resolve_server_binary() is not None

    def _scan_models(self) -> list[ModelInfo]:
        if not self.models_dir or not self.models_dir.exists():
            return []
        found: list[ModelInfo] = []
        for path in sorted(self.models_dir.rglob("*.gguf")):
            if not path.is_file():
                continue
            size = path.stat().st_size
            ctx = None
            family = ""
            try:
                meta = inspect_gguf_metadata(str(path))
                ctx = meta.get("context_length") or None
                family = str(meta.get("architecture") or "")
            except Exception as exc:  # metadata is best-effort
                logger.debug("GGUF metadata read failed for %s: %s", path, exc)
            found.append(
                ModelInfo(
                    id=path.stem,
                    runtime=self.name,
                    display_name=path.stem,
                    size_bytes=size,
                    context_length=ctx,
                    family=family,
                    loaded=self._model_path == str(path) and self.is_running,
                    path=str(path),
                )
            )
        return found

    async def list_models(self) -> list[ModelInfo]:
        models = await asyncio.to_thread(self._scan_models)
        if self.is_running and self._model_path:
            for model in models:
                if model.path == self._model_path:
                    model.loaded = True
                    break
        return models

    async def health(self) -> RuntimeHealth:
        server = self.resolve_server_binary()
        if not server:
            return RuntimeHealth(
                runtime=self.name,
                available=False,
                detail=f"llama-server not found. {llama_install_hint().capitalize()}.",
                load_state="unavailable",
            )
        state = "ready" if self.is_running else "unloaded"
        return RuntimeHealth(
            runtime=self.name,
            available=True,
            detail=describe_exit(self._process.returncode) if self._process and not self.is_running else "",
            models=[Path(self._model_path).stem] if self._model_path and self.is_running else [],
            load_state=state,
            extra={
                "server_binary": server,
                "base_url": self.base_url if self.is_running else None,
                "model_path": self._model_path if self.is_running else None,
                "pid": self._process.pid if self.is_running and self._process else None,
            },
        )

    # -- lifecycle ---------------------------------------------------------

    def _resolve_model(self, model: str) -> Path:
        candidate = Path(model)
        if candidate.is_file():
            return candidate
        roots = [root for root in ([self.models_dir] + self.search_dirs) if root is not None]
        for root in roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*.gguf"):
                if path.stem == model or path.name == model:
                    return path
        raise ModelNotFoundError(f"Model '{model}' is not installed", {"model": model})

    async def load(self, model: str) -> None:
        """Start llama-server for a model. Idempotent when it is already serving."""
        path = await asyncio.to_thread(self._resolve_model, model)
        if self.is_running and self._model_path == str(path):
            return

        server = self.resolve_server_binary()
        if not server:
            raise RuntimeUnavailableError(
                f"llama-server executable not found. {llama_install_hint().capitalize()}.",
                {"hint": llama_install_hint()},
            )

        await self.unload()

        meta = await asyncio.to_thread(inspect_gguf_metadata, str(path))
        if meta.get("is_valid_gguf") is False:
            raise RuntimeCompatibilityError(
                f"'{path.name}' is not a valid GGUF file", {"path": str(path)}
            )

        if self.port == 0:
            self.port = free_port(self.host)
        self.api_key = self.api_key or secrets.token_urlsafe(24)
        self._model_path = str(path)

        cmd = build_server_command(
            server,
            str(path),
            self.host,
            self.port,
            context_size=self.context_size,
            n_gpu_layers=self.n_gpu_layers,
            threads=self.threads,
            alias=path.stem,
            api_key=self.api_key,
            extra_args=self.extra_args,
        )
        logger.info("Starting llama-server for %s on port %d", path.name, self.port)

        from racon.config import get_log_dir

        self._log_path = str(Path(get_log_dir()) / f"llama-server-{self.port}.log")
        out: Any = subprocess.DEVNULL
        try:
            self._log_handle = open(self._log_path, "w", encoding="utf-8")
            out = self._log_handle
        except OSError:
            self._log_path = None

        try:
            self._process = self._process_factory(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                env=llama_process_env(),
            )
        except OSError as exc:
            self._close_log()
            raise RuntimeUnavailableError(
                f"Could not start llama-server: {exc}", {"command": cmd[0]}
            ) from exc

        started = time.monotonic()
        try:
            await self._wait_until_healthy(self.startup_timeout)
        except Exception:
            self.last_load_error = self._tail_log()
            await self.unload()
            raise
        logger.info("llama-server ready in %.1fs", time.monotonic() - started)

    async def _wait_until_healthy(self, timeout: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        async with httpx.AsyncClient(timeout=2.0, headers=self._headers()) as probe:
            while loop.time() < deadline:
                process = self._process
                if process is not None and process.poll() is not None:
                    raise LlamaCppProcessError(
                        describe_exit(process.returncode),
                        {"exit_code": process.returncode, "log": self._tail_log()},
                    )
                try:
                    resp = await probe.get(f"{self.base_url}/health")
                    if resp.status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.25)
        raise LlamaCppProcessError(
            f"llama-server did not become healthy within {timeout:.0f}s",
            {"log": self._tail_log()},
        )

    def _tail_log(self, limit: int = 4000) -> str:
        if not self._log_path or not Path(self._log_path).exists():
            return ""
        try:
            text = Path(self._log_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        return text[-limit:]

    def _close_log(self) -> None:
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except Exception:  # pragma: no cover
                pass
            self._log_handle = None

    async def unload(self, model: Optional[str] = None) -> None:
        process = self._process
        self._process = None
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5.0)
        self._close_log()
        if not self._external_client and self._client is not None:
            await self._client.aclose()
            self._client = None
        self._active_response = None

    async def cancel(self) -> bool:
        self._cancelled = True
        response = self._active_response
        if response is None:
            return False
        try:
            await response.aclose()
        except Exception:  # pragma: no cover
            logger.debug("Closing llama-server stream failed", exc_info=True)
        return True

    async def aclose(self) -> None:
        await self.unload()

    # -- generation --------------------------------------------------------

    def _payload(
        self, messages: list[ChatMessage], options: GenerationOptions, stream: bool
    ) -> dict[str, Any]:
        opts = options.normalised()
        body: dict[str, Any] = {
            "model": opts.model,
            "messages": [m.to_dict() for m in messages],
            "max_tokens": opts.max_tokens,
            "temperature": opts.temperature,
            "top_p": opts.top_p,
            "stream": stream,
            "cache_prompt": True,
        }
        if opts.top_k:
            body["top_k"] = opts.top_k
        if opts.stop:
            body["stop"] = opts.stop
        if opts.seed is not None:
            body["seed"] = opts.seed
        if opts.tools:
            body["tools"] = opts.tools
        if opts.tool_choice is not None:
            body["tool_choice"] = opts.tool_choice
        if opts.parallel_tool_calls is not None:
            body["parallel_tool_calls"] = opts.parallel_tool_calls
        if opts.response_format:
            body["response_format"] = opts.response_format
        if opts.presence_penalty is not None:
            body["presence_penalty"] = opts.presence_penalty
        if opts.frequency_penalty is not None:
            body["frequency_penalty"] = opts.frequency_penalty
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    def _ensure_loaded(self) -> None:
        if not self.is_running:
            raise ModelNotLoadedError(
                "No model is loaded in llama.cpp",
                {"last_error": self.last_load_error},
            )

    @staticmethod
    def _usage_from(payload: dict[str, Any]) -> ChatUsage:
        usage = payload.get("usage")
        if isinstance(usage, dict) and ("completion_tokens" in usage or "prompt_tokens" in usage):
            return ChatUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            )
        timings = payload.get("timings")
        if isinstance(timings, dict) and ("predicted_n" in timings or "prompt_n" in timings):
            return ChatUsage(
                prompt_tokens=int(timings.get("prompt_n") or 0),
                completion_tokens=int(timings.get("predicted_n") or 0),
            )
        return ChatUsage(reported=False)

    async def chat(self, messages: list[ChatMessage], options: GenerationOptions) -> ChatResult:
        self._ensure_loaded()
        self._cancelled = False
        started = time.monotonic()
        body = self._payload(messages, options, stream=False)
        try:
            resp = await self._get_client().post("/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            self._raise_for_transport(exc)
            raise

        if resp.status_code == 404:
            raise ModelNotFoundError(f"Model '{options.model}' is not served", {"model": options.model})
        if resp.status_code != 200:
            raise InferenceExecutionError(
                f"llama-server returned HTTP {resp.status_code}",
                {"status_code": resp.status_code, "body": resp.text[:512]},
            )

        data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = message.get("content") or ""
        return ChatResult(
            text=text,
            model=data.get("model", options.model),
            runtime=self.name,
            usage=self._usage_from(data),
            duration_ms=(time.monotonic() - started) * 1000.0,
            finish_reason=choice.get("finish_reason"),
            tool_calls=message.get("tool_calls") or None,
        )

    async def chat_stream(
        self, messages: list[ChatMessage], options: GenerationOptions
    ) -> AsyncIterator[ChatChunk]:
        self._ensure_loaded()
        self._cancelled = False
        body = self._payload(messages, options, stream=True)
        try:
            async with self._get_client().stream(
                "POST", "/v1/chat/completions", json=body
            ) as response:
                self._active_response = response
                if response.status_code != 200:
                    raw = (await response.aread()).decode("utf-8", "replace")[:512]
                    raise InferenceExecutionError(
                        f"llama-server returned HTTP {response.status_code}",
                        {"status_code": response.status_code, "body": raw},
                    )

                usage = ChatUsage(reported=False)
                finish_reason: Optional[str] = None
                async for line in response.aiter_lines():
                    if self._cancelled:
                        raise InferenceCancelledError("Generation cancelled by caller")
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    if event.get("usage"):
                        usage = self._usage_from(event)
                    choice = (event.get("choices") or [{}])[0]
                    delta_obj = choice.get("delta") or {}
                    delta = delta_obj.get("content") or ""
                    tool_calls = delta_obj.get("tool_calls")
                    role = delta_obj.get("role")
                    if delta or tool_calls or role:
                        yield ChatChunk(
                            delta=delta,
                            tool_calls=tool_calls or None,
                            role=role or None,
                        )
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

                yield ChatChunk(done=True, finish_reason=finish_reason or "stop", usage=usage)
        except (InferenceCancelledError, InferenceExecutionError, ModelNotFoundError):
            raise
        except httpx.HTTPError as exc:
            self._raise_for_transport(exc)
            raise
        finally:
            self._active_response = None

    def _raise_for_transport(self, exc: httpx.HTTPError) -> None:
        if self._process is not None and self._process.poll() is not None:
            raise LlamaCppProcessError(
                describe_exit(self._process.returncode),
                {"exit_code": self._process.returncode, "log": self._tail_log()},
            ) from exc
        raise RuntimeUnavailableError(
            f"llama-server is not reachable at {self.base_url}",
            {"error": str(exc)},
        ) from exc
