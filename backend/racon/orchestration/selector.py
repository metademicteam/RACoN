"""Worker selection for individual tasks.

Selection combines capability fit, model fit, availability, measured reliability, network
suitability, and current load. A worker that cannot satisfy a hard requirement is rejected
outright. Missing telemetry never becomes a fabricated number: unmeasured components use a
documented neutral prior and are listed in ``WorkerScore.unmeasured``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional

from backend.racon.orchestration.schemas import TaskSpec

LOCAL_ORIGIN = "local"
DISTRIBUTED_ORIGIN = "distributed"


@dataclass(frozen=True)
class WorkerTelemetry:
    """Live, measurable state of one worker."""

    worker_id: str
    origin: str = LOCAL_ORIGIN
    status: str = "available"
    capabilities: frozenset[str] = frozenset({"text_generation"})
    models: frozenset[str] = frozenset()
    ram_total_mb: Optional[float] = None
    ram_available_mb: Optional[float] = None
    gpu_vram_mb: Optional[float] = None
    workload_pct: Optional[float] = None
    latency_ms: Optional[float] = None
    success_rate: Optional[float] = None
    active_tasks: int = 0


@dataclass(frozen=True)
class SelectionWeights:
    """Configurable weights. The score is a weighted sum minus a load penalty."""

    capability: float = 0.30
    model: float = 0.20
    availability: float = 0.20
    reliability: float = 0.15
    network: float = 0.10
    load: float = 0.15

    @classmethod
    def from_mapping(cls, values: Optional[Mapping[str, float]]) -> "SelectionWeights":
        if not values:
            return cls()
        allowed = {f.name for f in cls.__dataclass_fields__.values()}
        cleaned = {key: float(value) for key, value in values.items() if key in allowed}
        return cls(**cleaned)

    def as_dict(self) -> dict[str, float]:
        return {f.name: float(getattr(self, f.name)) for f in self.__dataclass_fields__.values()}


@dataclass
class WorkerScore:
    worker_id: str
    origin: str
    feasible: bool
    score: float = 0.0
    components: dict[str, float] = field(default_factory=dict)
    unmeasured: tuple[str, ...] = ()
    rejection_reason: Optional[str] = None


def evaluate_worker(
    task: TaskSpec,
    worker: WorkerTelemetry,
    weights: SelectionWeights = SelectionWeights(),
) -> WorkerScore:
    """Score one worker for one task. Hard constraints reject before scoring."""
    required = set(task.requirements.capabilities)
    missing = required - set(worker.capabilities)
    if missing:
        return WorkerScore(
            worker_id=worker.worker_id,
            origin=worker.origin,
            feasible=False,
            rejection_reason=f"missing capabilities: {', '.join(sorted(missing))}",
        )

    if worker.status == "unavailable":
        return WorkerScore(
            worker_id=worker.worker_id,
            origin=worker.origin,
            feasible=False,
            rejection_reason="worker is offline",
        )

    if worker.status == "busy" and (worker.workload_pct is None or worker.workload_pct >= 100.0):
        return WorkerScore(
            worker_id=worker.worker_id,
            origin=worker.origin,
            feasible=False,
            rejection_reason="worker is fully busy",
        )

    if (
        task.requirements.min_ram_mb > 0
        and worker.ram_available_mb is not None
        and worker.ram_available_mb < task.requirements.min_ram_mb
    ):
        return WorkerScore(
            worker_id=worker.worker_id,
            origin=worker.origin,
            feasible=False,
            rejection_reason=(
                f"insufficient RAM: {worker.ram_available_mb:.0f} MB available, "
                f"{task.requirements.min_ram_mb:.0f} MB required"
            ),
        )

    unmeasured: list[str] = []
    requested_model = task.requirements.model

    if requested_model:
        if worker.models and requested_model not in worker.models:
            return WorkerScore(
                worker_id=worker.worker_id,
                origin=worker.origin,
                feasible=False,
                rejection_reason=f"model '{requested_model}' is not installed on this worker",
            )
        if not worker.models:
            model_fit = 0.5
            unmeasured.append("model")
        else:
            model_fit = 1.0
    else:
        model_fit = 0.5

    capability_fit = 1.0

    availability = 1.0 if worker.status == "available" else 0.5

    reliability = worker.success_rate
    if reliability is None:
        reliability = 0.5
        unmeasured.append("reliability")
    reliability = max(0.0, min(1.0, reliability))

    if worker.latency_ms is None:
        network_fit = 0.5
        unmeasured.append("network")
    else:
        network_fit = 1.0 / (1.0 + max(worker.latency_ms, 0.0) / 100.0)

    if worker.workload_pct is None:
        load_penalty = 0.25
        unmeasured.append("load")
    else:
        load_penalty = max(0.0, min(1.0, worker.workload_pct / 100.0))

    # Spreading a plan across nodes is the point of decomposition. A worker already
    # running a task is a worse target than an idle one, even when its CPU looks calm.
    active_tasks = max(0, int(worker.active_tasks or 0))
    queue_penalty = min(0.6, 0.3 * active_tasks)

    score = (
        weights.capability * capability_fit
        + weights.model * model_fit
        + weights.availability * availability
        + weights.reliability * reliability
        + weights.network * network_fit
        - weights.load * load_penalty
        - queue_penalty
    )

    return WorkerScore(
        worker_id=worker.worker_id,
        origin=worker.origin,
        feasible=True,
        score=round(score, 6),
        components={
            "capability_fit": capability_fit,
            "active_tasks": float(active_tasks),
            "queue_penalty": round(queue_penalty, 6),
            "model_fit": model_fit,
            "availability": availability,
            "reliability": reliability,
            "network_fit": round(network_fit, 6),
            "load_penalty": load_penalty,
        },
        unmeasured=tuple(unmeasured),
    )


def rank_workers(
    task: TaskSpec,
    workers: Iterable[WorkerTelemetry],
    weights: SelectionWeights = SelectionWeights(),
    exclude: Optional[set[str]] = None,
) -> list[WorkerScore]:
    """Return feasible workers ordered best-first with a deterministic tie-break."""
    excluded = exclude or set()
    scores = [evaluate_worker(task, worker, weights) for worker in workers if worker.worker_id not in excluded]
    feasible = [item for item in scores if item.feasible]
    feasible.sort(key=lambda item: (-item.score, item.worker_id))
    return feasible


def select_worker(
    task: TaskSpec,
    workers: Iterable[WorkerTelemetry],
    weights: SelectionWeights = SelectionWeights(),
    exclude: Optional[set[str]] = None,
) -> Optional[WorkerScore]:
    """Return the best feasible worker, or ``None`` when every worker is rejected."""
    ranked = rank_workers(task, workers, weights, exclude)
    return ranked[0] if ranked else None


def telemetry_from_node(node: Mapping[str, object], origin: str = DISTRIBUTED_ORIGIN) -> WorkerTelemetry:
    """Build telemetry from a stored node record without inventing missing measurements."""
    capabilities_raw = node.get("capabilities") or {}
    capabilities = {"text_generation"}
    if isinstance(capabilities_raw, Mapping):
        runtimes = capabilities_raw.get("runtimes") or []
        if isinstance(runtimes, list):
            for runtime in runtimes:
                if isinstance(runtime, str) and runtime:
                    capabilities.add(runtime)

    models_raw = node.get("models") or node.get("installed_models") or []
    models = {str(item) for item in models_raw} if isinstance(models_raw, (list, tuple, set)) else set()

    def _number(key: str) -> Optional[float]:
        value = node.get(key)
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return WorkerTelemetry(
        worker_id=str(node.get("id") or node.get("worker_id") or ""),
        origin=origin,
        status=str(node.get("status") or "unavailable"),
        capabilities=frozenset(capabilities),
        models=frozenset(models),
        ram_total_mb=_number("ram_total_mb"),
        ram_available_mb=_number("ram_available_mb"),
        gpu_vram_mb=_number("gpu_vram_mb") or _number("vram_total_mb"),
        workload_pct=_number("workload_pct"),
        latency_ms=_number("network_latency_ms"),
        success_rate=_number("success_rate"),
        active_tasks=int(_number("active_tasks") or 0),
    )
