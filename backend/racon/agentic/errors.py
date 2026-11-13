"""Errors for the Together AI agentic layer."""

from __future__ import annotations


class AgenticUnavailable(RuntimeError):
    """The layer is disabled or cannot be reached; the caller should use the old path."""


class AgenticRunError(RuntimeError):
    """The layer ran but could not produce a usable answer."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "agent_failed",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class TogetherError(RuntimeError):
    """A structured Together AI request failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        code: str = "together_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable

    def to_dict(self) -> dict:
        return {
            "message": str(self),
            "code": self.code,
            "status_code": self.status_code,
            "retryable": self.retryable,
        }
