"""Data models for the scheduling engine."""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


class SchedulingObjective(enum.Enum):
    """Supported scheduling objectives."""
    LOWEST_LATENCY = "lowest_latency"
    HIGHEST_THROUGHPUT = "highest_throughput"
    RESOURCE_EFFICIENCY = "resource_efficiency"
    BALANCED = "balanced"


class SchedulingMode(enum.Enum):
    """Scheduling mode."""
    DETERMINISTIC = "deterministic"
    LAYA_SHADOW = "laya_shadow"
    LAYA_ASSISTED = "laya_assisted"


class WorkerStatus(enum.Enum):
    """Worker availability status."""
    AVAILABLE = "available"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


class DeviceType(enum.Enum):
    """Compatible device/runtime type."""
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"
    XPU = "xpu"
    ONNX = "onnx"


@dataclass
class WorkerCandidate:
    """A worker node candidate for scheduling."""
    worker_id: str
    is_simulated: bool
    status: WorkerStatus
    device_type: DeviceType
    ram_total_mb: float
    ram_available_mb: float
    accelerator_memory_mb: Optional[float]  # None = not measurable/applicable
    processing_speed_tps: Optional[float]  # tokens per second; None = unmeasured
    network_latency_ms: Optional[float]  # None = local or unmeasured
    current_workload_pct: float  # 0-100
    model_partition_required_mb: float  # memory required for the model partition
    compatible_runtimes: list[DeviceType] = field(default_factory=list)
    label: str = ""  # Human-readable label

    def __post_init__(self):
        if not self.label:
            prefix = "SIM" if self.is_simulated else "LIVE"
            self.label = f"{prefix}-{self.worker_id}"


@dataclass
class CandidateEvaluation:
    """Evaluation result for a single candidate."""
    worker_id: str
    is_feasible: bool
    rejection_reasons: list[str] = field(default_factory=list)
    score: Optional[float] = None  # Higher is better, None if infeasible
    score_components: dict[str, float] = field(default_factory=dict)
    is_simulated: bool = False


@dataclass
class SchedulerResult:
    """Complete result of a scheduling run."""
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    scenario_name: str = ""
    objective: SchedulingObjective = SchedulingObjective.BALANCED
    mode: SchedulingMode = SchedulingMode.DETERMINISTIC

    # All evaluations
    evaluations: list[CandidateEvaluation] = field(default_factory=list)

    # Deterministic selection
    deterministic_choice: Optional[str] = None
    deterministic_score: Optional[float] = None

    # Laya recommendation (shadow or assisted)
    laya_recommendation: Optional[str] = None
    laya_confidence: Optional[float] = None
    laya_used_successfully: bool = False

    # Final decision
    final_choice: Optional[str] = None
    final_score: Optional[float] = None

    # Metadata
    decision_duration_ms: float = 0.0
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    success: bool = False
    failure_reason: Optional[str] = None
    candidate_count: int = 0
    feasible_count: int = 0


@dataclass
class SchedulerFailure:
    """Represents a scheduling failure with diagnostic info."""
    reason: str
    details: str = ""
    candidate_count: int = 0
    feasible_count: int = 0


@dataclass
class Placement:
    """A validated placement decision."""
    worker_id: str
    score: float
    objective: SchedulingObjective
    mode: SchedulingMode
    reason: str
