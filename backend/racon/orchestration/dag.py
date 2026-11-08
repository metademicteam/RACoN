"""Plan validation and dependency ordering.

The scheduler may only dispatch a task after every dependency has completed. This module
is the single authority for that rule: it rejects a plan with unknown dependencies, self
dependencies, duplicate ids, or cycles *before* any work is dispatched.
"""
from __future__ import annotations

from collections import deque
from typing import Iterable, Mapping

from backend.racon.orchestration.schemas import PlanSpec

TERMINAL_TASK_STATES = frozenset({"completed", "failed", "cancelled"})


class DagValidationError(ValueError):
    """Raised when a plan cannot be executed as a directed acyclic graph."""

    def __init__(self, errors: Iterable[str]) -> None:
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def validate_plan(plan: PlanSpec) -> None:
    """Validate dependency integrity and acyclicity. Raises ``DagValidationError``."""
    errors: list[str] = []
    if not plan.tasks:
        raise DagValidationError(["plan contains no tasks"])

    ids = [task.task_id for task in plan.tasks]
    known = set(ids)

    if len(ids) != len(known):
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        errors.append(f"duplicate task ids: {', '.join(duplicates)}")

    for task in plan.tasks:
        for dep in task.depends_on:
            if dep == task.task_id:
                errors.append(f"task '{task.task_id}' depends on itself")
            elif dep not in known:
                errors.append(f"task '{task.task_id}' depends on unknown task '{dep}'")

    if not errors:
        cycle = _find_cycle(plan)
        if cycle:
            errors.append("dependency cycle detected: " + " -> ".join(cycle))

    if errors:
        raise DagValidationError(errors)


def topological_order(plan: PlanSpec) -> list[str]:
    """Return task ids in a deterministic dependency-respecting order."""
    validate_plan(plan)
    indegree = {task.task_id: len(task.depends_on) for task in plan.tasks}
    dependents: dict[str, list[str]] = {task.task_id: [] for task in plan.tasks}
    for task in plan.tasks:
        for dep in task.depends_on:
            dependents[dep].append(task.task_id)

    ready = sorted(task_id for task_id, count in indegree.items() if count == 0)
    queue = deque(ready)
    order: list[str] = []

    while queue:
        current = queue.popleft()
        order.append(current)
        for child in sorted(dependents[current]):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if len(order) != len(indegree):
        raise DagValidationError(["dependency cycle detected"])
    return order


def ready_tasks(plan: PlanSpec, states: Mapping[str, str]) -> list[str]:
    """Return pending tasks whose dependencies have all completed, in plan order.

    ``states`` maps task id to its current state. A task that is already terminal is never
    returned.
    """
    ready: list[str] = []
    for task in plan.tasks:
        if states.get(task.task_id, "pending") != "pending":
            continue
        if all(states.get(dep) == "completed" for dep in task.depends_on):
            ready.append(task.task_id)
    return ready


def blocked_by(plan: PlanSpec, task_id: str, states: Mapping[str, str]) -> list[str]:
    """Return dependencies of ``task_id`` that are not complete (diagnostics)."""
    task = plan.task_map()[task_id]
    return [dep for dep in task.depends_on if states.get(dep) != "completed"]


def _find_cycle(plan: PlanSpec) -> list[str]:
    """Return one cycle as a task-id path, or an empty list when the graph is acyclic."""
    dependencies = {task.task_id: list(task.depends_on) for task in plan.tasks}
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(node: str) -> list[str]:
        if node in visiting:
            start = path.index(node)
            return path[start:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        path.append(node)
        for dep in dependencies.get(node, []):
            cycle = visit(dep)
            if cycle:
                return cycle
        path.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for task in plan.tasks:
        cycle = visit(task.task_id)
        if cycle:
            return cycle
    return []
