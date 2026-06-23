from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from academic_engine.job_queue import (
    TERMINAL_JOB_STATUSES,
    InvalidJobStateError,
    JobNotFoundError,
    JobQueue,
    WorkflowJobSpec,
)


class JobQueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_submit_workflow_persists_queued_job(self) -> None:
        queue = JobQueue(self.root, now=lambda: "2026-06-23T10:00:00+00:00", id_factory=lambda: "job-demo")

        job = queue.submit_workflow(
            WorkflowJobSpec(
                work_id="demo-work",
                lane="thesis",
                action="write-section",
                target_or_topic="thesis/manuscript/sections/01.md",
                notes="draft carefully",
                search_override=True,
                model_override="test-model",
                profile_override=None,
            )
        )

        self.assertEqual(job["kind"], "engine-job")
        self.assertEqual(job["version"], "job/v1")
        self.assertEqual(job["job_id"], "job-demo")
        self.assertEqual(job["work_id"], "demo-work")
        self.assertEqual(job["job_type"], "workflow")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempt"], 0)
        self.assertEqual(job["max_attempts"], 3)
        self.assertIsNone(job["workflow_id"])
        self.assertEqual(job["payload"]["lane"], "thesis")
        self.assertEqual(job["payload"]["action"], "write-section")
        self.assertEqual(job["payload"]["target_or_topic"], "thesis/manuscript/sections/01.md")
        self.assertEqual(job["payload"]["notes"], "draft carefully")
        self.assertTrue(job["payload"]["search_override"])
        self.assertEqual(job["payload"]["model_override"], "test-model")
        self.assertEqual(job["limits"], {"global_concurrency": 2, "per_work_concurrency": 1})
        self.assertEqual(job["history"][0]["event"], "job-submitted")

        stored = json.loads((self.root / "output" / "runtime" / "jobs" / "job-demo.json").read_text())
        self.assertEqual(stored, job)

    def test_list_jobs_filters_by_work_and_status(self) -> None:
        queue = JobQueue(self.root)
        first = queue.submit_workflow(WorkflowJobSpec("alpha", "thesis", "verify", "section-1"))
        second = queue.submit_workflow(WorkflowJobSpec("beta", "article", "review", "draft.md"))
        queue.cancel_job(second["job_id"], reason="operator-cancelled")

        self.assertEqual([item["job_id"] for item in queue.list_jobs(work_id="alpha")], [first["job_id"]])
        self.assertEqual([item["job_id"] for item in queue.list_jobs(status="blocked")], [second["job_id"]])

    def test_list_jobs_orders_by_created_at_then_job_id(self) -> None:
        ids = iter(["job-c", "job-b", "job-a"])
        timestamps = iter(
            [
                "2026-06-23T10:02:00+00:00",
                "2026-06-23T10:01:00+00:00",
                "2026-06-23T10:01:00+00:00",
            ]
        )
        queue = JobQueue(self.root, id_factory=lambda: next(ids), now=lambda: next(timestamps))

        queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-c"))
        queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-b"))
        queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-a"))

        self.assertEqual([item["job_id"] for item in queue.list_jobs()], ["job-a", "job-b", "job-c"])

    def test_get_job_rejects_unknown_id(self) -> None:
        with self.assertRaises(JobNotFoundError):
            JobQueue(self.root).get_job("missing-job")

    def test_terminal_statuses_exclude_resumable_blocked_jobs(self) -> None:
        self.assertEqual(TERMINAL_JOB_STATUSES, {"failed", "completed"})

    def test_cancel_retry_and_resume_use_public_states(self) -> None:
        queue = JobQueue(self.root, now=lambda: "2026-06-23T10:00:00+00:00")
        queued = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))

        blocked = queue.cancel_job(queued["job_id"], reason="operator-cancelled")
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blocked_reason"], "operator-cancelled")
        self.assertEqual(blocked["history"][-1]["event"], "job-cancelled")

        resumed = queue.resume_job(queued["job_id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertIsNone(resumed["blocked_reason"])
        self.assertEqual(resumed["history"][-1]["event"], "job-resumed")

        failed = queue._transition_for_test(queued["job_id"], status="failed", failure={"code": "boom"})
        self.assertEqual(failed["status"], "failed")
        retried = queue.retry_job(queued["job_id"])
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["attempt"], 1)
        self.assertIsNone(retried["failure"])
        self.assertEqual(retried["history"][-1]["event"], "job-retried")

    def test_cancel_running_job_blocks_and_records_history(self) -> None:
        queue = JobQueue(self.root)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        queue._transition_for_test(job["job_id"], status="running")

        blocked = queue.cancel_job(job["job_id"], reason="operator-cancelled")

        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blocked_reason"], "operator-cancelled")
        self.assertEqual(blocked["history"][-1]["event"], "job-cancelled")

    def test_cancel_terminal_jobs_fail_closed(self) -> None:
        queue = JobQueue(self.root)
        completed = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "done.md"))
        failed = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "failed.md"))
        queue._transition_for_test(completed["job_id"], status="completed")
        queue._transition_for_test(failed["job_id"], status="failed", failure={"code": "boom"})

        with self.assertRaises(InvalidJobStateError):
            queue.cancel_job(completed["job_id"])

        with self.assertRaises(InvalidJobStateError):
            queue.cancel_job(failed["job_id"])

    def test_retry_clears_run_fields_and_failure(self) -> None:
        queue = JobQueue(self.root)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        job["workflow_id"] = "workflow-demo"
        job["active_run_id"] = "run-demo"
        queue._transition(job, status="failed", event="job-failed", failure={"code": "boom"})

        retried = queue.retry_job(job["job_id"])

        self.assertEqual(retried["status"], "queued")
        self.assertIsNone(retried["workflow_id"])
        self.assertIsNone(retried["active_run_id"])
        self.assertIsNone(retried["failure"])
        self.assertEqual(retried["history"][-1]["event"], "job-retried")

    def test_resume_clears_run_fields_and_blocked_reason(self) -> None:
        queue = JobQueue(self.root)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        job["workflow_id"] = "workflow-demo"
        job["active_run_id"] = "run-demo"
        queue._transition(job, status="blocked", event="job-blocked", blocked_reason="waiting")

        resumed = queue.resume_job(job["job_id"])

        self.assertEqual(resumed["status"], "queued")
        self.assertIsNone(resumed["workflow_id"])
        self.assertIsNone(resumed["active_run_id"])
        self.assertIsNone(resumed["blocked_reason"])
        self.assertEqual(resumed["history"][-1]["event"], "job-resumed")

    def test_retry_exhaustion_allows_final_attempt_then_rejects(self) -> None:
        queue = JobQueue(self.root)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md", max_attempts=3))
        job["attempt"] = 2
        queue._transition(job, status="failed", event="job-failed", failure={"code": "boom"})

        retried = queue.retry_job(job["job_id"])
        self.assertEqual(retried["attempt"], 3)
        self.assertEqual(retried["status"], "queued")

        queue._transition_for_test(job["job_id"], status="failed", failure={"code": "still-boom"})
        with self.assertRaises(InvalidJobStateError):
            queue.retry_job(job["job_id"])

    def test_invalid_retry_and_resume_states_fail_closed(self) -> None:
        queue = JobQueue(self.root)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))

        with self.assertRaises(InvalidJobStateError):
            queue.retry_job(job["job_id"])

        with self.assertRaises(InvalidJobStateError):
            queue.resume_job(job["job_id"])
