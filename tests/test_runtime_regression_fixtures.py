from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from academic_engine.article_bundle_state import article_bundle_manifest_path
from academic_engine.export_explain import explain_export
from academic_engine.one_shot import OneShotConfig, run_one_shot
from academic_engine.orchestrator import WorkflowOrchestrator
from academic_engine.orchestrator_exports import ONE_SHOT_REPORT_VERSION
from academic_engine.workspace import (
    article_bundle_paths,
    load_work_config,
    load_workspace_config,
    relative_to_workspace,
)
from tests.test_academic_engine import TEST_WORK_ID, build_fake_repo, write_raw_manifest


class RuntimeRegressionFixtureTests(unittest.TestCase):
    def test_article_without_evidence_is_not_submission_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            orchestrator = WorkflowOrchestrator(root)

            run_dir = _write_article_finalize_runtime(
                root,
                orchestrator,
                article_slug="demo",
                claimed_status="submission-ready",
            )

            status = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
            bundle_state = json.loads(
                article_bundle_manifest_path(orchestrator._work(TEST_WORK_ID), "demo").read_text(encoding="utf-8")
            )
            blocker_codes = {item["code"] for item in status["blockers"]}

        self.assertEqual(bundle_state["current_status"], "strong-draft-with-blockers")
        self.assertEqual(
            status["finalization_check"]["effective_readiness_status"],
            "strong-draft-with-blockers",
        )
        self.assertIn("submission-missing-evidence", blocker_codes)

    def test_vkr_without_originality_corpus_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            manuscript = root / "vkr.md"
            manuscript.write_text(
                dedent(
                    """\
                    # Введение

                    Выпускная квалификационная работа анализирует правовой режим
                    биометрических персональных данных и требования к их обработке.

                    ## Список использованных источников

                    1. О персональных данных: Федеральный закон от 27.07.2006 N 152-ФЗ.
                    2. О единой биометрической системе: Федеральный закон от 29.12.2022 N 572-ФЗ.
                    """
                ),
                encoding="utf-8",
            )

            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )

        originality = next(gate for gate in report.gates if gate.name == "originality")
        self.assertEqual(report.status, "blocked")
        self.assertFalse(originality.passed)
        self.assertEqual(originality.blockers[0].code, "originality-corpus-required")

    def test_submission_ready_evaluator_cannot_override_failed_machine_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            _write_workflow(root, "thesis-ready-but-gates-blocked", lane="thesis")
            _write_one_shot_report(root, status="blocked")

            payload = explain_export(root, "thesis", work_id=TEST_WORK_ID)

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")

    def test_promotion_conflict_does_not_mutate_canon(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            canon_path = root / "works" / TEST_WORK_ID / "work-canon.md"
            original_canon = canon_path.read_text(encoding="utf-8")
            _write_workflow(
                root,
                "article-ready-but-promotion-conflict",
                lane="article",
                promotion={"status": "conflict"},
            )

            payload = explain_export(root, "article:demo", work_id=TEST_WORK_ID)
            current_canon = canon_path.read_text(encoding="utf-8")

        self.assertEqual(current_canon, original_canon)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["reasons"][0]["code"], "promotion-not-safe")


def _write_article_finalize_runtime(
    root: Path,
    orchestrator: WorkflowOrchestrator,
    *,
    article_slug: str,
    claimed_status: str,
) -> Path:
    workspace = load_workspace_config(root)
    work = load_work_config(workspace, TEST_WORK_ID)
    bundle = article_bundle_paths(work, article_slug)
    target_rel = relative_to_workspace(workspace, bundle["final_markdown"])
    timestamp = "20260418-103000"
    output_file = work.article.paths.output_runs_dir / f"{timestamp}-finalize-{article_slug}.md"
    manifest_file = work.article.paths.output_runs_dir / f"{timestamp}-finalize-{article_slug}.meta.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(f"Final verdict: `{claimed_status}`\n", encoding="utf-8")
    manifest_file.write_text(
        json.dumps(
            {
                "timestamp": timestamp,
                "command": "finalize",
                "work_id": work.slug,
                "work_title": work.title,
                "profile_id": "ru-law-article-v1",
                "requested_profile_id": "ru-law-article-v1",
                "resolved_profile_id": "ru-law-article-v1",
                "profile_conflict_flag": False,
                "profile_status": "available",
                "search_enabled": False,
                "topic": None,
                "input_brief": None,
                "target_path": target_rel,
                "root_dir": str(root),
                "output_file": str(output_file),
                "bundle": {
                    "slug": article_slug,
                    "brief": str(bundle["brief"]),
                    "evidence_pack": str(bundle["evidence_pack"]),
                    "claim_map": str(bundle["claim_map"]),
                    "draft": str(bundle["draft"]),
                    "review": str(bundle["review"]),
                    "final_markdown": str(bundle["final_markdown"]),
                    "checklist": str(bundle["checklist"]),
                    "docx": str(bundle["docx"]),
                    "state_manifest": str(article_bundle_manifest_path(work, article_slug)),
                },
                "related_context": [str(root / "AGENTS.md")],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    run_dir = orchestrator.store.runs_dir / "article-without-evidence-finalize"
    run_dir.mkdir(parents=True, exist_ok=True)
    request = {
        "run_id": "default:20260418-article-without-evidence-finalize",
        "lane": "article",
        "action": "finalize",
        "started_at": "2026-04-18T10:30:00+00:00",
        "project_id": "default",
        "project_title": root.name,
        "project_root": str(root),
        "work_id": work.slug,
        "work_title": work.title,
        "target": target_rel,
    }
    result = {
        "status": "success",
        "returncode": 0,
        "started_at": request["started_at"],
        "finished_at": "2026-04-18T10:31:00+00:00",
        "log_path": str(run_dir / "launcher.log"),
    }
    orchestrator._finalize_runtime_run(run_dir, request, result)
    return run_dir


def _write_workflow(
    root: Path,
    workflow_id: str,
    *,
    lane: str,
    promotion: dict[str, object] | None = None,
) -> None:
    workflow_dir = root / "output" / "runs" / workflow_id
    workflow_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "workflow-run/v1",
        "workflow_id": workflow_id,
        "run_id": workflow_id,
        "work_id": TEST_WORK_ID,
        "lane": lane,
        "action": "finalize",
        "execution_status": "succeeded",
        "readiness_status": "submission-ready",
        "started_at": "2026-04-18T10:00:00+00:00",
        "finished_at": "2026-04-18T11:00:00+00:00",
        "gates": [],
        "promotion": promotion or {"status": "promoted"},
    }
    (workflow_dir / "workflow.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_one_shot_report(root: Path, *, status: str) -> None:
    report_path = root / "works" / TEST_WORK_ID / "thesis" / "reviews" / "2026-04-18-one-shot-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": ONE_SHOT_REPORT_VERSION,
        "status": status,
        "finished_at": "2026-04-18T10:59:00+00:00",
    }
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
