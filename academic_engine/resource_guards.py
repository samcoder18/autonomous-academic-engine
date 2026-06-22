"""Generic resource guards for long-running workflow runs.

Scope:

- **Timeouts** — absolute wall-clock budgets per run / per phase.
- **Stuck detector** — flag runs with no checkpoint progress for N
  minutes; triggers ops alerts and gives the orchestrator a deterministic
  kill signal.
- **Retry budgets** — bounded per-connector retry counters with cooldown
  (used by :mod:`academic_engine.sources` in Phase 1).

All guards are **pure data + clock-injected helpers** so they compose
with the existing autonomous daemon without spawning extra threads.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ResourceGuardError(RuntimeError):
    """Raised when a guard decides the run must terminate."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": dict(self.details)}


@dataclass
class TimeoutBudget:
    """Enforces a total wall-clock budget.

    ``check()`` returns silently while the budget holds and raises
    :class:`ResourceGuardError` the first time it is exceeded.
    """

    limit: timedelta
    label: str = "run"
    started_at: datetime | None = None
    _clock: Clock = field(default=_utcnow, repr=False)

    def start(self) -> None:
        self.started_at = self._clock()

    def elapsed(self) -> timedelta:
        if self.started_at is None:
            return timedelta(0)
        return self._clock() - self.started_at

    def remaining(self) -> timedelta:
        return max(self.limit - self.elapsed(), timedelta(0))

    def check(self) -> None:
        if self.started_at is None:
            return
        if self.elapsed() > self.limit:
            raise ResourceGuardError(
                code="timeout-exceeded",
                message=(
                    f"{self.label} exceeded the wall-clock budget "
                    f"({self.elapsed().total_seconds():.0f}s > {self.limit.total_seconds():.0f}s)."
                ),
                details={
                    "label": self.label,
                    "elapsed_s": round(self.elapsed().total_seconds(), 1),
                    "limit_s": round(self.limit.total_seconds(), 1),
                },
            )


@dataclass
class StuckDetector:
    """Flags runs that haven't reported progress for ``stuck_after``.

    The orchestrator calls :meth:`checkpoint` whenever a meaningful
    state transition happens (new artifact written, skill finished,
    connector response received). :meth:`check` raises when no
    checkpoint has arrived within the window.
    """

    stuck_after: timedelta
    label: str = "run"
    last_checkpoint: datetime | None = None
    last_note: str | None = None
    _clock: Clock = field(default=_utcnow, repr=False)

    def checkpoint(self, note: str = "") -> None:
        self.last_checkpoint = self._clock()
        self.last_note = note or None

    def idle_for(self) -> timedelta:
        if self.last_checkpoint is None:
            return timedelta(0)
        return self._clock() - self.last_checkpoint

    def check(self) -> None:
        if self.last_checkpoint is None:
            return
        if self.idle_for() > self.stuck_after:
            raise ResourceGuardError(
                code="run-stuck",
                message=(
                    f"{self.label} produced no checkpoint for "
                    f"{self.idle_for().total_seconds():.0f}s "
                    f"(threshold {self.stuck_after.total_seconds():.0f}s)."
                ),
                details={
                    "label": self.label,
                    "idle_s": round(self.idle_for().total_seconds(), 1),
                    "last_note": self.last_note,
                },
            )


@dataclass
class RetryBudget:
    """Bounded retries per key with cooldown after exhaustion.

    Used by connectors so that an outage in one data source cannot
    monopolise the daemon retry loop.
    """

    max_retries: int
    cooldown: timedelta = field(default_factory=lambda: timedelta(minutes=5))
    counters: dict[str, int] = field(default_factory=dict)
    frozen_until: dict[str, datetime] = field(default_factory=dict)
    _clock: Clock = field(default=_utcnow, repr=False)

    def can_retry(self, key: str) -> bool:
        frozen = self.frozen_until.get(key)
        if frozen and self._clock() < frozen:
            return False
        return self.counters.get(key, 0) < self.max_retries

    def record_failure(self, key: str) -> None:
        current = self.counters.get(key, 0) + 1
        self.counters[key] = current
        if current >= self.max_retries:
            self.frozen_until[key] = self._clock() + self.cooldown

    def reset(self, key: str) -> None:
        self.counters.pop(key, None)
        self.frozen_until.pop(key, None)

    def summary(self) -> dict[str, Any]:
        return {
            "max_retries": self.max_retries,
            "cooldown_s": self.cooldown.total_seconds(),
            "counters": dict(self.counters),
            "frozen_until": {key: value.isoformat() for key, value in self.frozen_until.items()},
        }


@dataclass
class RunGuards:
    """Bundle used by :mod:`academic_engine.one_shot`."""

    timeout: TimeoutBudget
    stuck: StuckDetector
    retries: RetryBudget | None = None

    def start(self) -> None:
        self.timeout.start()
        self.stuck.checkpoint("run-start")

    def checkpoint(self, note: str = "") -> None:
        self.stuck.checkpoint(note)

    def check(self) -> None:
        self.timeout.check()
        self.stuck.check()


def sleep_with_guard(guard: RunGuards, seconds: float) -> None:
    """Sleep in small slices so guard timeouts fire promptly."""
    slice_size = 1.0
    remaining = seconds
    while remaining > 0:
        step = min(slice_size, remaining)
        time.sleep(step)
        guard.check()
        remaining -= step
