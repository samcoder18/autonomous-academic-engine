from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_engine.executors import (
    ExecutorRouter,
    ProviderExecutionError,
    RoleExecutionContext,
    UnavailableExecutor,
)
from academic_engine.openrouter_qualification import (
    QUALIFICATION_CANDIDATES,
    QualificationCandidate,
    QualificationError,
    run_openrouter_role_qualification,
)

_SEED_PATH = Path("works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md")


class OpenRouterQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self.seed_path = self._build_workspace(self.root)
        self.recording_executor = _RecordingWritePlanExecutor(self.seed_path)
        self.router = ExecutorRouter(
            default_executor=UnavailableExecutor("qualification-default"),
            default_executor_id="qualification-default",
            role_executors={"academic-intake": self.recording_executor},
            role_executor_ids={"academic-intake": "openrouter"},
            role_policies={
                "academic-intake": {
                    "executor_id": "openrouter",
                    "execution_mode": "write-plan",
                }
            },
        )

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_registry_contains_only_the_bounded_intake_candidate(self) -> None:
        self.assertEqual(
            QUALIFICATION_CANDIDATES,
            (
                QualificationCandidate(
                    role_id="academic-intake",
                    work_id="openrouter-live-smoke",
                    lane="article",
                    action="qualify-intake",
                    seed_path=_SEED_PATH.as_posix(),
                    execution_mode="write-plan",
                ),
            ),
        )

    def test_runs_only_intake_in_non_promoting_write_plan_sandbox(self) -> None:
        canonical_before = self.seed_path.read_bytes()

        result = run_openrouter_role_qualification(
            self.root,
            "academic-intake",
            "openrouter-live-smoke",
            _SEED_PATH.as_posix(),
            use_search=False,
            model=None,
            router=self.router,
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual([role.role_id for role in result.role_runs], ["academic-intake"])
        role = result.role_runs[0]
        self.assertEqual(role.executor_route, "role")
        self.assertEqual(role.executor_id, "openrouter")
        self.assertEqual(role.execution_mode, "write-plan")
        self.assertTrue(role.write_plan_applied)
        self.assertEqual(role.changed_paths, [_SEED_PATH.as_posix()])
        self.assertEqual(result.promotion.status, "skipped")
        self.assertEqual(result.promotion.reason, "qualification-no-promotion")
        self.assertEqual(self.seed_path.read_bytes(), canonical_before)
        self.assertEqual(len(self.recording_executor.calls), 2)
        self.assertTrue(all(call.role_id == "academic-intake" for call, _prompt in self.recording_executor.calls))

        expected_metadata_keys = {
            "candidate_id",
            "allowed_path",
            "before_sha256",
            "after_sha256",
            "canonical_unchanged",
        }
        self.assertEqual(set(result.metadata), expected_metadata_keys)
        self.assertEqual(result.metadata["candidate_id"], "academic-intake")
        self.assertEqual(result.metadata["allowed_path"], _SEED_PATH.as_posix())
        self.assertEqual(result.metadata["before_sha256"], hashlib.sha256(canonical_before).hexdigest())
        self.assertEqual(result.metadata["before_sha256"], result.metadata["after_sha256"])
        self.assertTrue(result.metadata["canonical_unchanged"])

        workflow_payload = json.loads((Path(result.workflow_dir) / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(workflow_payload["metadata"], result.metadata)
        self.assertFalse((self.root / "works/openrouter-live-smoke/articles/runs").exists())
        self.assertFalse((self.root / "output/jobs").exists())

    def test_rejects_unknown_and_removed_candidates_before_executor_invocation(self) -> None:
        with self.assertRaises(ProviderExecutionError) as unknown:
            run_openrouter_role_qualification(
                self.root,
                "academic-draft-writer",
                "openrouter-live-smoke",
                _SEED_PATH.as_posix(),
                use_search=False,
                model=None,
                router=self.router,
            )
        self.assertEqual(unknown.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(self.recording_executor.calls, [])

        with patch("academic_engine.openrouter_qualification.QUALIFICATION_CANDIDATES", ()):
            with self.assertRaises(ProviderExecutionError) as removed:
                run_openrouter_role_qualification(
                    self.root,
                    "academic-intake",
                    "openrouter-live-smoke",
                    _SEED_PATH.as_posix(),
                    use_search=False,
                    model=None,
                    router=self.router,
                )
        self.assertEqual(removed.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(self.recording_executor.calls, [])

    def test_rejects_invalid_work_and_seed_before_executor_invocation(self) -> None:
        attempts = (
            ("wrong-work", _SEED_PATH.as_posix()),
            ("openrouter-live-smoke", "works/openrouter-live-smoke/articles/briefs/not-the-seed.md"),
        )
        for work_id, seed_path in attempts:
            with self.subTest(work_id=work_id, seed_path=seed_path):
                with self.assertRaises(QualificationError):
                    run_openrouter_role_qualification(
                        self.root,
                        "academic-intake",
                        work_id,
                        seed_path,
                        use_search=False,
                        model=None,
                        router=self.router,
                    )
                self.assertEqual(self.recording_executor.calls, [])

        self.seed_path.unlink()
        with self.assertRaises(QualificationError):
            run_openrouter_role_qualification(
                self.root,
                "academic-intake",
                "openrouter-live-smoke",
                _SEED_PATH.as_posix(),
                use_search=False,
                model=None,
                router=self.router,
            )
        self.assertEqual(self.recording_executor.calls, [])

    def test_rejects_canonical_seed_symlinked_outside_workspace_before_executor_invocation(self) -> None:
        outside = self.root.parent / "outside-qualification-seed.md"
        outside.write_text("# Outside\n", encoding="utf-8")
        self.seed_path.unlink()
        self.seed_path.symlink_to(outside)

        with self.assertRaises(QualificationError):
            run_openrouter_role_qualification(
                self.root,
                "academic-intake",
                "openrouter-live-smoke",
                _SEED_PATH.as_posix(),
                use_search=False,
                model=None,
                router=self.router,
            )

        self.assertEqual(self.recording_executor.calls, [])

    def test_marks_workflow_failed_when_canonical_fixture_drifts(self) -> None:
        self.recording_executor.mutate_canonical = True

        result = run_openrouter_role_qualification(
            self.root,
            "academic-intake",
            "openrouter-live-smoke",
            _SEED_PATH.as_posix(),
            use_search=False,
            model=None,
            router=self.router,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertEqual(result.status, "failed")
        self.assertFalse(result.metadata["canonical_unchanged"])
        self.assertTrue(any(item["code"] == "qualification-canonical-drift" for item in result.blockers))
        workflow_payload = json.loads((Path(result.workflow_dir) / "workflow.json").read_text(encoding="utf-8"))
        self.assertFalse(workflow_payload["metadata"]["canonical_unchanged"])
        self.assertTrue(any(item["code"] == "qualification-canonical-drift" for item in workflow_payload["blockers"]))

    def test_marks_workflow_failed_when_canonical_fixture_is_removed(self) -> None:
        self.recording_executor.remove_canonical = True

        result = run_openrouter_role_qualification(
            self.root,
            "academic-intake",
            "openrouter-live-smoke",
            _SEED_PATH.as_posix(),
            use_search=False,
            model=None,
            router=self.router,
        )

        self.assertEqual(result.execution_status, "failed")
        self.assertFalse(result.metadata["canonical_unchanged"])
        self.assertEqual(result.metadata["after_sha256"], "")
        self.assertTrue(any(item["code"] == "qualification-canonical-drift" for item in result.blockers))

    @staticmethod
    def _build_workspace(root: Path) -> Path:
        seed_path = root / _SEED_PATH
        seed_path.parent.mkdir(parents=True)
        seed_path.write_text("# Qualification seed\n", encoding="utf-8")
        work_dir = root / "works/openrouter-live-smoke"
        (work_dir / "work.toml").write_text('slug = "openrouter-live-smoke"\n', encoding="utf-8")
        (work_dir / "work-canon.md").write_text("# Canon\n", encoding="utf-8")
        policy = root / "agents/academic-intake.md"
        policy.parent.mkdir(parents=True)
        policy.write_text("# Intake policy\n", encoding="utf-8")
        return seed_path


class _RecordingWritePlanExecutor:
    def __init__(self, canonical_seed: Path) -> None:
        self.canonical_seed = canonical_seed
        self.calls: list[tuple[RoleExecutionContext, str]] = []
        self.mutate_canonical = False
        self.remove_canonical = False

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        self.calls.append((context, prompt))
        output = context.output_file
        if "Provider result evidence envelope:" not in prompt:
            target = context.sandbox_dir / _SEED_PATH
            plan = {
                "version": "provider-write-plan/v1",
                "workflow_id": _prompt_field(prompt, "Workflow ID"),
                "role_run_id": _prompt_field(prompt, "Role Run ID"),
                "role_id": _prompt_field(prompt, "Role ID"),
                "work_id": _prompt_field(prompt, "Work ID"),
                "operations": [
                    {
                        "path": _SEED_PATH.as_posix(),
                        "base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "content": "# Qualified intake fixture\n",
                    }
                ],
            }
            output.write_text(f"```provider-write-plan\n{json.dumps(plan)}\n```\n", encoding="utf-8")
            return

        envelope_match = re.search(
            r"Provider result evidence envelope:\n(?P<body>\{.*?\})\n\nReturn exactly",
            prompt,
            re.DOTALL,
        )
        assert envelope_match is not None
        envelope = json.loads(envelope_match.group("body"))["provider_result_evidence_envelope"]
        checkpoints = json.loads(re.search(r"Required checkpoints:\n(?P<body>\[[^\n]*\])", prompt).group("body"))
        result = {
            "version": "role-result/v1",
            "workflow_id": _prompt_field(prompt, "Workflow ID"),
            "role_run_id": _prompt_field(prompt, "Role Run ID"),
            "role_id": _prompt_field(prompt, "Role ID"),
            "work_id": _prompt_field(prompt, "Work ID"),
            "lane": _prompt_field(prompt, "Lane/action").split("/", 1)[0],
            "action": _prompt_field(prompt, "Lane/action").split("/", 1)[1],
            "status": "succeeded",
            "checkpoints": checkpoints,
            "checkpoint_evidence": envelope["checkpoint_evidence"],
            "blockers": [],
            "artifacts": envelope["artifacts"],
            "verdict": None,
        }
        output.write_text(f"```role-result\n{json.dumps(result)}\n```\n", encoding="utf-8")
        if self.mutate_canonical:
            self.canonical_seed.write_text("# Canonical drift\n", encoding="utf-8")
        if self.remove_canonical:
            self.canonical_seed.unlink()


def _prompt_field(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}: (.+)$", prompt, re.MULTILINE)
    assert match is not None
    return match.group(1).strip()


if __name__ == "__main__":
    unittest.main()
