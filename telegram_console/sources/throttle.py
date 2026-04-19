"""Per-connector throttling with monotonic-clock injection for tests."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

MonotonicClock = Callable[[], float]


@dataclass
class ConnectorThrottle:
    """Enforces a minimum interval between calls for a single connector."""

    min_interval_s: float
    last_call_at: float | None = None
    _clock: MonotonicClock = field(default=time.monotonic, repr=False)
    _sleeper: Callable[[float], None] = field(default=time.sleep, repr=False)

    def wait_if_needed(self) -> None:
        if self.last_call_at is None:
            self.last_call_at = self._clock()
            return
        elapsed = self._clock() - self.last_call_at
        remaining = self.min_interval_s - elapsed
        if remaining > 0:
            self._sleeper(remaining)
        self.last_call_at = self._clock()
