"""Worker snapshot and fastest-node selection for the agentic scheduler.

Only signals that are actually measured are scored. Until a worker has served a task, the
latency component uses a neutral prior and is listed as unmeasured; missing telemetry is
never turned into a fabricated number.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from backend.racon.network.catalog import is_advertisable_model, online_workers
from backend.racon.orchestration.model_router import parse_parameter_count

#: Exponential moving average weight for new latency samples.
_ALPHA = 0.3


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


_STRONG_FAMILIES = (
    "qwen",
    "llama",
    "phi",
    "gemma",
    "mistral",
    "deepseek",
    "granite",
    "minimax",
)


def _model_rank(name: str) -> tuple[int, int, float, int, str]:
    """Prefer a capable small chat model, not the tiniest model on the node.

    A 135M completion model is faster but produces the kind of hallucinated report the
    merge stage then has to reject. Models below 0.5B are ranked last unless the node has
    nothing else; known instruction-tuned families win ties.
    """
    low = name.lower()
    try:
        count = parse_parameter_count(name)
    except Exception:
        count = None
    tiny = 1 if (count is not None and float(count) < 0.5) else 0
    known = 0 if any(family in low for family in _STRONG_FAMILIES) else 1
    charge = 999.0 if count is None else float(count)
    chat_bonus = 0 if ("instruct" in low or "chat" in low) else 1
    return (tiny, known, charge, chat_bonus, low)


def worker_models(node: Any) -> list[str]:
    models: list[str] = []
    for raw in getattr(node, "models", None) or []:
        name = str(raw).strip()
        if name and is_advertisable_model(name) and name not in models:
            models.append(name)
    return sorted(models, key=_model_rank)


class WorkerTelemetryStore:
    """Process-local EMA of observed latency and throughput per worker."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._claim_ms: dict[str, float] = {}
        self._ttft_ms: dict[str, float] = {}
        self._tps: dict[str, float] = {}

    def _update(self, table: dict[str, float], worker_id: str, value: float) -> None:
        if not worker_id or value <= 0:
            return
        with self._lock:
            previous = table.get(worker_id)
            table[worker_id] = value if previous is None else (
                (_ALPHA * value) + ((1.0 - _ALPHA) * previous)
            )

    def record_claim(self, worker_id: str, milliseconds: float) -> None:
        self._update(self._claim_ms, worker_id, float(milliseconds))

    def record_ttft(self, worker_id: str, milliseconds: float) -> None:
        self._update(self._ttft_ms, worker_id, float(milliseconds))

    def record_throughput(self, worker_id: str, tokens_per_second: float) -> None:
        self._update(self._tps, worker_id, float(tokens_per_second))

    def claim_ms(self, worker_id: str) -> Optional[float]:
        with self._lock:
            return self._claim_ms.get(worker_id)

    def ttft_ms(self, worker_id: str) -> Optional[float]:
        with self._lock:
            return self._ttft_ms.get(worker_id)

    def tokens_per_second(self, worker_id: str) -> Optional[float]:
        with self._lock:
            return self._tps.get(worker_id)

    def latency_ms(self, worker_id: str) -> Optional[float]:
        """Claim latency is the best available network signal; TTFT is the fallback."""
        claim = self.claim_ms(worker_id)
        if claim is not None:
            return claim
        return self.ttft_ms(worker_id)

    def reset(self) -> None:
        with self._lock:
            self._claim_ms.clear()
            self._ttft_ms.clear()
            self._tps.clear()


_TELEMETRY = WorkerTelemetryStore()


def get_worker_telemetry() -> WorkerTelemetryStore:
    return _TELEMETRY


@dataclass
class WorkerCandidate:
    node_id: str
    models: list[str]
    active_tasks: int
    cpu_pct: float
    ram_available_mb: Optional[float]
    gpu_name: Optional[str]
    gpu_vram_mb: Optional[float]
    latency_ms: Optional[float]
    tokens_per_second: Optional[float]
    score: float
    unmeasured: tuple[str, ...] = field(default_factory=tuple)
    node: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.node_id,
            "models": self.models,
            "active_tasks": self.active_tasks,
            "cpu_pct": round(self.cpu_pct, 1),
            "ram_available_mb": (
                round(self.ram_available_mb, 0) if self.ram_available_mb is not None else None
            ),
            "gpu": self.gpu_name,
            "gpu_vram_mb": self.gpu_vram_mb,
            "recent_latency_ms": (
                round(self.latency_ms, 1) if self.latency_ms is not None else None
            ),
            "tokens_per_second": (
                round(self.tokens_per_second, 1)
                if self.tokens_per_second is not None
                else None
            ),
            "score": round(self.score, 3),
            "unmeasured": list(self.unmeasured),
        }


