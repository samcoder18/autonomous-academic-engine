from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from academic_engine.job_inspector import inspect_job
from academic_engine.job_queue import JobQueue, WorkflowJobSpec


class JobInspectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self.queue = JobQueue(
            self.root,
            now=lambda: "2026-06-23T10:00:00+00:00",
            id_factory=lambda: "job-demo",
        )
        self.job = self.queue.submit_workflow(WorkflowJobSpec("demo-work", "thesis", "verify", "section-1"))
        self.job["workflow_id"] = "wf-demo"
        self.queue._transition(
            self.job,
            status="running",
            event="job-dispatched",
            details={"workflow_id": "wf-demo"},
        )
        self.workflow_dir = self.root / "output" / "runs" / "wf-demo"
        self.workflow_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def _write_workflow(self, *, include_role_runs: bool = True, **overrides: object) -> None:
        payload: dict[str, object] = {
            "version": "workflow-run/v1",
            "workflow_id": "wf-demo",
            "run_id": "wf-demo",
            "work_id": "demo-work",
            "lane": "thesis",
            "action": "verify",
            "status": "succeeded",
            "execution_status": "succeeded",
            "started_at": "2026-06-23T10:00:00+00:00",
            "finished_at": "2026-06-23T10:01:00+00:00",
            "blockers": [],
        }
        if include_role_runs:
            payload["role_runs"] = []
        payload.update(overrides)
        (self.workflow_dir / "workflow.json").write_text(json.dumps(payload), encoding="utf-8")

    def test_inspection_merges_timeline_durations_failure_and_changed_files(self) -> None:
        (self.workflow_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"timestamp": "2026-06-23T10:00:01+00:00", "event": "workflow-queued"}),
                    json.dumps(
                        {
                            "timestamp": "2026-06-23T10:00:02+00:00",
                            "event": "role-started",
                            "role_run_id": "01-role",
                        }
                    ),
                    json.dumps(
                        {
                            "timestamp": "2026-06-23T10:00:12+00:00",
                            "event": "role-finished",
                            "role_run_id": "01-role",
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (self.workflow_dir / "workflow.json").write_text(
            json.dumps(
                {
                    "version": "workflow-run/v1",
                    "workflow_id": "wf-demo",
                    "run_id": "wf-demo",
                    "work_id": "demo-work",
                    "lane": "thesis",
                    "action": "verify",
                    "status": "failed",
                    "execution_status": "failed",
                    "readiness_status": "strong-draft-with-blockers",
                    "started_at": "2026-06-23T10:00:00+00:00",
                    "finished_at": "2026-06-23T10:02:00+00:00",
                    "workflow_dir": str(self.workflow_dir),
                    "sandbox_dir": str(self.workflow_dir / "sandbox"),
                    "role_runs": [
                        {
                            "role_run_id": "01-role",
                            "role_id": "thesis-source-verifier",
                            "status": "failed",
                            "started_at": "2026-06-23T10:00:02+00:00",
                            "finished_at": "2026-06-23T10:00:12+00:00",
                            "reported_status": "failed",
                            "error": "source missing",
                            "blockers": [
                                {
                                    "category": "primary-support",
                                    "code": "missing-source",
                                    "message": "Add source.",
                                }
                            ],
                            "changed_paths": ["works/demo-work/thesis/sources/source-pack.md"],
                            "output_file": str(self.workflow_dir / "roles" / "01-role" / "output.md"),
                        }
                    ],
                    "gates": [
                        {
                            "gate_id": "required-output",
                            "status": "block",
                            "blocking": True,
                            "reason": "missing",
                        }
                    ],
                    "gate_summary": {"block": 1},
                    "blockers": [
                        {
                            "category": "runtime",
                            "code": "workflow-failed",
                            "message": "Workflow failed.",
                        }
                    ],
                    "promotion": {"status": "blocked", "reason": "Workflow did not promote."},
                    "promotion_status": "blocked",
                    "evaluator_verdict": None,
                    "metadata": {},
                }
            ),
            encoding="utf-8",
        )

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertEqual(payload["job"]["job_id"], "job-demo")
        self.assertEqual(payload["durations"]["total_seconds"], 120.0)
        self.assertEqual(payload["durations"]["roles"][0]["duration_seconds"], 10.0)
        self.assertEqual(payload["failure"]["role_id"], "thesis-source-verifier")
        self.assertEqual(payload["failure"]["error"], "source missing")
        self.assertEqual(payload["blockers"][0]["code"], "workflow-failed")
        self.assertEqual(payload["blockers"][1]["code"], "missing-source")
        self.assertIn("works/demo-work/thesis/sources/source-pack.md", payload["changed_files"])
        self.assertTrue(payload["attachments"]["workflow"]["exists"])
        self.assertTrue(any(item["event"] == "workflow-queued" for item in payload["timeline"]))

    def test_missing_or_malformed_files_are_observability_warnings(self) -> None:
        (self.workflow_dir / "workflow.json").write_text("{bad json", encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertTrue(any("workflow.json" in item["path"] for item in payload["observability_warnings"]))

    def test_job_queue_wrapper_inspects_stored_job(self) -> None:
        payload = self.queue.inspect_job("job-demo")

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertEqual(payload["job"]["job_id"], "job-demo")

    def test_export_blockers_argument_is_passed_through(self) -> None:
        export_blockers = [{"category": "export", "code": "docx-blocked", "message": "DOCX unavailable."}]

        payload = inspect_job(self.root, self.queue.get_job("job-demo"), export_blockers=export_blockers)

        self.assertEqual(payload["export_blockers"], export_blockers)

    def test_malformed_events_line_warns_and_keeps_valid_events(self) -> None:
        (self.workflow_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"timestamp": "2026-06-23T10:00:01+00:00", "event": "workflow-queued"}),
                    "{bad json",
                    json.dumps({"timestamp": "2026-06-23T10:00:03+00:00", "event": "workflow-finished"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertTrue(any("events.jsonl" in item["path"] for item in payload["observability_warnings"]))
        self.assertTrue(any(item["event"] == "workflow-queued" for item in payload["timeline"]))
        self.assertTrue(any(item["event"] == "workflow-finished" for item in payload["timeline"]))

    def test_missing_gates_and_promotion_files_are_observability_warnings(self) -> None:
        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        warning_paths = [item["path"] for item in payload["observability_warnings"]]
        self.assertTrue(any("gates.json" in path for path in warning_paths))
        self.assertTrue(any("promotion.json" in path for path in warning_paths))

    def test_malformed_gates_and_promotion_files_are_observability_warnings(self) -> None:
        (self.workflow_dir / "gates.json").write_text("{bad gates", encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text("{bad promotion", encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        warning_paths = [item["path"] for item in payload["observability_warnings"]]
        self.assertTrue(any("gates.json" in path for path in warning_paths))
        self.assertTrue(any("promotion.json" in path for path in warning_paths))

    def test_schema_invalid_gates_and_promotion_files_are_observability_warnings(self) -> None:
        (self.workflow_dir / "gates.json").write_text("[]", encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text(json.dumps("ok"), encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        warning_paths = [item["path"] for item in payload["observability_warnings"]]
        self.assertTrue(any("gates.json" in path for path in warning_paths))
        self.assertTrue(any("promotion.json" in path for path in warning_paths))

    def test_gates_with_non_object_entries_are_observability_warnings(self) -> None:
        (self.workflow_dir / "gates.json").write_text(json.dumps({"gates": ["bad"]}), encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertTrue(any("gates.json" in item["path"] for item in payload["observability_warnings"]))

    def test_promotion_without_string_status_is_observability_warning(self) -> None:
        cases = [{}, {"status": 123}]
        for promotion_payload in cases:
            with self.subTest(promotion_payload=promotion_payload):
                (self.workflow_dir / "gates.json").write_text(json.dumps({"gates": []}), encoding="utf-8")
                (self.workflow_dir / "promotion.json").write_text(json.dumps(promotion_payload), encoding="utf-8")

                payload = inspect_job(self.root, self.queue.get_job("job-demo"))

                self.assertTrue(any("promotion.json" in item["path"] for item in payload["observability_warnings"]))

    def test_workflow_missing_role_runs_warns_without_raising(self) -> None:
        self._write_workflow(include_role_runs=False)
        (self.workflow_dir / "gates.json").write_text(json.dumps({"gates": []}), encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertTrue(
            any(
                "workflow.json" in item["path"] and "role_runs" in item["message"]
                for item in payload["observability_warnings"]
            )
        )

    def test_workflow_null_role_runs_warns_without_raising(self) -> None:
        self._write_workflow(role_runs=None)
        (self.workflow_dir / "gates.json").write_text(json.dumps({"gates": []}), encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertTrue(
            any(
                "workflow.json" in item["path"] and "role_runs" in item["message"]
                for item in payload["observability_warnings"]
            )
        )

    def test_workflow_role_runs_string_warns_without_raising(self) -> None:
        self._write_workflow(role_runs="not-a-list")
        (self.workflow_dir / "gates.json").write_text(json.dumps({"gates": []}), encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertEqual(payload["kind"], "job-inspection")
        self.assertTrue(
            any(
                "workflow.json" in item["path"] and "role_runs" in item["message"]
                for item in payload["observability_warnings"]
            )
        )

    def test_workflow_role_runs_skips_non_object_entries_with_warning(self) -> None:
        self._write_workflow(
            role_runs=[
                "not-an-object",
                {
                    "role_run_id": "valid-role",
                    "role_id": "thesis-style-editor",
                    "status": "succeeded",
                    "started_at": "2026-06-23T10:00:10+00:00",
                    "finished_at": "2026-06-23T10:00:20+00:00",
                    "changed_paths": ["works/demo-work/b.md", "works/demo-work/a.md", "works/demo-work/a.md"],
                },
            ]
        )
        (self.workflow_dir / "gates.json").write_text(json.dumps({"gates": []}), encoding="utf-8")
        (self.workflow_dir / "promotion.json").write_text(json.dumps({"status": "succeeded"}), encoding="utf-8")

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        self.assertTrue(
            any(
                "workflow.json" in item["path"] and "role_runs[0]" in item["message"]
                for item in payload["observability_warnings"]
            )
        )
        self.assertEqual(payload["durations"]["roles"][0]["role_id"], "thesis-style-editor")
        self.assertEqual(payload["changed_files"], ["works/demo-work/a.md", "works/demo-work/b.md"])

    def test_schema_invalid_event_objects_warn_and_do_not_enter_timeline(self) -> None:
        (self.workflow_dir / "events.jsonl").write_text(
            "\n".join(
                [
                    json.dumps({"timestamp": "2026-06-23T10:00:01+00:00", "event": "workflow-queued"}),
                    json.dumps({"timestamp": "2026-06-23T10:00:02+00:00", "details": "missing event"}),
                    json.dumps({"event": "missing-timestamp"}),
                    json.dumps({"timestamp": 123, "event": "bad-timestamp"}),
                    json.dumps({"timestamp": "2026-06-23T10:00:05+00:00", "event": "workflow-finished"}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        payload = inspect_job(self.root, self.queue.get_job("job-demo"))

        event_warnings = [
            item
            for item in payload["observability_warnings"]
            if "events.jsonl" in item["path"] and item["code"] == "malformed-event"
        ]
        self.assertEqual([item["line"] for item in event_warnings], [2, 3, 4])
        timeline_events = [item["event"] for item in payload["timeline"]]
        self.assertIn("workflow-queued", timeline_events)
        self.assertIn("workflow-finished", timeline_events)
        self.assertNotIn("missing-timestamp", timeline_events)
        self.assertNotIn("bad-timestamp", timeline_events)
