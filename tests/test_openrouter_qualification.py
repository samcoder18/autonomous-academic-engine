from __future__ import annotations

import hashlib
import json
import re
import tempfile
import unittest
from dataclasses import replace
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
_SOURCE_SEED_PATH = Path("works/openrouter-live-smoke/articles/briefs/academic-source-acquirer-qualification.md")
_SOURCE_TARGET_PATH = Path("works/openrouter-live-smoke/articles/evidence/academic-source-acquirer-qualification.md")


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
        self.source_seed_path, self.source_target_path = self._build_source_fixtures(self.root)
        self.source_executor = _RecordingWritePlanExecutor(
            self.source_target_path,
            relative_write_path=_SOURCE_TARGET_PATH,
        )
        self.source_router = ExecutorRouter(
            default_executor=UnavailableExecutor("qualification-default"),
            default_executor_id="qualification-default",
            role_executors={"academic-source-acquirer": self.source_executor},
            role_executor_ids={"academic-source-acquirer": "openrouter"},
            role_policies={
                "academic-source-acquirer": {
                    "executor_id": "openrouter",
                    "execution_mode": "write-plan",
                }
            },
        )

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_registry_preserves_the_bounded_intake_candidate(self) -> None:
        self.assertEqual(
            QUALIFICATION_CANDIDATES[0],
            QualificationCandidate(
                role_id="academic-intake",
                work_id="openrouter-live-smoke",
                lane="article",
                action="qualify-intake",
                seed_path=_SEED_PATH.as_posix(),
                execution_mode="write-plan",
                context_paths=(_SEED_PATH.as_posix(),),
                write_path=_SEED_PATH.as_posix(),
                policy_path="agents/academic-intake.md",
                checkpoint="qualification:academic-intake",
            ),
        )

    def test_registry_contains_the_fixed_source_acquirer_candidate(self) -> None:
        candidates = {candidate.role_id: candidate for candidate in QUALIFICATION_CANDIDATES}

        source = candidates["academic-source-acquirer"]

        self.assertEqual(source.work_id, "openrouter-live-smoke")
        self.assertEqual(source.lane, "article")
        self.assertEqual(source.action, "qualify-source-acquirer")
        self.assertEqual(source.seed_path, _SOURCE_SEED_PATH.as_posix())
        self.assertEqual(source.context_paths, (_SOURCE_SEED_PATH.as_posix(),))
        self.assertEqual(source.write_path, _SOURCE_TARGET_PATH.as_posix())
        self.assertEqual(source.policy_path, "agents/academic-source-acquirer.md")
        self.assertEqual(source.checkpoint, "qualification:academic-source-acquirer")
        self.assertTrue(source.requires_no_search)

    def test_runs_only_source_acquirer_in_non_promoting_write_plan_sandbox(self) -> None:
        context_before = self.source_seed_path.read_bytes()
        target_before = self.source_target_path.read_bytes()

        result = run_openrouter_role_qualification(
            self.root,
            "academic-source-acquirer",
            "openrouter-live-smoke",
            _SOURCE_SEED_PATH.as_posix(),
            use_search=False,
            model=None,
            router=self.source_router,
            target_path=_SOURCE_TARGET_PATH.as_posix(),
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual([role.role_id for role in result.role_runs], ["academic-source-acquirer"])
        role = result.role_runs[0]
        self.assertEqual(role.executor_route, "role")
        self.assertEqual(role.executor_id, "openrouter")
        self.assertEqual(role.execution_mode, "write-plan")
        self.assertTrue(role.write_plan_applied)
        self.assertEqual(role.changed_paths, [_SOURCE_TARGET_PATH.as_posix()])
        self.assertEqual(role.forbidden_paths, [])
        self.assertEqual(role.checkpoints, ["qualification:academic-source-acquirer"])
        self.assertEqual([item.path for item in role.artifacts], [_SOURCE_TARGET_PATH.as_posix()])
        self.assertEqual(result.promotion.status, "skipped")
        self.assertEqual(result.promotion.reason, "qualification-no-promotion")
        self.assertEqual(self.source_seed_path.read_bytes(), context_before)
        self.assertEqual(self.source_target_path.read_bytes(), target_before)
        self.assertEqual(len(self.source_executor.calls), 2)
        self.assertTrue(all(call.role_id == "academic-source-acquirer" for call, _prompt in self.source_executor.calls))

        expected_metadata_keys = {
            "candidate_id",
            "context_path",
            "write_path",
            "context_before_sha256",
            "context_after_sha256",
            "write_before_sha256",
            "write_after_sha256",
            "canonical_unchanged",
        }
        self.assertEqual(set(result.metadata), expected_metadata_keys)
        self.assertEqual(result.metadata["candidate_id"], "academic-source-acquirer")
        self.assertEqual(result.metadata["context_path"], _SOURCE_SEED_PATH.as_posix())
        self.assertEqual(result.metadata["write_path"], _SOURCE_TARGET_PATH.as_posix())
        self.assertEqual(result.metadata["context_before_sha256"], hashlib.sha256(context_before).hexdigest())
        self.assertEqual(result.metadata["context_before_sha256"], result.metadata["context_after_sha256"])
        self.assertEqual(result.metadata["write_before_sha256"], hashlib.sha256(target_before).hexdigest())
        self.assertEqual(result.metadata["write_before_sha256"], result.metadata["write_after_sha256"])
        self.assertTrue(result.metadata["canonical_unchanged"])

        first_prompt = self.source_executor.calls[0][1]
        self.assertIn(_SOURCE_SEED_PATH.as_posix(), first_prompt)
        self.assertIn(_SOURCE_TARGET_PATH.as_posix(), first_prompt)
        self.assertNotIn(str(self.root), first_prompt)
        self.assertNotIn(str(self.source_seed_path), first_prompt)
        self.assertNotIn(str(self.source_target_path), first_prompt)
        self.assertFalse((self.root / "works/openrouter-live-smoke/articles/runs").exists())
        self.assertFalse((self.root / "output/jobs").exists())

    def test_source_acquirer_requires_exact_no_search_input_and_target_before_executor_invocation(self) -> None:
        attempts = (
            (_SOURCE_SEED_PATH.as_posix(), None, False),
            (_SOURCE_SEED_PATH.as_posix(), "works/openrouter-live-smoke/articles/evidence/not-approved.md", False),
            ("works/openrouter-live-smoke/articles/briefs/not-approved.md", _SOURCE_TARGET_PATH.as_posix(), False),
            (_SOURCE_SEED_PATH.as_posix(), _SOURCE_TARGET_PATH.as_posix(), True),
        )
        for seed_path, target_path, use_search in attempts:
            with self.subTest(seed_path=seed_path, target_path=target_path, use_search=use_search):
                with self.assertRaises(QualificationError):
                    run_openrouter_role_qualification(
                        self.root,
                        "academic-source-acquirer",
                        "openrouter-live-smoke",
                        seed_path,
                        use_search=use_search,
                        model=None,
                        router=self.source_router,
                        target_path=target_path,
                    )
                self.assertEqual(self.source_executor.calls, [])

        self.source_target_path.unlink()
        with self.assertRaises(QualificationError):
            run_openrouter_role_qualification(
                self.root,
                "academic-source-acquirer",
                "openrouter-live-smoke",
                _SOURCE_SEED_PATH.as_posix(),
                use_search=False,
                model=None,
                router=self.source_router,
                target_path=_SOURCE_TARGET_PATH.as_posix(),
            )
        self.assertEqual(self.source_executor.calls, [])

    def test_source_acquirer_rejects_symlinked_context_or_target_before_executor_invocation(self) -> None:
        for fixture_path in (self.source_seed_path, self.source_target_path):
            with self.subTest(fixture_path=fixture_path):
                outside = self.root.parent / f"outside-{fixture_path.name}"
                outside.write_text("# Outside\n", encoding="utf-8")
                original = fixture_path.read_text(encoding="utf-8")
                fixture_path.unlink()
                fixture_path.symlink_to(outside)
                with self.assertRaises(QualificationError):
                    run_openrouter_role_qualification(
                        self.root,
                        "academic-source-acquirer",
                        "openrouter-live-smoke",
                        _SOURCE_SEED_PATH.as_posix(),
                        use_search=False,
                        model=None,
                        router=self.source_router,
                        target_path=_SOURCE_TARGET_PATH.as_posix(),
                    )
                self.assertEqual(self.source_executor.calls, [])
                fixture_path.unlink()
                fixture_path.write_text(original, encoding="utf-8")

    def test_source_acquirer_rejects_malformed_candidate_or_router_before_executor_invocation(self) -> None:
        source = next(item for item in QUALIFICATION_CANDIDATES if item.role_id == "academic-source-acquirer")
        malformed_candidates = (
            replace(source, action="qualification-action-removed"),
            replace(source, write_path="works/openrouter-live-smoke/articles/evidence/not-approved.md"),
            replace(source, policy_path="agents/academic-intake.md"),
        )
        invalid_routers = (
            replace(self.source_router, role_policies={}),
            replace(
                self.source_router,
                role_policies={
                    "academic-source-acquirer": {
                        "executor_id": "openrouter",
                        "execution_mode": "read-only",
                    }
                },
            ),
        )

        for malformed_candidate in malformed_candidates:
            with self.subTest(malformed_candidate=malformed_candidate):
                with patch(
                    "academic_engine.openrouter_qualification.QUALIFICATION_CANDIDATES",
                    (QUALIFICATION_CANDIDATES[0], malformed_candidate),
                ):
                    self._assert_source_router_forbidden(self.source_router)
        with patch(
            "academic_engine.openrouter_qualification.QUALIFICATION_CANDIDATES",
            (QUALIFICATION_CANDIDATES[0],),
        ):
            self._assert_source_router_forbidden(self.source_router)
        for invalid_router in invalid_routers:
            with self.subTest(invalid_router=invalid_router):
                self._assert_source_router_forbidden(invalid_router)

    def test_source_acquirer_marks_workflow_failed_when_any_canonical_fixture_drifts(self) -> None:
        for canonical_fixture in (self.source_seed_path, self.source_target_path):
            with self.subTest(canonical_fixture=canonical_fixture):
                self.source_executor.mutate_canonical_path = canonical_fixture
                result = run_openrouter_role_qualification(
                    self.root,
                    "academic-source-acquirer",
                    "openrouter-live-smoke",
                    _SOURCE_SEED_PATH.as_posix(),
                    use_search=False,
                    model=None,
                    router=self.source_router,
                    target_path=_SOURCE_TARGET_PATH.as_posix(),
                )
                self.assertEqual(result.execution_status, "failed")
                self.assertFalse(result.metadata["canonical_unchanged"])
                self.assertTrue(any(item["code"] == "qualification-canonical-drift" for item in result.blockers))
                self.source_executor.mutate_canonical_path = None

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

    def test_rejects_injected_router_with_extra_or_missing_role_maps_before_executor_invocation(self) -> None:
        invalid_routers = (
            replace(
                self.router,
                role_executors={
                    "academic-intake": self.recording_executor,
                    "academic-draft-writer": self.recording_executor,
                },
            ),
            replace(
                self.router,
                role_executor_ids={
                    "academic-intake": "openrouter",
                    "academic-draft-writer": "openrouter",
                },
            ),
            replace(
                self.router,
                role_policies={
                    "academic-intake": {"executor_id": "openrouter", "execution_mode": "write-plan"},
                    "academic-draft-writer": {"executor_id": "openrouter", "execution_mode": "write-plan"},
                },
            ),
            replace(self.router, role_executors={}),
            replace(self.router, role_executor_ids={}),
            replace(self.router, role_policies={}),
        )

        for router in invalid_routers:
            with self.subTest(router=router):
                self._assert_injected_router_forbidden(router)

    def test_rejects_injected_router_with_wrong_candidate_route_or_policy_before_executor_invocation(self) -> None:
        invalid_routers = (
            replace(self.router, role_executor_ids={"academic-intake": "codex-cli"}),
            replace(
                self.router,
                role_policies={"academic-intake": {"executor_id": "codex-cli", "execution_mode": "write-plan"}},
            ),
            replace(
                self.router,
                role_policies={"academic-intake": {"executor_id": "openrouter", "execution_mode": "read-only"}},
            ),
            replace(
                self.router,
                role_policies={
                    "academic-intake": {
                        "executor_id": "openrouter",
                        "execution_mode": "write-plan",
                        "unexpected": "field",
                    }
                },
            ),
        )

        for router in invalid_routers:
            with self.subTest(router=router):
                self._assert_injected_router_forbidden(router)

    def test_rejects_default_or_evaluator_shaped_injected_router_before_executor_invocation(self) -> None:
        invalid_routers = (
            ExecutorRouter(
                default_executor=UnavailableExecutor("codex-cli"),
                default_executor_id="codex-cli",
            ),
            replace(self.router, default_executor=UnavailableExecutor("wrong-default")),
            replace(self.router, default_executor_id="wrong-default"),
            replace(self.router, default_executor=self.recording_executor),
            replace(self.router, evaluator_executor=self.recording_executor),
            replace(self.router, evaluator_executor_id="openrouter"),
            replace(self.router, verifier_executor=self.recording_executor),
            replace(self.router, verifier_executor_id="openrouter"),
        )

        for router in invalid_routers:
            with self.subTest(router=router):
                self._assert_injected_router_forbidden(router)

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

    def _assert_injected_router_forbidden(self, router: ExecutorRouter) -> None:
        self.recording_executor.calls.clear()
        with self.assertRaises(ProviderExecutionError) as caught:
            run_openrouter_role_qualification(
                self.root,
                "academic-intake",
                "openrouter-live-smoke",
                _SEED_PATH.as_posix(),
                use_search=False,
                model=None,
                router=router,
            )
        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(self.recording_executor.calls, [])

    def _assert_source_router_forbidden(self, router: ExecutorRouter) -> None:
        self.source_executor.calls.clear()
        with self.assertRaises(ProviderExecutionError) as caught:
            run_openrouter_role_qualification(
                self.root,
                "academic-source-acquirer",
                "openrouter-live-smoke",
                _SOURCE_SEED_PATH.as_posix(),
                use_search=False,
                model=None,
                router=router,
                target_path=_SOURCE_TARGET_PATH.as_posix(),
            )
        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(self.source_executor.calls, [])

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

    @staticmethod
    def _build_source_fixtures(root: Path) -> tuple[Path, Path]:
        source_seed = root / _SOURCE_SEED_PATH
        source_seed.parent.mkdir(parents=True, exist_ok=True)
        source_seed.write_text("# Qualification source dossier\n", encoding="utf-8")
        source_target = root / _SOURCE_TARGET_PATH
        source_target.parent.mkdir(parents=True, exist_ok=True)
        source_target.write_text("# Qualification evidence template\n", encoding="utf-8")
        policy = root / "agents/academic-source-acquirer.md"
        policy.parent.mkdir(parents=True, exist_ok=True)
        policy.write_text("# Source acquirer policy\n", encoding="utf-8")
        return source_seed, source_target


class _RecordingWritePlanExecutor:
    def __init__(self, canonical_seed: Path, *, relative_write_path: Path = _SEED_PATH) -> None:
        self.canonical_seed = canonical_seed
        self.relative_write_path = relative_write_path
        self.calls: list[tuple[RoleExecutionContext, str]] = []
        self.mutate_canonical = False
        self.remove_canonical = False
        self.mutate_canonical_path: Path | None = None

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        self.calls.append((context, prompt))
        output = context.output_file
        if "Provider result evidence envelope:" not in prompt:
            target = context.sandbox_dir / self.relative_write_path
            plan = {
                "version": "provider-write-plan/v1",
                "workflow_id": _prompt_field(prompt, "Workflow ID"),
                "role_run_id": _prompt_field(prompt, "Role Run ID"),
                "role_id": _prompt_field(prompt, "Role ID"),
                "work_id": _prompt_field(prompt, "Work ID"),
                "operations": [
                    {
                        "path": self.relative_write_path.as_posix(),
                        "base_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "content": "# Qualified qualification fixture\n",
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
        if self.mutate_canonical_path is not None:
            self.mutate_canonical_path.write_text("# Canonical drift\n", encoding="utf-8")


def _prompt_field(prompt: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}: (.+)$", prompt, re.MULTILINE)
    assert match is not None
    return match.group(1).strip()


if __name__ == "__main__":
    unittest.main()
