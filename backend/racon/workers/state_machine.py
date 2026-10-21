"""Task state machine for RACoN distributed tasks and jobs.

Defines the formal task lifecycle states:
QUEUED -> VALIDATING -> SCHEDULED -> RUNNING -> COMPLETED / FAILED / CANCELLED.
Enforces legal transitions and prevents illegal state mutations.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional, Union

logger = logging.getLogger(__name__)


class TaskState(str, enum.Enum):
    """Lifecycle states of an inference task or computation unit."""
    QUEUED = "QUEUED"
    VALIDATING = "VALIDATING"
    SCHEDULED = "SCHEDULED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


# Formal Transition Matrix
VALID_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.VALIDATING, TaskState.CANCELLED},
    TaskState.VALIDATING: {TaskState.SCHEDULED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.SCHEDULED: {TaskState.RUNNING, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.RUNNING: {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED},
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
}

# Terminal states from which no further transitions may occur
TERMINAL_STATES: frozenset[TaskState] = frozenset(
    {TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
)


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal task state transition is attempted."""

    def __init__(
        self,
        current: Union[str, TaskState],
        next_state: Union[str, TaskState],
        reason: str = "",
    ):
        curr_str = current.value if isinstance(current, TaskState) else str(current)
        next_str = next_state.value if isinstance(next_state, TaskState) else str(next_state)
        msg = f"Invalid task state transition from '{curr_str}' to '{next_str}'."
        if reason:
            msg += f" Reason: {reason}"
        super().__init__(msg)
        self.current_state = curr_str
        self.target_state = next_str
        self.reason = reason


def _coerce_task_state(state: Union[str, TaskState]) -> Optional[TaskState]:
    """Coerce string or TaskState into a canonical TaskState enum member.

    Returns None if the state string is unrecognized.
    """
    if isinstance(state, TaskState):
        return state
    try:
        return TaskState(str(state).strip().upper())
    except (ValueError, KeyError, AttributeError):
        return None


def transition_task(
    current: Union[str, TaskState],
    next_state: Union[str, TaskState],
) -> bool:
    """Validate whether transitioning from current to next_state is legally allowed.

    Args:
        current: Current state string or TaskState enum member.
        next_state: Target state string or TaskState enum member.

    Returns:
        bool: True if the transition is valid according to the state machine,
              False if illegal or if either state name is unrecognized.
    """
    curr_enum = _coerce_task_state(current)
    next_enum = _coerce_task_state(next_state)

    if curr_enum is None or next_enum is None:
        logger.warning(
            "transition_task received unrecognized state: current='%s', next='%s'",
            current,
            next_state,
        )
        return False

    allowed = VALID_TRANSITIONS.get(curr_enum, set())
    return next_enum in allowed


def validate_transition(
    current: Union[str, TaskState],
    next_state: Union[str, TaskState],
    reason: str = "",
) -> TaskState:
    """Enforce a state transition, raising InvalidStateTransitionError if illegal.

    Args:
        current: Current state.
        next_state: Target state.
        reason: Optional context or explanation for the transition.

    Returns:
        TaskState: The validated next TaskState.

    Raises:
        InvalidStateTransitionError: If the transition is prohibited.
    """
    if not transition_task(current, next_state):
        raise InvalidStateTransitionError(current, next_state, reason=reason)
    target = _coerce_task_state(next_state)
    assert target is not None  # Guaranteed by transition_task
    return target


def is_terminal_state(state: Union[str, TaskState]) -> bool:
    """Check if the given state is terminal (COMPLETED, FAILED, CANCELLED)."""
    coerced = _coerce_task_state(state)
    return coerced in TERMINAL_STATES if coerced else False


@dataclass
class StateTransitionRecord:
    """Audit record of an individual state transition."""
    from_state: TaskState
    to_state: TaskState
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary."""
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "timestamp": self.timestamp,
            "reason": self.reason,
        }


class TaskStateMachine:
    """Manages state transitions and audit history for a single task."""

    def __init__(
        self,
        task_id: str,
        initial_state: Union[str, TaskState] = TaskState.QUEUED,
    ):
        """Initialize the task state machine.

        Args:
            task_id: Unique task identifier.
            initial_state: Starting state (defaults to QUEUED).
        """
        self.task_id = task_id
        init = _coerce_task_state(initial_state)
        if init is None:
            raise ValueError(f"Invalid initial task state: {initial_state}")
        self._current_state = init
        self._history: list[StateTransitionRecord] = []
        self._created_at = datetime.now(timezone.utc).isoformat()

    @property
    def current_state(self) -> TaskState:
        """Return the current task state."""
        return self._current_state

    @property
    def history(self) -> list[StateTransitionRecord]:
        """Return the chronological state transition audit trail."""
        return list(self._history)

    def can_transition_to(self, next_state: Union[str, TaskState]) -> bool:
        """Check if transition to next_state is valid from current state."""
        return transition_task(self._current_state, next_state)

    def is_terminal(self) -> bool:
        """Return True if current state is terminal."""
        return self._current_state in TERMINAL_STATES

    def transition_to(
        self,
        next_state: Union[str, TaskState],
        reason: str = "",
    ) -> TaskState:
        """Execute a state transition on this task.

        Args:
            next_state: Target state.
            reason: Optional justification or diagnostic message.

        Returns:
            TaskState: The newly applied state.

        Raises:
            InvalidStateTransitionError: If the transition is prohibited.
        """
        validated_next = validate_transition(self._current_state, next_state, reason=reason)
        record = StateTransitionRecord(
            from_state=self._current_state,
            to_state=validated_next,
            reason=reason,
        )
        self._history.append(record)
        old_state = self._current_state
        self._current_state = validated_next
        logger.info(
            "Task [%s] transitioned from %s to %s (reason: '%s')",
            self.task_id,
            old_state.value,
            validated_next.value,
            reason,
        )
        return self._current_state

    def to_dict(self) -> dict[str, Any]:
        """Serialize state machine status and history."""
        return {
            "task_id": self.task_id,
            "current_state": self._current_state.value,
            "is_terminal": self.is_terminal(),
            "created_at": self._created_at,
            "history": [r.to_dict() for r in self._history],
        }
