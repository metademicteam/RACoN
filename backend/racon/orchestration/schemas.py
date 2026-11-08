"""Typed task-plan contract with hard bounds.

Plans are model-generated and therefore untrusted. Every field is length-bounded and task
ids are restricted to a slug alphabet so a plan can never inject paths, URLs, or commands
into later stages.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_TASKS = 12
MAX_TASK_ID_CHARS = 32
MAX_TITLE_CHARS = 120
MAX_INSTRUCTION_CHARS = 6000
MAX_SYNTHESIS_CHARS = 4000
MAX_DEPENDENCIES = MAX_TASKS
MAX_CAPABILITIES = 8
MAX_OUTPUT_CHARS = 20000
MAX_CONTEXT_CHARS = 12000

TASK_ID_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"


class TaskRequirements(BaseModel):
    """What a worker must provide to execute a task."""

    model_config = ConfigDict(extra="forbid")

    capabilities: list[str] = Field(default_factory=lambda: ["text_generation"], max_length=MAX_CAPABILITIES)
    model: Optional[str] = Field(default=None, max_length=128)
    min_ram_mb: float = Field(default=0.0, ge=0.0, le=1_048_576.0)


class TaskSpec(BaseModel):
    """One independently executable unit of work in a plan."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(pattern=TASK_ID_PATTERN)
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION_CHARS)
    depends_on: list[str] = Field(default_factory=list, max_length=MAX_DEPENDENCIES)
    requirements: TaskRequirements = Field(default_factory=TaskRequirements)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    timeout_s: float = Field(default=180.0, gt=0.0, le=900.0)
    retries: int = Field(default=1, ge=0, le=3)

    @field_validator("task_id")
    @classmethod
    def _normalise_task_id(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("depends_on")
    @classmethod
    def _unique_dependencies(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for dep in value:
            cleaned = dep.strip().lower()
            if cleaned and cleaned not in seen:
                seen.append(cleaned)
        return seen


class PlanSpec(BaseModel):
    """A validated, acyclic plan produced by the planner."""

    model_config = ConfigDict(extra="forbid")

    objective: str = Field(default="", max_length=500)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=MAX_TASKS)
    synthesis: str = Field(default="", max_length=MAX_SYNTHESIS_CHARS)
    planner_mode: Literal["llm", "fallback_single"] = "llm"
    planner_note: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def _distinct_task_ids(self) -> "PlanSpec":
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            duplicates = sorted({item for item in ids if ids.count(item) > 1})
            raise ValueError(f"duplicate task ids: {', '.join(duplicates)}")
        return self

    def task_map(self) -> dict[str, TaskSpec]:
        return {task.task_id: task for task in self.tasks}


class PlanRequest(BaseModel):
    """The user-facing request handed to the planner."""

    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=MAX_INSTRUCTION_CHARS)
    model: str = Field(min_length=1, max_length=128)
    # Planner and synthesizer run on the coordinator's local engine; task workers may
    # advertise a different model. Empty means "use model".
    planner_model: Optional[str] = Field(default=None, max_length=128)
    synthesis_model: Optional[str] = Field(default=None, max_length=128)
    max_tokens: int = Field(default=512, ge=1, le=4096)
    decompose: bool = True
    execution_mode: Literal["auto", "local", "distributed"] = "auto"
    max_parallel_tasks: int = Field(default=3, ge=1, le=MAX_TASKS)
    # When true (network gateway), worker deltas are published to the job stream.
    stream: bool = False
    context: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    # Persisted with the plan so the scheduler panel can show why a model was chosen.
    router: dict[str, Any] = Field(default_factory=dict)


def single_task_plan(prompt: str, *, reason: str = "", max_tokens: int = 512) -> PlanSpec:
    """Honest fallback: execute the request as one task instead of pretending to decompose it."""
    return PlanSpec(
        objective="Execute the request without decomposition.",
        tasks=[
            TaskSpec(
                task_id="task-1",
                title="Answer the request",
                instruction=prompt,
                depends_on=[],
                max_tokens=max(1, min(int(max_tokens), 4096)),
            )
        ],
        synthesis="Return the single task result as the answer.",
        planner_mode="fallback_single",
        planner_note=reason[:1000] if reason else "Planning was skipped; one task executes the whole request.",
    )
