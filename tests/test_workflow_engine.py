from __future__ import annotations

import hashlib
import json
import re
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from academic_engine.action_specs import (
    AllowedWriteScope,
    ExecutionContract,
    QualityGate,
    RepairPolicy,
    RequiredArtifact,
)
from academic_engine.runtime_status import load_runtime_record
from academic_engine.state import RuntimeStore
from academic_engine.workflow_engine import WorkflowBusyError, WorkflowEngine, WorkflowLease, build_role_plan


class WorkflowEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.work_dir = self.root / "works" / "demo"
        self.target = self.work_dir / "thesis" / "manuscript" / "sections" / "01.md"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# Original\n", encoding="utf-8")
        (self.work_dir / "work.toml").write_text('slug = "demo"\n', encoding="utf-8")
        (self.work_dir / "work-canon.md").write_text("# Canon\n", encoding="utf-8")
        (self.root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
        (self.root / "workspace.toml").write_text(
            'version = 1\ndefault_work = "demo"\n[works]\ndemo = "works/demo"\n',
            encoding="utf-8",
        )
        (self.root / "meta").mkdir()
        (self.root / "meta" / "master-protocol.md").write_text("# Protocol\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def contract(self, *, action: str = "style-pass", target: Path | None = None) -> ExecutionContract:
        resolved_target = target or self.target
        return ExecutionContract(
            lane="thesis",
            action=action,
            title="Test",
            summary="Test workflow",
            target_kind="markdown",
            target_validation="test",
            prompt_rules=(),
            deliverables=(),
            required_context=(RequiredArtifact("target", str(resolved_target), "required", "Target."),),
            allowed_write_scopes=(AllowedWriteScope("target", str(resolved_target), "Target."),),
            required_outputs=(RequiredArtifact("target", str(resolved_target), "required", "Target."),),
            required_checkpoints=("context-loaded", "completed"),
            terminal_statuses=("submission-ready", "strong-draft-with-blockers"),
            quality_gates=(
                QualityGate("lane-boundary", "Stay in lane.", ("submission-ready",)),
                QualityGate("evaluator-verdict", "Evaluator required.", ("submission-ready",)),
            ),
            repair_policy=RepairPolicy(
                eligible=True,
                max_iterations=2,
                safe_only=True,
                triggers=("blockers",),
                terminal_reasons=("ready", "max-repair-iterations"),
            ),
            transitions=(),
            metadata=(("work_id", "demo"),),
        )

    def test_promotes_valid_draft_after_independent_evaluator(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            if "Role ID: thesis-style-editor" in prompt:
                path = sandbox / self.target.relative_to(self.root)
                path.write_text("# Updated\n", encoding="utf-8")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=_evaluator_payload("submission-ready")
                if "Role ID: thesis-submission-evaluator" in prompt
                else None,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.readiness_status, "submission-ready")
        self.assertEqual(result.promotion.status, "promoted")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# Updated\n")
        workflow_payload = json.loads((Path(result.workflow_dir) / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(workflow_payload["version"], "workflow-run/v1")
        self.assertEqual(len(workflow_payload["role_runs"]), 2)
        runtime_record = load_runtime_record(Path(result.workflow_dir), "workflow-run")
        self.assertIsNotNone(runtime_record)
        assert runtime_record is not None
        self.assertEqual(runtime_record.workflow_id, result.workflow_id)
        self.assertEqual(runtime_record.promotion_status, "promoted")

    def test_machine_source_gate_vetoes_ready_but_promotes_draft(self) -> None:
        contract = self.contract(action="write-section")

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            if "Role ID: thesis-draft-writer" in prompt:
                path = sandbox / self.target.relative_to(self.root)
                path.write_text("# Draft without live provenance\n", encoding="utf-8")
            if "Role ID: thesis-source-verifier" in prompt:
                verdict = _source_payload()
            elif "Role ID: thesis-submission-evaluator" in prompt:
                verdict = _evaluator_payload("submission-ready")
            else:
                verdict = None
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=verdict,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="write-section",
            contract=contract,
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.readiness_status, "strong-draft-with-blockers")
        self.assertEqual(result.promotion.status, "promoted")
        self.assertIn("Draft without live provenance", self.target.read_text(encoding="utf-8"))
        gate = next(item for item in result.gates if item.gate_id == "live-source-provenance")
        self.assertEqual(gate.status, "block")

    def test_live_source_gate_requires_every_primary_record_to_be_verifiable(self) -> None:
        source_manifest = self.work_dir / "thesis" / "sources" / "sources.json"
        source_manifest.parent.mkdir(parents=True, exist_ok=True)

        def source(notes: str = "") -> dict[str, object]:
            return {
                "identifier": f"law-{notes or 'live'}",
                "kind": "statute",
                "canonical_url": "https://example.test/law",
                "content_hash": "a" * 64,
                "provenance": {
                    "retrieved_at": "2026-06-15T12:00:00+00:00",
                    "canonical_url": "https://example.test/law",
                    "http_status": 200,
                    "notes": notes,
                },
            }

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            role_id = _prompt_field(prompt, "Role ID")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=(
                    _source_payload()
                    if role_id == "thesis-source-verifier"
                    else _evaluator_payload("submission-ready")
                    if role_id == "thesis-submission-evaluator"
                    else None
                ),
            )

        source_manifest.write_text(json.dumps({"sources": [source()]}), encoding="utf-8")
        ready = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="write-section",
            contract=self.contract(action="write-section"),
            base_prompt="test",
            use_search=False,
            model=None,
        )
        self.assertEqual(ready.readiness_status, "submission-ready")

        source_manifest.write_text(json.dumps({"sources": [source(), source("stub-mode")]}), encoding="utf-8")
        blocked = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="write-section",
            contract=self.contract(action="write-section"),
            base_prompt="test",
            use_search=False,
            model=None,
        )
        gate = next(item for item in blocked.gates if item.gate_id == "live-source-provenance")
        self.assertEqual(gate.status, "block")
        self.assertEqual(gate.details["stub"], 1)

    def test_forbidden_write_blocks_promotion(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            (sandbox / "AGENTS.md").write_text("# Unauthorized\n", encoding="utf-8")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root), Path("AGENTS.md")],
                verdict=_evaluator_payload("submission-ready")
                if "Role ID: thesis-submission-evaluator" in prompt
                else None,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertEqual(result.promotion.status, "blocked")
        self.assertEqual((self.root / "AGENTS.md").read_text(encoding="utf-8"), "# Agents\n")

    def test_evaluator_is_enforced_read_only(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            target = sandbox / self.target.relative_to(self.root)
            role_id = _prompt_field(prompt, "Role ID")
            if role_id == "thesis-submission-evaluator":
                target.write_text("# Evaluator mutation\n", encoding="utf-8")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=_evaluator_payload("submission-ready") if role_id == "thesis-submission-evaluator" else None,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        evaluator = result.role_runs[-1]
        self.assertEqual(evaluator.status, "failed")
        self.assertIn(self.target.relative_to(self.root).as_posix(), evaluator.forbidden_paths)
        self.assertEqual(result.promotion.status, "blocked")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# Original\n")

    def test_malformed_role_result_fails_closed(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("role complete without structured result\n", encoding="utf-8")

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertEqual(result.promotion.status, "blocked")
        self.assertTrue(any(item["code"] == "role-result-block-missing" for item in result.blockers))

    def test_role_result_work_mismatch_fails_closed(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=None,
                identity_overrides={"work_id": "other-work"},
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertTrue(any(item["code"] == "role-result-identity-mismatch" for item in result.blockers))

    def test_missing_checkpoint_evidence_fails_closed(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=None,
                checkpoint_evidence_override={},
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertTrue(any(item["code"] == "role-result-success-without-evidence" for item in result.blockers))

    def test_successful_role_result_with_blockers_fails_closed(self) -> None:
        blocker = {
            "category": "citation",
            "code": "citation-gap",
            "message": "Citation support remains incomplete.",
            "repairable": True,
        }

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=None,
                blockers=[blocker],
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertTrue(any(item["code"] == "role-result-success-with-blockers" for item in result.blockers))

    def test_transient_role_failure_retries_once(self) -> None:
        attempts: dict[str, int] = {}

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            role_id = _prompt_field(prompt, "Role ID")
            attempts[role_id] = attempts.get(role_id, 0) + 1
            if role_id == "thesis-style-editor" and attempts[role_id] == 1:
                raise OSError("temporary process failure")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=_evaluator_payload("submission-ready") if role_id == "thesis-submission-evaluator" else None,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual(result.role_runs[0].attempt_count, 2)
        self.assertEqual(attempts["thesis-style-editor"], 2)

    def test_role_timeout_retries_once_then_fails(self) -> None:
        attempts = 0

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            nonlocal attempts
            attempts += 1
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=None,
            )

        result = WorkflowEngine(
            self.root,
            role_executor=executor,
            role_timeout_seconds=0,
        ).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertEqual(result.role_runs[0].attempt_count, 2)
        self.assertEqual(attempts, 2)

    def test_file_deletion_blocks_promotion(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            target = sandbox / self.target.relative_to(self.root)
            target.unlink()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("deleted target\n", encoding="utf-8")

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.promotion.status, "blocked")
        self.assertTrue(self.target.exists())

    def test_repair_loop_is_bounded_to_two_iterations(self) -> None:
        blocker = {
            "category": "citation",
            "code": "citation-still-open",
            "message": "Citation support remains incomplete.",
            "repairable": True,
        }

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            role_id = _prompt_field(prompt, "Role ID")
            if role_id == "thesis-submission-evaluator":
                verdict = _evaluator_payload("strong-draft-with-blockers", blockers=[blocker])
            elif role_id == "thesis-source-verifier":
                verdict = _source_payload()
            else:
                verdict = None
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=verdict,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="review-section",
            contract=self.contract(action="review-section"),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        iterations = {
            checkpoint.split(":", 1)[0]
            for role in result.role_runs
            for checkpoint in role.checkpoints
            if checkpoint.startswith("repair-")
        }
        self.assertEqual(iterations, {"repair-1", "repair-2"})
        self.assertEqual(result.readiness_status, "strong-draft-with-blockers")

    def test_canonical_conflict_preserves_user_change(self) -> None:
        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            if "Role ID: thesis-style-editor" in prompt:
                path = sandbox / self.target.relative_to(self.root)
                path.write_text("# Agent change\n", encoding="utf-8")
                self.target.write_text("# User change\n", encoding="utf-8")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=_evaluator_payload("submission-ready")
                if "Role ID: thesis-submission-evaluator" in prompt
                else None,
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(result.promotion.status, "conflict")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# User change\n")

    def test_work_lease_is_exclusive_per_work(self) -> None:
        with WorkflowLease(self.root, "demo"):
            with self.assertRaises(WorkflowBusyError):
                with WorkflowLease(self.root, "demo"):
                    pass

    def test_workflow_lease_global_limit_is_two(self) -> None:
        with WorkflowLease(self.root, "alpha"), WorkflowLease(self.root, "beta"):
            with self.assertRaises(WorkflowBusyError):
                with WorkflowLease(self.root, "gamma"):
                    pass

    def test_two_works_run_concurrently_without_artifact_mixing(self) -> None:
        beta_work = self.root / "works" / "beta"
        beta_target = beta_work / "thesis" / "manuscript" / "sections" / "01.md"
        beta_target.parent.mkdir(parents=True)
        beta_target.write_text("# Beta original\n", encoding="utf-8")
        (beta_work / "work.toml").write_text('slug = "beta"\n', encoding="utf-8")
        (beta_work / "work-canon.md").write_text("# Beta canon\n", encoding="utf-8")
        barrier = threading.Barrier(2)

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            work_id = _prompt_field(prompt, "Work ID")
            role_id = _prompt_field(prompt, "Role ID")
            target = sandbox / "works" / work_id / "thesis" / "manuscript" / "sections" / "01.md"
            if role_id == "thesis-style-editor":
                barrier.wait(timeout=2)
                target.write_text(f"# Updated {work_id}\n", encoding="utf-8")
            _write_role_result(
                output,
                prompt,
                sandbox,
                [target.relative_to(sandbox)],
                verdict=_evaluator_payload("submission-ready") if role_id == "thesis-submission-evaluator" else None,
            )

        def run(work_id: str, work_dir: Path, target: Path):
            return WorkflowEngine(self.root, role_executor=executor).run(
                work_id=work_id,
                work_dir=work_dir,
                lane="thesis",
                action="style-pass",
                contract=self.contract(target=target),
                base_prompt="test",
                use_search=False,
                model=None,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            alpha_future = pool.submit(run, "demo", self.work_dir, self.target)
            beta_future = pool.submit(run, "beta", beta_work, beta_target)
            alpha = alpha_future.result(timeout=5)
            beta = beta_future.result(timeout=5)

        self.assertEqual(alpha.execution_status, "succeeded")
        self.assertEqual(beta.execution_status, "succeeded")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# Updated demo\n")
        self.assertEqual(beta_target.read_text(encoding="utf-8"), "# Updated beta\n")

    def test_runtime_store_tracks_active_runs_per_work(self) -> None:
        store = RuntimeStore(self.root)
        store.set_active_run({"run_id": "alpha:1", "work_id": "alpha"})
        store.set_active_run({"run_id": "beta:1", "work_id": "beta"})

        self.assertEqual(store.get_active_run("alpha")["run_id"], "alpha:1")
        self.assertEqual(store.get_active_run("beta")["run_id"], "beta:1")
        self.assertEqual(len(store.list_active_runs()), 2)

        store.clear_active_run("alpha")
        self.assertIsNone(store.get_active_run("alpha"))
        self.assertEqual(store.get_active_run("beta")["run_id"], "beta:1")

    def test_role_plan_assigns_checkpoint_to_every_role(self) -> None:
        checkpoints = (
            "brief-normalized",
            "evidence-updated",
            "claim-map-updated",
            "draft-updated",
            "reviewed",
            "final-status-issued",
        )

        nodes = build_role_plan("article", "article", checkpoints)

        self.assertTrue(all(node.checkpoints for node in nodes))
        observed = {checkpoint for node in nodes for checkpoint in node.checkpoints}
        self.assertTrue(set(checkpoints).issubset(observed))
        source_verifier = next(node for node in nodes if node.role_id == "academic-source-verifier")
        self.assertEqual(source_verifier.checkpoints, ("role-completed:academic-source-verifier",))


def _evaluator_payload(
    status: str,
    *,
    blockers: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "verdict_version": "1",
        "lane": "thesis",
        "kind": "submission-evaluator",
        "status": status,
        "summary": "Independent evaluation complete.",
    }
    if blockers:
        payload["blockers"] = blockers
    return payload


def _source_payload() -> dict[str, str]:
    return {
        "verdict_version": "1",
        "lane": "thesis",
        "kind": "source-verifier",
        "status": "reviewed",
        "summary": "Source review complete.",
    }


def _write_role_result(
    output: Path,
    prompt: str,
    sandbox: Path,
    artifact_paths: list[Path],
    *,
    verdict: dict[str, object] | None,
    status: str = "succeeded",
    blockers: list[dict[str, object]] | None = None,
    identity_overrides: dict[str, str] | None = None,
    checkpoint_evidence_override: dict[str, list[str]] | None = None,
) -> None:
    checkpoint_match = re.search(r"Required checkpoints:\n(?P<body>\[[^\n]*\])", prompt)
    assert checkpoint_match is not None
    checkpoints = json.loads(checkpoint_match.group("body"))
    artifacts = []
    for relative in artifact_paths:
        path = sandbox / relative
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts.append({"path": relative.as_posix(), "sha256": digest})
    evidence_path = artifacts[0]["path"] if artifacts else ""
    payload = {
        "version": "role-result/v1",
        "workflow_id": _prompt_field(prompt, "Workflow ID"),
        "role_run_id": _prompt_field(prompt, "Role Run ID"),
        "role_id": _prompt_field(prompt, "Role ID"),
        "work_id": _prompt_field(prompt, "Work ID"),
        "lane": _prompt_field(prompt, "Lane/action").split("/", 1)[0],
        "action": _prompt_field(prompt, "Lane/action").split("/", 1)[1],
        "status": status,
        "checkpoints": checkpoints,
        "checkpoint_evidence": (
            checkpoint_evidence_override
            if checkpoint_evidence_override is not None
            else {checkpoint: [evidence_path] for checkpoint in checkpoints}
        ),
        "blockers": blockers or [],
        "artifacts": artifacts,
        "verdict": verdict,
    }
    payload.update(identity_overrides or {})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(f"role complete\n```role-result\n{json.dumps(payload)}\n```\n", encoding="utf-8")


def _prompt_field(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}: (.+)$", prompt, re.MULTILINE)
    assert match is not None
    return match.group(1).strip()


if __name__ == "__main__":
    unittest.main()
