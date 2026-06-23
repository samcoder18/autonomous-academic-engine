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
    JobQueueError,
    WorkflowJobSpec,
)


class JobQueueStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _job_path(self, job_id: str) -> Path:
        jobs_dir = self.root / "output" / "runtime" / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        return jobs_dir / f"{job_id}.json"

    def _valid_job_record(self, job_id: str = "job-demo", **overrides: object) -> dict[str, object]:
        record: dict[str, object] = {
            "kind": "engine-job",
            "version": "job/v1",
            "job_id": job_id,
            "work_id": "demo-work",
            "job_type": "workflow",
            "status": "queued",
            "created_at": "2026-06-23T10:00:00+00:00",
            "updated_at": "2026-06-23T10:00:00+00:00",
            "attempt": 0,
            "max_attempts": 3,
            "workflow_id": None,
            "active_run_id": None,
            "payload": {
                "lane": "article",
                "action": "review",
                "target_or_topic": "draft.md",
                "notes": None,
                "search_override": None,
                "model_override": None,
                "profile_override": None,
            },
            "limits": {
                "global_concurrency": 2,
                "per_work_concurrency": 1,
            },
            "blocked_reason": None,
            "failure": None,
            "history": [
                {
                    "timestamp": "2026-06-23T10:00:00+00:00",
                    "event": "job-submitted",
                    "status": "queued",
                    "details": {},
                }
            ],
        }
        record.update(overrides)
        return record

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

    def test_list_jobs_rejects_unknown_status_filter(self) -> None:
        with self.assertRaises(InvalidJobStateError):
            JobQueue(self.root).list_jobs(status="not-public")

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

    def test_corrupt_job_json_raises_for_get_and_list(self) -> None:
        self._job_path("broken").write_text("{not json", encoding="utf-8")
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "broken.json"):
            queue.get_job("broken")

        with self.assertRaisesRegex(JobQueueError, "broken.json"):
            queue.list_jobs()

    def test_wrong_kind_job_json_raises_for_get_and_list(self) -> None:
        self._job_path("wrong-kind").write_text(
            json.dumps({"kind": "not-engine-job", "job_id": "wrong-kind", "status": "queued"}),
            encoding="utf-8",
        )
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "wrong-kind.json"):
            queue.get_job("wrong-kind")

        with self.assertRaisesRegex(JobQueueError, "wrong-kind.json"):
            queue.list_jobs()

    def test_invalid_loaded_status_fails_closed(self) -> None:
        record = self._valid_job_record(job_id="bad-status", status="mystery")
        path = self._job_path("bad-status")
        path.write_text(json.dumps(record), encoding="utf-8")
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(InvalidJobStateError, "bad-status.json"):
            queue.list_jobs()

        with self.assertRaisesRegex(InvalidJobStateError, "bad-status.json"):
            queue.cancel_job("bad-status")

        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["status"], "mystery")
        self.assertEqual(stored["history"], record["history"])

    def test_filename_payload_job_id_mismatch_fails_closed_without_payload_id_write(self) -> None:
        foo_path = self._job_path("foo")
        foo_path.write_text(json.dumps(self._valid_job_record(job_id="bar")), encoding="utf-8")
        bar_record = self._valid_job_record(job_id="bar", work_id="untouched-work")
        bar_path = self._job_path("bar")
        bar_path.write_text(json.dumps(bar_record), encoding="utf-8")
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "foo.json"):
            queue.cancel_job("foo")

        self.assertEqual(json.loads(bar_path.read_text(encoding="utf-8")), bar_record)

    def test_wrong_version_job_record_raises(self) -> None:
        self._job_path("wrong-version").write_text(
            json.dumps(self._valid_job_record(job_id="wrong-version", version="job/v0")),
            encoding="utf-8",
        )
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "wrong-version.json"):
            queue.get_job("wrong-version")

        with self.assertRaisesRegex(JobQueueError, "wrong-version.json"):
            queue.list_jobs()

    def test_missing_required_job_record_field_raises(self) -> None:
        record = self._valid_job_record(job_id="missing-payload")
        del record["payload"]
        self._job_path("missing-payload").write_text(json.dumps(record), encoding="utf-8")
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "missing-payload.json"):
            queue.get_job("missing-payload")

        with self.assertRaisesRegex(JobQueueError, "missing-payload.json"):
            queue.list_jobs()

    def test_loaded_record_with_invalid_limits_values_raises(self) -> None:
        self._job_path("bad-global-limit").write_text(
            json.dumps(
                self._valid_job_record(
                    job_id="bad-global-limit",
                    limits={"global_concurrency": "2", "per_work_concurrency": 1},
                )
            ),
            encoding="utf-8",
        )
        self._job_path("bad-work-limit").write_text(
            json.dumps(
                self._valid_job_record(
                    job_id="bad-work-limit",
                    limits={"global_concurrency": 2, "per_work_concurrency": None},
                )
            ),
            encoding="utf-8",
        )
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "bad-global-limit.json"):
            queue.get_job("bad-global-limit")

        with self.assertRaisesRegex(JobQueueError, "bad-global-limit.json|bad-work-limit.json"):
            queue.list_jobs()

    def test_loaded_record_with_non_dict_history_item_raises(self) -> None:
        self._job_path("bad-history-item").write_text(
            json.dumps(self._valid_job_record(job_id="bad-history-item", history=["not-an-event"])),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(JobQueueError, "bad-history-item.json"):
            JobQueue(self.root).get_job("bad-history-item")

    def test_loaded_record_with_invalid_history_event_shape_raises(self) -> None:
        missing_event = self._valid_job_record(
            job_id="missing-history-event",
            history=[
                {
                    "timestamp": "2026-06-23T10:00:00+00:00",
                    "status": "queued",
                    "details": {},
                }
            ],
        )
        invalid_status = self._valid_job_record(
            job_id="invalid-history-status",
            history=[
                {
                    "timestamp": "2026-06-23T10:00:00+00:00",
                    "event": "job-submitted",
                    "status": "mystery",
                    "details": {},
                }
            ],
        )
        self._job_path("missing-history-event").write_text(json.dumps(missing_event), encoding="utf-8")
        self._job_path("invalid-history-status").write_text(json.dumps(invalid_status), encoding="utf-8")
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "missing-history-event.json"):
            queue.get_job("missing-history-event")

        with self.assertRaisesRegex(JobQueueError, "invalid-history-status.json"):
            queue.get_job("invalid-history-status")

    def test_loaded_record_with_non_workflow_job_type_raises(self) -> None:
        self._job_path("bad-job-type").write_text(
            json.dumps(self._valid_job_record(job_id="bad-job-type", job_type="not-workflow")),
            encoding="utf-8",
        )
        queue = JobQueue(self.root)

        with self.assertRaisesRegex(JobQueueError, "bad-job-type.json"):
            queue.get_job("bad-job-type")

        with self.assertRaisesRegex(JobQueueError, "bad-job-type.json"):
            queue.list_jobs()

    def test_loaded_record_with_invalid_optional_payload_fields_raises(self) -> None:
        cases = [
            ("bad-notes", {"notes": 123}),
            ("bad-search-override", {"search_override": "yes"}),
            ("bad-model-override", {"model_override": 123}),
            ("bad-profile-override", {"profile_override": False}),
        ]

        for job_id, payload_overrides in cases:
            record = self._valid_job_record(job_id=job_id)
            payload = dict(record["payload"])  # type: ignore[arg-type]
            payload.update(payload_overrides)
            record["payload"] = payload
            self._job_path(job_id).write_text(json.dumps(record), encoding="utf-8")

            with self.subTest(job_id=job_id):
                with self.assertRaisesRegex(JobQueueError, f"{job_id}.json"):
                    JobQueue(self.root).get_job(job_id)

    def test_invalid_job_id_from_factory_is_rejected(self) -> None:
        queue = JobQueue(self.root, id_factory=lambda: "bad/id")

        with self.assertRaisesRegex(JobQueueError, "bad/id"):
            queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))

    def test_duplicate_job_id_is_rejected_without_overwrite(self) -> None:
        queue = JobQueue(self.root, id_factory=lambda: "job-demo")
        first = queue.submit_workflow(WorkflowJobSpec("first-work", "article", "review", "draft.md"))

        with self.assertRaisesRegex(JobQueueError, "job-demo"):
            queue.submit_workflow(WorkflowJobSpec("second-work", "article", "review", "draft.md"))

        stored = json.loads(self._job_path("job-demo").read_text(encoding="utf-8"))
        self.assertEqual(stored, first)

    def test_submit_workflow_rejects_invalid_max_attempts_without_writing(self) -> None:
        queue = JobQueue(self.root, id_factory=lambda: "bad-max-attempts")

        with self.assertRaises(JobQueueError):
            queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md", max_attempts=0))

        self.assertFalse(self._job_path("bad-max-attempts").exists())

    def test_submit_workflow_rejects_invalid_concurrency_without_writing(self) -> None:
        queue = JobQueue(self.root, id_factory=lambda: "bad-global-concurrency")

        with self.assertRaises(JobQueueError):
            queue.submit_workflow(
                WorkflowJobSpec(
                    "demo-work",
                    "article",
                    "review",
                    "draft.md",
                    global_concurrency="2",  # type: ignore[arg-type]
                )
            )

        self.assertFalse(self._job_path("bad-global-concurrency").exists())

    def test_submit_workflow_rejects_non_string_core_field_without_writing(self) -> None:
        queue = JobQueue(self.root, id_factory=lambda: "bad-work-id")

        with self.assertRaises(JobQueueError):
            queue.submit_workflow(WorkflowJobSpec(123, "article", "review", "draft.md"))  # type: ignore[arg-type]

        self.assertFalse(self._job_path("bad-work-id").exists())

    def test_submit_workflow_rejects_invalid_optional_payload_fields_without_writing(self) -> None:
        bad_notes = JobQueue(self.root, id_factory=lambda: "bad-notes")
        with self.assertRaises(JobQueueError):
            bad_notes.submit_workflow(
                WorkflowJobSpec("demo-work", "article", "review", "draft.md", notes=123)  # type: ignore[arg-type]
            )
        self.assertFalse(self._job_path("bad-notes").exists())

        bad_search = JobQueue(self.root, id_factory=lambda: "bad-search-override")
        with self.assertRaises(JobQueueError):
            bad_search.submit_workflow(
                WorkflowJobSpec(
                    "demo-work",
                    "article",
                    "review",
                    "draft.md",
                    search_override="yes",  # type: ignore[arg-type]
                )
            )
        self.assertFalse(self._job_path("bad-search-override").exists())

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

    def test_cancel_running_job_calls_stop_hook_and_records_result(self) -> None:
        calls: list[tuple[Path, str, str]] = []

        def stop_job(root_dir: Path, work_id: str, reason: str) -> dict[str, object]:
            calls.append((root_dir, work_id, reason))
            return {"stopped": True, "reason": reason}

        queue = JobQueue(self.root, stop_job_func=stop_job)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        queue._transition_for_test(job["job_id"], status="running")

        blocked = queue.cancel_job(job["job_id"], reason="operator-cancelled")

        self.assertEqual(calls, [(queue.root_dir, "demo-work", "operator-cancelled")])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(
            blocked["history"][-1]["details"],
            {"stop_result": {"stopped": True, "reason": "operator-cancelled"}},
        )

    def test_cancel_running_job_stop_hook_error_does_not_mutate_job(self) -> None:
        def stop_job(root_dir: Path, work_id: str, reason: str) -> dict[str, object]:
            raise RuntimeError("stop failed")

        queue = JobQueue(self.root, stop_job_func=stop_job)
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        running = queue._transition_for_test(job["job_id"], status="running")

        with self.assertRaisesRegex(RuntimeError, "stop failed"):
            queue.cancel_job(job["job_id"], reason="operator-cancelled")

        stored = queue.get_job(job["job_id"])
        self.assertEqual(stored["status"], "running")
        self.assertEqual(stored["updated_at"], running["updated_at"])
        self.assertNotEqual(stored["history"][-1]["event"], "job-cancelled")

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
