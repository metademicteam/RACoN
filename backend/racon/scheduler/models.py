"""Data models and enums for the scheduling engine and API schemas."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SchedulingObjective(str, enum.Enum):
    """Supported multi-objective scheduling goals."""
    LOWEST_LATENCY = "lowest_latency"
    HIGHEST_THROUGHPUT = "highest_throughput"
    RESOURCE_EFFICIENCY = "resource_efficiency"
    BALANCED = "balanced"


class SchedulingMode(str, enum.Enum):
    """Scheduling decision modes."""
    DETERMINISTIC = "deterministic"
    LAYA_SHADOW = "laya_shadow"
    LAYA_ASSISTED = "laya_assisted"


class WorkerStatus(str, enum.Enum):
    """Worker availability status."""
    AVAILABLE = "available"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


class DeviceType(str, enum.Enum):
    """Worker device or runtime compute target."""
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"
    XPU = "xpu"
    ONNX = "onnx"


class WorkerCandidate(BaseModel):
    """A candidate worker node evaluated during a scheduling decision."""
    model_config = ConfigDict(arbitrary_types_allowed=True, use_enum_values=False, populate_by_name=True)

    worker_id: str
    is_simulated: bool = False
    status: WorkerStatus = WorkerStatus.AVAILABLE
    device_type: DeviceType = DeviceType.CPU
    ram_total_mb: float
    ram_available_mb: float
    accelerator_memory_mb: Optional[float] = None  # None = unmeasured / not applicable
    processing_speed_tps: Optional[float] = None  # Tokens per second; None = unmeasured
    network_latency_ms: Optional[float] = None  # None = local or unmeasured
    current_workload_pct: float = 0.0  # 0.0 to 100.0%
    model_partition_required_mb: float = 0.0  # Required memory for model partition
    compatible_runtimes: list[DeviceType] = Field(default_factory=list)
    label: str = ""

    @model_validator(mode="after")
    def populate_label(self) -> WorkerCandidate:
        if not self.label:
            prefix = "SIM" if self.is_simulated else "LIVE"
            self.label = f"{prefix}-{self.worker_id}"
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert candidate to dictionary representation."""
        return self.model_dump()


class CandidateEvaluation(BaseModel):
    """Evaluation result and constraint validation for a single worker candidate."""
    model_config = ConfigDict(arbitrary_types_allowed=True, use_enum_values=False)

    worker_id: str
    is_feasible: bool = True
    rejection_reasons: list[str] = Field(default_factory=list)
    score: Optional[float] = None  # Higher is better; None if infeasible
    score_components: dict[str, float] = Field(default_factory=dict)
    is_simulated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class SchedulerResult(BaseModel):
    """Complete result and audit trace of a scheduling decision run."""
    model_config = ConfigDict(arbitrary_types_allowed=True, use_enum_values=False)

    run_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    scenario_name: str = ""
    objective: SchedulingObjective = SchedulingObjective.BALANCED
    mode: SchedulingMode = SchedulingMode.DETERMINISTIC

    # Candidate evaluations
    evaluations: list[CandidateEvaluation] = Field(default_factory=list)

    # Deterministic choice
    deterministic_choice: Optional[str] = None
    deterministic_score: Optional[float] = None

    # Laya advisor recommendation (Shadow or Assisted)
    laya_recommendation: Optional[str] = None
    laya_confidence: Optional[float] = None
    laya_used_successfully: bool = False

    # Final decision output
    final_choice: Optional[str] = None
    final_score: Optional[float] = None

    # Performance & Diagnostics
    decision_duration_ms: float = 0.0
    fallback_used: bool = False
    fallback_reason: Optional[str] = None
    success: bool = False
    failure_reason: Optional[str] = None
    candidate_count: int = 0
    feasible_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class SchedulerFailure(BaseModel):
    """Diagnostic details when no valid worker could be scheduled."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    reason: str
    details: str = ""
    candidate_count: int = 0
    feasible_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class Placement(BaseModel):
    """A validated, finalized placement decision."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    worker_id: str
    score: float
    objective: SchedulingObjective
    mode: SchedulingMode
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()
