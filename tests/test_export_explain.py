from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from academic_engine.export_explain import explain_export
from academic_engine.one_shot import ONE_SHOT_REPORT_VERSION
from tests.test_academic_engine import TEST_ARTICLE_FINAL, TEST_WORK_ID, build_fake_repo


class ExportExplainTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        build_fake_repo(self.root)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_no_successful_workflow_blocks_export(self) -> None:
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["kind"], "export-explanation")
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "no-successful-workflow")

    def test_non_submission_ready_workflow_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-blocked",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="strong-draft-with-blockers",
        )
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "latest-workflow-not-submission-ready")

    def test_failed_mandatory_gate_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-gate",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
            gates=[{"gate_id": "required-output", "status": "block", "blocking": True, "reason": "missing"}],
        )
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "mandatory-gates-failed")
        self.assertEqual(payload["reasons"][0]["details"]["gate_ids"], ["required-output"])

    def test_promotion_conflict_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-promotion",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
            promotion={"status": "conflict"},
        )
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "promotion-not-safe")
        self.assertEqual(payload["reasons"][0]["details"]["promotion_status"], "conflict")

    def test_missing_thesis_machine_gates_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-ready",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")
        self.assertEqual(payload["reasons"][0]["details"]["workflow_id"], "wf-ready")
        self.assertEqual(payload["reasons"][0]["details"]["readiness_status"], "submission-ready")
        self.assertEqual(payload["reasons"][0]["details"]["required_status"], "machine-gates-passed")

    def test_missing_article_final_markdown_blocks_export(self) -> None:
        _write_workflow(
            self.root,
            "wf-article",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        (self.root / TEST_ARTICLE_FINAL).unlink()
        payload = explain_export(self.root, "article:demo", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "article-final-markdown-missing")
        self.assertEqual(payload["reasons"][0]["details"]["workflow_id"], "wf-article")
        self.assertEqual(payload["reasons"][0]["details"]["readiness_status"], "submission-ready")
        self.assertEqual(
            Path(payload["reasons"][0]["details"]["expected_path"]).resolve(),
            (self.root / TEST_ARTICLE_FINAL).resolve(),
        )

    def test_ready_when_article_workflow_and_final_markdown_exist(self) -> None:
        _write_workflow(
            self.root,
            "wf-article-ready",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "article:demo", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["reasons"], [])

    def test_empty_article_slug_blocks_export_without_raising(self) -> None:
        _write_workflow(
            self.root,
            "wf-article-ready",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "article:", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "unsupported-export-subject")

    def test_whitespace_article_slug_blocks_export_without_raising(self) -> None:
        _write_workflow(
            self.root,
            "wf-article-ready",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "article:   ", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "unsupported-export-subject")

    def test_path_like_article_slug_blocks_export_without_escaping_bundle(self) -> None:
        _write_workflow(
            self.root,
            "wf-article-ready",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "article:../briefs/demo", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "unsupported-export-subject")

    def test_disabled_work_lane_blocks_export_even_with_stale_ready_artifacts(self) -> None:
        work_file = self.root / "works" / TEST_WORK_ID / "work.toml"
        work_file.write_text(
            work_file.read_text(encoding="utf-8").replace(
                'active_lanes = ["thesis", "article"]',
                'active_lanes = ["thesis"]',
            ),
            encoding="utf-8",
        )
        _write_workflow(
            self.root,
            "wf-article-ready",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "article:demo", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "work-lane-disabled")
        self.assertEqual(payload["reasons"][0]["details"]["lane"], "article")

    def test_missing_enabled_lane_config_blocks_export_without_raising(self) -> None:
        work_file = self.root / "works" / TEST_WORK_ID / "work.toml"
        content = work_file.read_text(encoding="utf-8")
        work_file.write_text(content.split("\n[article]\n", 1)[0], encoding="utf-8")
        _write_workflow(
            self.root,
            "wf-article-ready",
            lane="article",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        payload = explain_export(self.root, "article:demo", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "work-lane-not-configured")
        self.assertEqual(payload["reasons"][0]["details"]["lane"], "article")

    def test_unsupported_subject_blocks_export(self) -> None:
        payload = explain_export(self.root, "slides", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "unsupported-export-subject")

    def test_ready_when_thesis_workflow_and_machine_gates_exist(self) -> None:
        _write_workflow(
            self.root,
            "wf-thesis-ready",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        _write_one_shot_report(self.root, status="machine-gates-passed")
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "ready")
        self.assertEqual(payload["reasons"], [])

    def test_latest_malformed_one_shot_report_blocks_even_when_older_report_passed(self) -> None:
        _write_workflow(
            self.root,
            "wf-thesis-ready",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        _write_one_shot_report(
            self.root,
            status="machine-gates-passed",
            finished_at="2026-04-18T10:59:00+00:00",
        )
        _write_raw_one_shot_report(
            self.root,
            "2026-04-19-one-shot-report.json",
            "{not json",
            mtime="2026-04-19T10:59:00+00:00",
        )
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")

    def test_latest_non_dict_one_shot_report_blocks_without_raising(self) -> None:
        for raw_payload in (["not", "a", "dict"], "not a dict"):
            with self.subTest(raw_payload=raw_payload):
                _write_workflow(
                    self.root,
                    "wf-thesis-ready",
                    lane="thesis",
                    execution_status="succeeded",
                    readiness_status="submission-ready",
                )
                _write_one_shot_report(
                    self.root,
                    status="machine-gates-passed",
                    finished_at="2026-04-18T10:59:00+00:00",
                )
                _write_raw_one_shot_report(
                    self.root,
                    "2026-04-19-one-shot-report.json",
                    json.dumps(raw_payload),
                    mtime="2026-04-19T10:59:00+00:00",
                )
                payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
                self.assertEqual(payload["status"], "blocked")
                self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")

    def test_latest_blocked_one_shot_report_blocks_even_when_older_report_passed(self) -> None:
        _write_workflow(
            self.root,
            "wf-thesis-ready",
            lane="thesis",
            execution_status="succeeded",
            readiness_status="submission-ready",
        )
        _write_one_shot_report(
            self.root,
            status="machine-gates-passed",
            finished_at="2026-04-18T10:59:00+00:00",
            name="2026-04-18-one-shot-report.json",
        )
        _write_one_shot_report(
            self.root,
            status="blocked",
            finished_at="2026-04-19T10:59:00+00:00",
            name="2026-04-19-one-shot-report.json",
        )
        payload = explain_export(self.root, "thesis", work_id=TEST_WORK_ID)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")


def _write_workflow(
    root: Path,
    workflow_id: str,
    *,
    lane: str,
    execution_status: str,
    readiness_status: str,
    work_id: str = TEST_WORK_ID,
    gates: list[dict[str, object]] | None = None,
    promotion: dict[str, object] | None = None,
    finished_at: str = "2026-04-18T11:00:00+00:00",
) -> None:
    workflow_dir = root / "output" / "runs" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "workflow-run/v1",
        "workflow_id": workflow_id,
        "run_id": workflow_id,
        "work_id": work_id,
        "lane": lane,
        "action": "finalize",
        "execution_status": execution_status,
        "readiness_status": readiness_status,
        "started_at": "2026-04-18T10:00:00+00:00",
        "finished_at": finished_at,
        "gates": gates or [],
        "promotion": promotion or {"status": "promoted"},
    }
    (workflow_dir / "workflow.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_one_shot_report(
    root: Path,
    *,
    status: str,
    finished_at: str = "2026-04-18T10:59:00+00:00",
    name: str = "2026-04-18-one-shot-report.json",
) -> None:
    report_path = root / "works" / TEST_WORK_ID / "thesis" / "reviews" / name
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ONE_SHOT_REPORT_VERSION,
        "status": status,
        "finished_at": finished_at,
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_raw_one_shot_report(root: Path, name: str, content: str, *, mtime: str) -> None:
    report_path = root / "works" / TEST_WORK_ID / "thesis" / "reviews" / name
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(content, encoding="utf-8")
    timestamp = datetime.fromisoformat(mtime).timestamp()
    os.utime(report_path, (timestamp, timestamp))
