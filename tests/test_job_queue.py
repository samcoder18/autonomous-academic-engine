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


class FakeStore:
    def __init__(self) -> None:
        self.active_runs: list[dict[str, object]] = []

    def list_active_runs(self) -> list[dict[str, object]]:
        return list(self.active_runs)


class FakeOrchestrator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.store = FakeStore()
        self.start_calls: list[dict[str, object]] = []
        self.raise_on_start: Exception | None = None
        self.raise_busy = False

    def sync_active_run(self, work_id: str | None = None) -> list[dict[str, object]]:
        return []

    def start_run(
        self,
        lane: str,
        action: str,
        target_or_topic: str,
        *,
        notes: str | None = None,
        search_override: bool | None = None,
        model_override: str | None = None,
        profile_override: str | None = None,
        work_id: str | None = None,
    ) -> dict[str, object]:
        if self.raise_busy:
            from academic_engine.orchestrator_support import RunBusyError

            raise RunBusyError("busy")
        if self.raise_on_start is not None:
            raise self.raise_on_start
        workflow_id = f"wf-{len(self.start_calls) + 1}"
        run_id = f"run-{len(self.start_calls) + 1}"
        self.start_calls.append(
            {
                "lane": lane,
                "action": action,
                "target_or_topic": target_or_topic,
                "notes": notes,
                "search_override": search_override,
                "model_override": model_override,
                "profile_override": profile_override,
                "work_id": work_id,
            }
        )
        return {
            "workflow_id": workflow_id,
            "run_id": run_id,
            "status": "queued",
            "work_id": work_id,
            "lane": lane,
            "action": action,
        }