def _score(node: Any, models: list[str]) -> tuple[float, tuple[str, ...]]:
    worker_id = str(getattr(node, "node_id", ""))
    latency = _TELEMETRY.latency_ms(worker_id)
    tps = _TELEMETRY.tokens_per_second(worker_id)
    unmeasured: list[str] = []
    if latency is None:
        speed = 0.5
        unmeasured.append("latency")
    else:
        speed = 1.0 / (1.0 + max(0.0, latency) / 1000.0)

    active = len(getattr(node, "active_tasks", None) or [])
    cpu = float(getattr(node, "cpu_util_pct", 0.0) or 0.0)
    load = min(1.0, active * 0.30 + (min(100.0, max(0.0, cpu)) / 100.0) * 0.30)

    ram = _number(getattr(node, "ram_available_mb", None))
    if ram is None:
        caps = getattr(node, "capabilities", None)
        ram = _number(getattr(caps, "ram_available_mb", None))
    if ram is None:
        ram_fit = 0.5
        unmeasured.append("ram")
    else:
        ram_fit = min(1.0, max(0.0, ram) / 8192.0)

    model_fit = 1.0 if models else 0.0
    score = 0.50 * speed + 0.25 * (1.0 - load) + 0.15 * ram_fit + 0.10 * model_fit
    return score, tuple(unmeasured)


def build_environment(preferred_model: str = "") -> dict[str, Any]:
    """Return the live worker view handed to the planning agent."""
    candidates: list[WorkerCandidate] = []
    for node in online_workers():
        if "streaming_chat" not in (getattr(node, "features", None) or []):
            continue
        models = worker_models(node)
        if not models:
            continue
        caps = getattr(node, "capabilities", None)
        score, unmeasured = _score(node, models)
        candidates.append(
            WorkerCandidate(
                node_id=str(getattr(node, "node_id", "")),
                models=models,
                active_tasks=len(getattr(node, "active_tasks", None) or []),
                cpu_pct=float(getattr(node, "cpu_util_pct", 0.0) or 0.0),
                ram_available_mb=_number(getattr(node, "ram_available_mb", None)),
                gpu_name=getattr(caps, "gpu_name", None) if caps is not None else None,
                gpu_vram_mb=_number(getattr(caps, "gpu_vram_mb", None)) if caps is not None else None,
                latency_ms=_TELEMETRY.latency_ms(str(getattr(node, "node_id", ""))),
                tokens_per_second=_TELEMETRY.tokens_per_second(str(getattr(node, "node_id", ""))),
                score=score,
                unmeasured=unmeasured,
                node=node,
            )
        )

    candidates.sort(key=lambda item: (-item.score, item.node_id))
    return {
        "workers": [item.to_dict() for item in candidates],
        "worker_count": len(candidates),
        "preferred_model": preferred_model or None,
    }


def pick_model(
    node: Any,
    *,
    requested: Optional[str] = None,
    preferred: str = "",
) -> Optional[str]:
    models = worker_models(node)
    if not models:
        return None
    if requested:
        wanted = requested.strip().lower()
        for name in models:
            if name.lower() == wanted:
                return name
        return None
    if preferred:
        wanted = preferred.strip().lower()
        for name in models:
            if name.lower() == wanted:
                return name
    return models[0]


def resolve_placement(
    *,
    requested_worker: Optional[str],
    requested_model: Optional[str],
    preferred_model: str = "",
    exclude: Optional[Iterable[str]] = None,
) -> tuple[Optional[WorkerCandidate], Optional[str], Optional[str]]:
    """Pick a worker and an installed model for one task.

    Returns ``(candidate, model, error)``. The caller reports the error to the agent instead
    of fabricating a placement.
    """
    excluded = {item for item in (exclude or []) if item}
    candidates: list[WorkerCandidate] = []
    for node in online_workers():
        if "streaming_chat" not in (getattr(node, "features", None) or []):
            continue
        node_id = str(getattr(node, "node_id", ""))
        if requested_worker and node_id != requested_worker:
            continue
        models = worker_models(node)
        if not models:
            continue
        model = pick_model(node, requested=requested_model, preferred=preferred_model)
        if model is None:
            continue
        score, unmeasured = _score(node, models)
        caps = getattr(node, "capabilities", None)
        candidates.append(
            WorkerCandidate(
                node_id=node_id,
                models=models,
                active_tasks=len(getattr(node, "active_tasks", None) or []),
                cpu_pct=float(getattr(node, "cpu_util_pct", 0.0) or 0.0),
                ram_available_mb=_number(getattr(node, "ram_available_mb", None)),
                gpu_name=getattr(caps, "gpu_name", None) if caps is not None else None,
                gpu_vram_mb=_number(getattr(caps, "gpu_vram_mb", None)) if caps is not None else None,
                latency_ms=_TELEMETRY.latency_ms(node_id),
                tokens_per_second=_TELEMETRY.tokens_per_second(node_id),
                score=score,
                unmeasured=unmeasured,
                node=node,
            )
        )

    if requested_worker:
        if not candidates:
            return None, None, f"worker '{requested_worker}' is offline or cannot serve the task"
        candidate = candidates[0]
        model = pick_model(candidate.node, requested=requested_model, preferred=preferred_model)
        return candidate, model, None

    if requested_model:
        if not candidates:
            return None, None, f"model '{requested_model}' is not advertised by an online worker"
    else:
        # Prefer an idle worker; the score already rewards low load and low latency.
        candidates = [item for item in candidates if item.node_id not in excluded] or candidates
        if not candidates:
            return None, None, "no online worker can execute this task"

    candidates.sort(key=lambda item: (-item.score, item.node_id))
    candidate = candidates[0]
    model = pick_model(candidate.node, requested=requested_model, preferred=preferred_model)
    if model is None:
        return None, None, "the selected worker has no servable model"
    return candidate, model, None
