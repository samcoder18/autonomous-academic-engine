from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from academic_engine.resource_guards import (
    ResourceGuardError,
    RetryBudget,
    RunGuards,
    StuckDetector,
    TimeoutBudget,
)


class ManualClock:
    def __init__(self, start: datetime | None = None) -> None:
        self.now = start or datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


class TimeoutBudgetTests(unittest.TestCase):
    def test_check_before_start_is_noop(self) -> None:
        budget = TimeoutBudget(limit=timedelta(seconds=10))
        budget.check()

    def test_check_after_limit_raises(self) -> None:
        clock = ManualClock()
        budget = TimeoutBudget(limit=timedelta(seconds=5), label="phase0", _clock=clock)
        budget.start()
        clock.advance(timedelta(seconds=6))
        with self.assertRaises(ResourceGuardError) as ctx:
            budget.check()
        self.assertEqual(ctx.exception.code, "timeout-exceeded")
        self.assertEqual(ctx.exception.details["label"], "phase0")

    def test_remaining(self) -> None:
        clock = ManualClock()
        budget = TimeoutBudget(limit=timedelta(seconds=10), _clock=clock)
        budget.start()
        clock.advance(timedelta(seconds=3))
        self.assertAlmostEqual(budget.remaining().total_seconds(), 7.0)


class StuckDetectorTests(unittest.TestCase):
    def test_triggers_after_threshold(self) -> None:
        clock = ManualClock()
        detector = StuckDetector(stuck_after=timedelta(seconds=10), label="run", _clock=clock)
        detector.checkpoint("start")
        clock.advance(timedelta(seconds=11))
        with self.assertRaises(ResourceGuardError) as ctx:
            detector.check()
        self.assertEqual(ctx.exception.code, "run-stuck")

    def test_reset_by_checkpoint(self) -> None:
        clock = ManualClock()
        detector = StuckDetector(stuck_after=timedelta(seconds=10), _clock=clock)
        detector.checkpoint("start")
        clock.advance(timedelta(seconds=9))
        detector.checkpoint("mid")
        clock.advance(timedelta(seconds=9))
        detector.check()


class RetryBudgetTests(unittest.TestCase):
    def test_freezes_after_max(self) -> None:
        clock = ManualClock()
        budget = RetryBudget(max_retries=2, cooldown=timedelta(minutes=1), _clock=clock)
        self.assertTrue(budget.can_retry("pravo_gov_ru"))
        budget.record_failure("pravo_gov_ru")
        self.assertTrue(budget.can_retry("pravo_gov_ru"))
        budget.record_failure("pravo_gov_ru")
        self.assertFalse(budget.can_retry("pravo_gov_ru"))
        clock.advance(timedelta(minutes=2))
        budget.reset("pravo_gov_ru")
        self.assertTrue(budget.can_retry("pravo_gov_ru"))

    def test_isolated_per_key(self) -> None:
        budget = RetryBudget(max_retries=1)
        budget.record_failure("a")
        self.assertFalse(budget.can_retry("a"))
        self.assertTrue(budget.can_retry("b"))


class RunGuardsTests(unittest.TestCase):
    def test_check_chains(self) -> None:
        clock = ManualClock()
        guards = RunGuards(
            timeout=TimeoutBudget(limit=timedelta(seconds=60), _clock=clock),
            stuck=StuckDetector(stuck_after=timedelta(seconds=5), _clock=clock),
        )
        guards.start()
        clock.advance(timedelta(seconds=6))
        with self.assertRaises(ResourceGuardError) as ctx:
            guards.check()
        self.assertEqual(ctx.exception.code, "run-stuck")


if __name__ == "__main__":
    unittest.main()