def _write_workflow(
    root: Path,
    workflow_id: str,
    *,
    execution_status: str,
    work_id: str = "demo-work",
) -> None:
    workflow_dir = root / "output" / "runs" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    status = "completed" if execution_status == "succeeded" else execution_status
    if execution_status == "failed":
        status = "failed"
    (workflow_dir / "workflow.json").write_text(
        json.dumps(
            {
                "version": "workflow-run/v1",
                "workflow_id": workflow_id,
                "run_id": workflow_id,
                "work_id": work_id,
                "lane": "thesis",
                "action": "verify",
                "status": status,
                "execution_status": execution_status,
                "readiness_status": "submission-ready"
                if execution_status == "succeeded"
                else "strong-draft-with-blockers",
                "started_at": "2026-06-23T10:00:00+00:00",
                "finished_at": "2026-06-23T10:01:00+00:00",
                "workflow_dir": str(workflow_dir),
                "sandbox_dir": str(workflow_dir / "sandbox"),
                "role_runs": [],
                "gates": [],
                "gate_summary": {},
                "blockers": [],
                "promotion": {"status": "promoted"},
                "promotion_status": "promoted",
                "evaluator_verdict": None,
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )


def _write_workflow_payload(root: Path, workflow_id: str, payload: object) -> None:
    workflow_dir = root / "output" / "runs" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")


class JobQueueDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self.fake = FakeOrchestrator(self.root)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def queue(self) -> JobQueue:
        return JobQueue(self.root, orchestrator_factory=lambda root: self.fake)

    def test_dispatch_starts_oldest_queued_job_and_links_workflow(self) -> None:
        queue = self.queue()
        first = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-1"))
        queue.submit_workflow(WorkflowJobSpec("other-work", "article", "review", "draft.md"))

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual([item["job_id"] for item in result["dispatched"]], [first["job_id"]])
        job = queue.get_job(first["job_id"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["workflow_id"], "wf-1")
        self.assertEqual(job["active_run_id"], "run-1")
        self.assertEqual(job["attempt"], 1)
        self.assertEqual(self.fake.start_calls[0]["work_id"], "demo-work")

    def test_dispatch_respects_global_and_per_work_limits(self) -> None:
        queue = self.queue()
        running = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "running"))
        running_payload = queue.get_job(running["job_id"])
        running_payload["workflow_id"] = "wf-running"
        running_payload["active_run_id"] = "run-running"
        queue._transition(running_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-running", execution_status="running")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-running",
                "run_id": "run-running",
                "work_id": "demo-work",
            }
        ]
        first = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "queued-same-work"))
        second = queue.submit_workflow(WorkflowJobSpec("other-work", "article", "review", "queued-other-work"))

        result = queue.dispatch_jobs(limit=5)

        self.assertEqual([item["job_id"] for item in result["dispatched"]], [second["job_id"]])
        self.assertEqual(queue.get_job(first["job_id"])["status"], "queued")

    def test_reconcile_completed_and_failed_workflows(self) -> None:
        queue = self.queue()
        done = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "done"))
        failed = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "failed"))
        done_payload = queue.get_job(done["job_id"])
        done_payload["workflow_id"] = "wf-done"
        queue._transition(done_payload, status="running", event="job-dispatched")
        failed_payload = queue.get_job(failed["job_id"])
        failed_payload["workflow_id"] = "wf-failed"
        queue._transition(failed_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-done", execution_status="succeeded")
        _write_workflow(self.root, "wf-failed", execution_status="failed")

        result = queue.reconcile_jobs()

        self.assertEqual(
            {item["job_id"]: item["status"] for item in result["reconciled"]},
            {
                done["job_id"]: "completed",
                failed["job_id"]: "failed",
            },
        )

    def test_reconcile_missing_runtime_blocks_running_job(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        job["workflow_id"] = "wf-missing"
        queue._transition(job, status="running", event="job-dispatched")

        queue.reconcile_jobs()

        blocked = queue.get_job(job["job_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failure"]["code"], "missing-runtime-result")

    def test_reconcile_keeps_missing_runtime_running_when_matching_active_run_exists(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        payload = queue.get_job(job["job_id"])
        payload["workflow_id"] = "wf-live"
        payload["active_run_id"] = "run-live"
        queue._transition(payload, status="running", event="job-dispatched")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-live",
                "run_id": "run-live",
                "work_id": "demo-work",
            }
        ]

        dispatch_result = queue.dispatch_jobs(limit=0)

        self.assertEqual(queue.get_job(job["job_id"])["status"], "running")
        self.assertEqual(dispatch_result["blocked"], [])

        direct_result = queue.reconcile_jobs()

        self.assertEqual(queue.get_job(job["job_id"])["status"], "running")
        self.assertEqual(direct_result["blocked"], [])

    def test_dispatch_preserves_retry_attempt_count(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "retry-me"))
        failed = queue._transition_for_test(job["job_id"], status="failed", failure={"code": "boom"})
        self.assertEqual(failed["attempt"], 0)
        retried = queue.retry_job(job["job_id"])
        self.assertEqual(retried["attempt"], 1)

        queue.dispatch_jobs(limit=1)

        dispatched = queue.get_job(job["job_id"])
        self.assertEqual(dispatched["status"], "running")
        self.assertEqual(dispatched["attempt"], 1)

    def test_dispatch_blocks_config_errors(self) -> None:
        queue = self.queue()
        self.fake.raise_on_start = ValueError("bad target")
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "bad-target"))

        result = queue.dispatch_jobs(limit=1)

        blocked = queue.get_job(job["job_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failure"]["category"], "config")
        self.assertEqual([item["job_id"] for item in result["blocked"]], [job["job_id"]])

    def test_dispatch_run_busy_leaves_job_queued(self) -> None:
        queue = self.queue()
        self.fake.raise_busy = True
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "busy-target"))

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual(queue.get_job(job["job_id"])["status"], "queued")
        self.assertEqual([item["job_id"] for item in result["skipped"]], [job["job_id"]])

    def test_dispatch_reconciles_terminal_running_jobs_first(self) -> None:
        queue = self.queue()
        done = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "done"))
        failed = queue.submit_workflow(WorkflowJobSpec("other-work", "article", "review", "failed"))
        done_payload = queue.get_job(done["job_id"])
        done_payload["workflow_id"] = "wf-done"
        queue._transition(done_payload, status="running", event="job-dispatched")
        failed_payload = queue.get_job(failed["job_id"])
        failed_payload["workflow_id"] = "wf-failed"
        queue._transition(failed_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-done", execution_status="succeeded")
        _write_workflow(self.root, "wf-failed", execution_status="failed", work_id="other-work")

        result = queue.dispatch_jobs(limit=0)

        self.assertEqual(
            {item["job_id"]: item["status"] for item in result["reconciled"]},
            {
                done["job_id"]: "completed",
                failed["job_id"]: "failed",
            },
        )

    def test_dispatch_reconcile_frees_capacity_before_starting_next_job(self) -> None:
        queue = self.queue()
        done = queue.submit_workflow(
            WorkflowJobSpec("demo-work", "thesis", "verify", "done", global_concurrency=1)
        )
        done_payload = queue.get_job(done["job_id"])
        done_payload["workflow_id"] = "wf-done"
        queue._transition(done_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-done", execution_status="succeeded")
        queued = queue.submit_workflow(
            WorkflowJobSpec("other-work", "article", "review", "next", global_concurrency=1)
        )

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual([item["job_id"] for item in result["reconciled"]], [done["job_id"]])
        self.assertEqual([item["job_id"] for item in result["dispatched"]], [queued["job_id"]])
        self.assertEqual(queue.get_job(queued["job_id"])["status"], "running")

    def test_dispatch_deduplicates_queue_managed_active_runs_for_capacity(self) -> None:
        queue = self.queue()
        running = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "running"))
        running_payload = queue.get_job(running["job_id"])
        running_payload["workflow_id"] = "wf-active"
        running_payload["active_run_id"] = "run-active"
        queue._transition(running_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-active", execution_status="running")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-active",
                "run_id": "run-active",
                "work_id": "demo-work",
            }
        ]
        queued = queue.submit_workflow(
            WorkflowJobSpec("other-work", "article", "review", "next", global_concurrency=2)
        )

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual([item["job_id"] for item in result["dispatched"]], [queued["job_id"]])

    def test_dispatch_does_not_dedupe_active_run_with_wrong_work_id(self) -> None:
        queue = self.queue()
        running = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "running"))
        running_payload = queue.get_job(running["job_id"])
        running_payload["workflow_id"] = "wf-active"
        running_payload["active_run_id"] = "run-active"
        queue._transition(running_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-active", execution_status="running")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-active",
                "run_id": "run-active",
                "work_id": "demo-work",
            },
            {
                "workflow_id": "wf-active",
                "run_id": "run-active",
                "work_id": "other-work",
            }
        ]
        queued = queue.submit_workflow(
            WorkflowJobSpec("third-work", "article", "review", "next", global_concurrency=2)
        )

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual(queue.get_job(queued["job_id"])["status"], "queued")
        self.assertEqual([item["job_id"] for item in result["skipped"]], [queued["job_id"]])
        self.assertEqual(result["skipped"][0]["reason"], "global-concurrency-limit")

    def test_reconcile_nonterminal_workflow_with_active_run_remains_running(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "running"))
        payload = queue.get_job(job["job_id"])
        payload["workflow_id"] = "wf-active"
        payload["active_run_id"] = "run-active"
        queue._transition(payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-active", execution_status="running")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-active",
                "run_id": "run-active",
                "work_id": "demo-work",
            }
        ]

        result = queue.reconcile_jobs()

        self.assertEqual(queue.get_job(job["job_id"])["status"], "running")
        self.assertEqual(result["blocked"], [])

    def test_reconcile_stale_nonterminal_workflow_without_active_run_blocks(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "stale"))
        payload = queue.get_job(job["job_id"])
        payload["workflow_id"] = "wf-stale"
        payload["active_run_id"] = "run-stale"
        queue._transition(payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-stale", execution_status="running")

        result = queue.reconcile_jobs()

        blocked = queue.get_job(job["job_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failure"]["code"], "missing-runtime-result")
        self.assertEqual([item["job_id"] for item in result["blocked"]], [job["job_id"]])

    def test_reconcile_nonterminal_workflow_with_wrong_work_active_run_blocks(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "wrong-active"))
        payload = queue.get_job(job["job_id"])
        payload["workflow_id"] = "wf-active"
        payload["active_run_id"] = "run-active"
        queue._transition(payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-active", execution_status="running")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-active",
                "run_id": "run-active",
                "work_id": "other-work",
            }
        ]

        result = queue.reconcile_jobs()

        blocked = queue.get_job(job["job_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertIn(blocked["failure"]["code"], {"missing-runtime-result", "runtime-link-mismatch"})
        self.assertEqual([item["job_id"] for item in result["blocked"]], [job["job_id"]])

    def test_reconcile_malformed_workflow_json_blocks_running_job(self) -> None:
        queue = self.queue()
        job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", "draft.md"))
        payload = queue.get_job(job["job_id"])
        payload["workflow_id"] = "wf-malformed"
        queue._transition(payload, status="running", event="job-dispatched")
        workflow_dir = self.root / "output" / "runs" / "wf-malformed"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        (workflow_dir / "workflow.json").write_text("{not json", encoding="utf-8")

        result = queue.reconcile_jobs()

        blocked = queue.get_job(job["job_id"])
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["failure"]["code"], "ambiguous-runtime-result")
        self.assertEqual([item["job_id"] for item in result["blocked"]], [job["job_id"]])

    def test_reconcile_missing_or_unknown_execution_status_blocks_running_job(self) -> None:
        cases = [
            (
                "wf-missing-status",
                {"version": "workflow-run/v1", "workflow_id": "wf-missing-status", "work_id": "demo-work"},
            ),
            (
                "wf-unknown-status",
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-unknown-status",
                    "work_id": "demo-work",
                    "execution_status": "mystery",
                },
            ),
        ]

        for workflow_id, workflow_payload in cases:
            with self.subTest(workflow_id=workflow_id):
                queue = self.queue()
                job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", workflow_id))
                payload = queue.get_job(job["job_id"])
                payload["workflow_id"] = workflow_id
                queue._transition(payload, status="running", event="job-dispatched")
                _write_workflow_payload(self.root, workflow_id, workflow_payload)

                queue.reconcile_jobs()

                blocked = queue.get_job(job["job_id"])
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(blocked["failure"]["code"], "ambiguous-runtime-result")

    def test_reconcile_runtime_identity_mismatch_blocks_terminal_payload(self) -> None:
        cases = [
            (
                "wrong-workflow-id",
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-other",
                    "work_id": "demo-work",
                    "execution_status": "succeeded",
                },
            ),
            (
                "missing-workflow-id",
                {
                    "version": "workflow-run/v1",
                    "work_id": "demo-work",
                    "execution_status": "succeeded",
                },
            ),
            (
                "wrong-work-id",
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-job",
                    "work_id": "other-work",
                    "execution_status": "succeeded",
                },
            ),
        ]

        for target, workflow_payload in cases:
            with self.subTest(target=target):
                queue = self.queue()
                job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", target))
                payload = queue.get_job(job["job_id"])
                payload["workflow_id"] = "wf-job"
                payload["active_run_id"] = "run-job"
                queue._transition(payload, status="running", event="job-dispatched")
                _write_workflow_payload(self.root, "wf-job", workflow_payload)

                queue.reconcile_jobs()

                blocked = queue.get_job(job["job_id"])
                self.assertEqual(blocked["status"], "blocked")
                self.assertIn(blocked["failure"]["code"], {"ambiguous-runtime-result", "runtime-link-mismatch"})

    def test_reconcile_terminal_payload_requires_string_work_id(self) -> None:
        cases = [
            (
                "missing-work-id",
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-job",
                    "execution_status": "succeeded",
                },
            ),
            (
                "non-string-work-id",
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-job",
                    "work_id": 123,
                    "execution_status": "succeeded",
                },
            ),
        ]

        for target, workflow_payload in cases:
            with self.subTest(target=target):
                queue = self.queue()
                job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", target))
                payload = queue.get_job(job["job_id"])
                payload["workflow_id"] = "wf-job"
                queue._transition(payload, status="running", event="job-dispatched")
                _write_workflow_payload(self.root, "wf-job", workflow_payload)

                queue.reconcile_jobs()

                blocked = queue.get_job(job["job_id"])
                self.assertEqual(blocked["status"], "blocked")
                self.assertIn(blocked["failure"]["code"], {"ambiguous-runtime-result", "runtime-link-mismatch"})

    def test_reconcile_terminal_payload_requires_workflow_run_version(self) -> None:
        cases = [
            (
                "missing-version",
                {
                    "workflow_id": "wf-job",
                    "work_id": "demo-work",
                    "execution_status": "succeeded",
                },
            ),
            (
                "wrong-version",
                {
                    "version": "workflow-run/v0",
                    "workflow_id": "wf-job",
                    "work_id": "demo-work",
                    "execution_status": "succeeded",
                },
            ),
        ]

        for target, workflow_payload in cases:
            with self.subTest(target=target):
                queue = self.queue()
                job = queue.submit_workflow(WorkflowJobSpec("demo-work", "article", "review", target))
                payload = queue.get_job(job["job_id"])
                payload["workflow_id"] = "wf-job"
                queue._transition(payload, status="running", event="job-dispatched")
                _write_workflow_payload(self.root, "wf-job", workflow_payload)

                queue.reconcile_jobs()

                blocked = queue.get_job(job["job_id"])
                self.assertEqual(blocked["status"], "blocked")
                self.assertEqual(blocked["failure"]["code"], "ambiguous-runtime-result")

    def test_dispatch_respects_true_global_limit_across_works(self) -> None:
        queue = self.queue()
        first = queue.submit_workflow(WorkflowJobSpec("first-work", "thesis", "verify", "running-1"))
        first_payload = queue.get_job(first["job_id"])
        first_payload["workflow_id"] = "wf-running-1"
        first_payload["active_run_id"] = "run-running-1"
        queue._transition(first_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-running-1", execution_status="running", work_id="first-work")
        second = queue.submit_workflow(WorkflowJobSpec("second-work", "article", "review", "running-2"))
        second_payload = queue.get_job(second["job_id"])
        second_payload["workflow_id"] = "wf-running-2"
        second_payload["active_run_id"] = "run-running-2"
        queue._transition(second_payload, status="running", event="job-dispatched")
        _write_workflow(self.root, "wf-running-2", execution_status="running", work_id="second-work")
        self.fake.store.active_runs = [
            {
                "workflow_id": "wf-running-1",
                "run_id": "run-running-1",
                "work_id": "first-work",
            },
            {
                "workflow_id": "wf-running-2",
                "run_id": "run-running-2",
                "work_id": "second-work",
            },
        ]
        queued = queue.submit_workflow(WorkflowJobSpec("third-work", "article", "review", "queued"))

        result = queue.dispatch_jobs(limit=1)

        self.assertEqual(queue.get_job(queued["job_id"])["status"], "queued")
        self.assertEqual([item["job_id"] for item in result["skipped"]], [queued["job_id"]])
        self.assertEqual(result["skipped"][0]["reason"], "global-concurrency-limit")
