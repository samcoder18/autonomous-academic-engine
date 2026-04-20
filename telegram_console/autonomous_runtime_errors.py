"""Structured runtime errors for daemon/scheduler orchestration."""

from __future__ import annotations

from typing import Any

from .orchestrator_support import WorkflowError
from .workspace import WorkspaceConfigError


class AutonomousRuntimeError(RuntimeError):
    """Base error for runtime-facing daemon/scheduler failures."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        blocking_categories: tuple[str, ...] = ("runtime",),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.blocking_categories = blocking_categories

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
            "blocking_categories": list(self.blocking_categories),
        }


class SchedulerWorkCandidateError(AutonomousRuntimeError):
    """Raised when one work cannot be evaluated as a daemon candidate."""


def classify_scheduler_candidate_error(
    exc: BaseException,
    *,
    work_id: str,
    stage: str,
) -> SchedulerWorkCandidateError:
    details = {
        "work_id": work_id,
        "stage": stage,
        "error_type": type(exc).__name__,
    }
    if isinstance(exc, (WorkflowError, WorkspaceConfigError)):
        return SchedulerWorkCandidateError(
            "work-state-error",
            str(exc),
            details=details,
            blocking_categories=("work-state",),
        )
    if isinstance(exc, FileNotFoundError | NotADirectoryError | PermissionError):
        return SchedulerWorkCandidateError(
            "work-state-io-error",
            str(exc),
            details=details,
            blocking_categories=("work-state", "runtime"),
        )
    if isinstance(exc, ValueError):
        return SchedulerWorkCandidateError(
            "work-state-value-error",
            str(exc),
            details=details,
            blocking_categories=("work-state", "runtime"),
        )
    if isinstance(exc, RuntimeError):
        return SchedulerWorkCandidateError(
            "work-state-runtime-error",
            str(exc),
            details=details,
            blocking_categories=("work-state", "runtime"),
        )
    if isinstance(exc, OSError):
        return SchedulerWorkCandidateError(
            "work-state-os-error",
            str(exc),
            details=details,
            blocking_categories=("work-state", "runtime"),
        )
    return SchedulerWorkCandidateError(
        "unexpected-candidate-error",
        f"{type(exc).__name__}: {exc}",
        details=details,
        blocking_categories=("runtime", "unexpected"),
    )
