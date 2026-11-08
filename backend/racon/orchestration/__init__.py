"""Agentic orchestration: planning, DAG validation, dispatch, and synthesis.

The desktop client submits a request; this package turns it into a persisted task graph,
executes independent tasks across workers, validates results, and synthesizes a final
answer. It is deliberately server-side: client code never makes trusted placement
decisions.
"""
from backend.racon.orchestration.dag import (
    DagValidationError,
    ready_tasks,
    topological_order,
    validate_plan,
)
from backend.racon.orchestration.schemas import (
    MAX_TASKS,
    PlanRequest,
    PlanSpec,
    TaskRequirements,
    TaskSpec,
)

__all__ = [
    "DagValidationError",
    "MAX_TASKS",
    "PlanRequest",
    "PlanSpec",
    "TaskRequirements",
    "TaskSpec",
    "ready_tasks",
    "topological_order",
    "validate_plan",
]
