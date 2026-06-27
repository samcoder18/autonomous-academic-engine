from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_engine.executors import (
    CallableRoleExecutor,
    CodexCliExecutor,
    ExecutorRouter,
    ExecutorUnavailableError,
    RoleExecutionContext,
    StubApiExecutor,
    build_executor_router,
)


class RecordingExecutor:
    def __init__(self, label: str):
        self.label = label
        self.calls: list[tuple[RoleExecutionContext, str]] = []

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        self.calls.append((context, prompt))
        context.output_file.parent.mkdir(parents=True, exist_ok=True)
        context.output_file.write_text(f"{self.label}:{context.role_id}:{prompt}", encoding="utf-8")


class ExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.output = self.root / "roles" / "01-role" / "output.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def context(
        self,
        role_id: str = "thesis-style-editor",
        *,
        is_evaluator: bool = False,
        is_verifier: bool = False,
        is_finalizer: bool = False,
    ) -> RoleExecutionContext:
        return RoleExecutionContext(
            workflow_id="workflow-1",
            role_run_id=f"01-{role_id}",
            role_id=role_id,
            work_id="demo",
            lane="thesis",
            action="style-pass",
            sandbox_dir=self.root,
            output_file=self.output,
            use_search=False,
            model=None,
            timeout_seconds=30,
            is_evaluator=is_evaluator,
            is_verifier=is_verifier,
            is_finalizer=is_finalizer,
        )

    def test_router_uses_default_executor_for_ordinary_role(self) -> None:
        default = RecordingExecutor("default")
        evaluator = RecordingExecutor("evaluator")
        verifier = RecordingExecutor("verifier")
        router = ExecutorRouter(default_executor=default, evaluator_executor=evaluator, verifier_executor=verifier)

        router.execute(self.context(), "prompt")

        self.assertEqual(len(default.calls), 1)
        self.assertEqual(len(evaluator.calls), 0)
        self.assertEqual(len(verifier.calls), 0)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "default:thesis-style-editor:prompt")

    def test_router_routes_evaluator_independently(self) -> None:
        default = RecordingExecutor("default")
        evaluator = RecordingExecutor("evaluator")
        router = ExecutorRouter(default_executor=default, evaluator_executor=evaluator)

        router.execute(
            self.context("thesis-submission-evaluator", is_evaluator=True),
            "evaluate",
        )

        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(evaluator.calls), 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "evaluator:thesis-submission-evaluator:evaluate")

    def test_router_routes_verifier_independently(self) -> None:
        default = RecordingExecutor("default")
        verifier = RecordingExecutor("verifier")
        router = ExecutorRouter(default_executor=default, verifier_executor=verifier)

        router.execute(
            self.context("thesis-source-verifier", is_verifier=True),
            "verify",
        )

        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "verifier:thesis-source-verifier:verify")

    def test_unset_specific_routes_inherit_default(self) -> None:
        default = RecordingExecutor("default")
        router = ExecutorRouter(default_executor=default)

        router.execute(
            self.context("academic-submission-evaluator", is_evaluator=True),
            "evaluate",
        )

        self.assertEqual(len(default.calls), 1)
        self.assertEqual(self.output.read_text(encoding="utf-8"), "default:academic-submission-evaluator:evaluate")

    def test_callable_role_executor_preserves_legacy_signature(self) -> None:
        calls: list[tuple[Path, str, Path, bool, str | None]] = []

        def legacy(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            calls.append((sandbox, prompt, output, use_search, model))
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text("legacy-output", encoding="utf-8")

        executor = CallableRoleExecutor(legacy)
        context = self.context()

        executor.execute(context, "legacy-prompt")

        self.assertEqual(calls, [(self.root, "legacy-prompt", self.output, False, None)])
        self.assertEqual(self.output.read_text(encoding="utf-8"), "legacy-output")

    def test_stub_api_executor_requires_output_strategy(self) -> None:
        executor = StubApiExecutor()

        with self.assertRaises(ExecutorUnavailableError):
            executor.execute(self.context("thesis-submission-evaluator", is_evaluator=True), "prompt")

    def test_stub_api_executor_uses_output_strategy_when_supplied(self) -> None:
        def strategy(context: RoleExecutionContext, prompt: str) -> None:
            context.output_file.parent.mkdir(parents=True, exist_ok=True)
            context.output_file.write_text(f"stub:{context.role_id}:{prompt}", encoding="utf-8")

        executor = StubApiExecutor(output_strategy=strategy)

        executor.execute(self.context("thesis-submission-evaluator", is_evaluator=True), "prompt")

        self.assertEqual(self.output.read_text(encoding="utf-8"), "stub:thesis-submission-evaluator:prompt")

    def test_build_router_reads_environment_routes(self) -> None:
        default = RecordingExecutor("default")
        stub = RecordingExecutor("stub")
        environ = {
            "ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "codex-cli",
            "ACADEMIC_ENGINE_EVALUATOR_EXECUTOR": "stub-api",
            "ACADEMIC_ENGINE_VERIFIER_EXECUTOR": "stub-api",
        }

        router = build_executor_router(
            environ=environ,
            registry={"codex-cli": default, "stub-api": stub},
        )
        router.execute(self.context("academic-submission-evaluator", is_evaluator=True), "evaluate")
        router.execute(self.context("academic-source-verifier", is_verifier=True), "verify")

        self.assertEqual(len(default.calls), 0)
        self.assertEqual(
            [call[0].role_id for call in stub.calls],
            ["academic-submission-evaluator", "academic-source-verifier"],
        )

    def test_unknown_explicit_executor_fails_without_default_fallback(self) -> None:
        default = RecordingExecutor("default")
        environ = {
            "ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "codex-cli",
            "ACADEMIC_ENGINE_EVALUATOR_EXECUTOR": "missing-executor",
        }
        router = build_executor_router(environ=environ, registry={"codex-cli": default})

        with self.assertRaises(ExecutorUnavailableError):
            router.execute(self.context("academic-submission-evaluator", is_evaluator=True), "evaluate")

        self.assertEqual(len(default.calls), 0)

    def test_empty_environment_uses_codex_cli_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            router = build_executor_router({})

        self.assertIsInstance(router, ExecutorRouter)
        self.assertIsInstance(router.default_executor, CodexCliExecutor)


if __name__ == "__main__":
    unittest.main()
