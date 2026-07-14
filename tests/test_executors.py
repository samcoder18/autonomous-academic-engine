from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_engine.executors import (
    OPENROUTER_ROLE_POLICY,
    SUPPORTED_ROLE_IDS,
    CallableRoleExecutor,
    CodexCliExecutor,
    ExecutorRouter,
    ExecutorUnavailableError,
    OpenAICompatibleExecutor,
    OpenRouterChatClient,
    ProviderExecutionError,
    ProviderSmokeResult,
    RoleExecutionContext,
    StubApiExecutor,
    build_executor_router,
    build_openrouter_executor,
    run_provider_smoke,
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

    def test_router_describes_selected_executor_route(self) -> None:
        default = RecordingExecutor("default")
        evaluator = RecordingExecutor("evaluator")
        verifier = RecordingExecutor("verifier")
        router = ExecutorRouter(
            default_executor=default,
            evaluator_executor=evaluator,
            verifier_executor=verifier,
            default_executor_id="codex-cli",
            evaluator_executor_id="openrouter",
            verifier_executor_id="openrouter",
        )

        self.assertEqual(
            router.describe_selection(self.context()).to_dict(),
            {"route_name": "default", "executor_id": "codex-cli"},
        )
        self.assertEqual(
            router.describe_selection(self.context("academic-submission-evaluator", is_evaluator=True)).to_dict(),
            {
                "route_name": "evaluator",
                "executor_id": "openrouter",
                "execution_mode": "read-only",
            },
        )
        self.assertEqual(
            router.describe_selection(self.context("academic-source-verifier", is_verifier=True)).to_dict(),
            {
                "route_name": "verifier",
                "executor_id": "openrouter",
                "execution_mode": "read-only",
            },
        )

    def test_openrouter_evaluator_route_runs_academic_evaluator(self) -> None:
        default = RecordingExecutor("default")
        evaluator = RecordingExecutor("evaluator")
        router = ExecutorRouter(
            default_executor=default,
            evaluator_executor=evaluator,
            default_executor_id="codex-cli",
            evaluator_executor_id="openrouter",
        )

        router.execute(
            self.context("academic-submission-evaluator", is_evaluator=True),
            "evaluate",
        )

        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(evaluator.calls), 1)

    def test_router_runs_approved_write_plan_role_through_explicit_role_policy(self) -> None:
        default = RecordingExecutor("default")
        writer = RecordingExecutor("writer")
        router = ExecutorRouter(
            default_executor=default,
            role_executors={"academic-intake": writer},
            role_executor_ids={"academic-intake": "openrouter"},
            role_policies={
                "academic-intake": {
                    "executor_id": "openrouter",
                    "execution_mode": "write-plan",
                }
            },
        )
        context = self.context("academic-intake")

        self.assertEqual(
            router.describe_selection(context).to_dict(),
            {
                "route_name": "role",
                "executor_id": "openrouter",
                "execution_mode": "write-plan",
            },
        )
        router.execute(context, "intake")

        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(writer.calls), 1)

    def test_router_rejects_unqualified_openrouter_role_without_fallback(self) -> None:
        default = RecordingExecutor("default")
        writer = RecordingExecutor("writer")
        router = ExecutorRouter(
            default_executor=default,
            role_executors={"academic-source-acquirer": writer},
            role_executor_ids={"academic-source-acquirer": "openrouter"},
            role_policies=OPENROUTER_ROLE_POLICY,
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(self.context("academic-source-acquirer"), "acquire")

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(writer.calls), 0)

    def test_router_rejects_invalid_openrouter_execution_mode_without_fallback(self) -> None:
        default = RecordingExecutor("default")
        writer = RecordingExecutor("writer")
        router = ExecutorRouter(
            default_executor=default,
            role_executors={"academic-intake": writer},
            role_executor_ids={"academic-intake": "openrouter"},
            role_policies={
                "academic-intake": {
                    "executor_id": "openrouter",
                    "execution_mode": "unsafe-mode",
                }
            },
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(self.context("academic-intake"), "intake")

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(writer.calls), 0)

    def test_openrouter_evaluator_route_rejects_thesis_role_without_fallback(self) -> None:
        default = RecordingExecutor("default")
        evaluator = RecordingExecutor("evaluator")
        router = ExecutorRouter(
            default_executor=default,
            evaluator_executor=evaluator,
            default_executor_id="codex-cli",
            evaluator_executor_id="openrouter",
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(
                self.context("thesis-submission-evaluator", is_evaluator=True),
                "evaluate",
            )

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(evaluator.calls), 0)

    def test_openrouter_verifier_route_rejects_thesis_role_without_fallback(self) -> None:
        default = RecordingExecutor("default")
        verifier = RecordingExecutor("verifier")
        router = ExecutorRouter(
            default_executor=default,
            verifier_executor=verifier,
            default_executor_id="codex-cli",
            verifier_executor_id="openrouter",
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(
                self.context("thesis-source-verifier", is_verifier=True),
                "verify",
            )

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(verifier.calls), 0)

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

    def test_build_router_registers_openrouter_for_evaluator_and_verifier_routes(self) -> None:
        environ = {
            "OPENROUTER_API_KEY": "secret-key",
            "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
            "ACADEMIC_ENGINE_EVALUATOR_EXECUTOR": "openrouter",
            "ACADEMIC_ENGINE_VERIFIER_EXECUTOR": "openrouter",
        }

        router = build_executor_router(environ=environ)

        self.assertIsInstance(router.evaluator_executor, OpenAICompatibleExecutor)
        self.assertIsInstance(router.verifier_executor, OpenAICompatibleExecutor)
        self.assertIsInstance(router.default_executor, CodexCliExecutor)

    def test_build_router_routes_qualified_academic_intake_via_explicit_env(self) -> None:
        default = RecordingExecutor("default")
        openrouter = RecordingExecutor("openrouter")
        environ = {
            "ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "codex-cli",
            "ACADEMIC_ENGINE_ROLE_EXECUTOR_ACADEMIC_INTAKE": "openrouter",
        }
        router = build_executor_router(
            environ=environ,
            registry={"codex-cli": default, "openrouter": openrouter},
        )
        context = self.context("academic-intake")

        self.assertEqual(
            router.describe_selection(context).to_dict(),
            {
                "route_name": "role",
                "executor_id": "openrouter",
                "execution_mode": "write-plan",
            },
        )
        router.execute(context, "intake")

        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(openrouter.calls), 1)

    def test_build_router_rejects_academic_intake_without_policy_before_executor_invocation(self) -> None:
        default = RecordingExecutor("default")
        openrouter = RecordingExecutor("openrouter")
        environ = {
            "ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "codex-cli",
            "ACADEMIC_ENGINE_ROLE_EXECUTOR_ACADEMIC_INTAKE": "openrouter",
        }
        router = build_executor_router(
            environ=environ,
            registry={"codex-cli": default, "openrouter": openrouter},
            role_policies={
                role_id: policy
                for role_id, policy in OPENROUTER_ROLE_POLICY.items()
                if role_id != "academic-intake"
            },
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(self.context("academic-intake"), "intake")

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(len(default.calls), 0)
        self.assertEqual(len(openrouter.calls), 0)

    def test_openrouter_default_route_fails_closed_with_provider_code(self) -> None:
        environ = {
            "OPENROUTER_API_KEY": "secret-key",
            "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
            "ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "openrouter",
        }
        router = build_executor_router(environ=environ)

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(self.context(), "prompt")

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")

    def test_guarded_openrouter_default_requires_complete_policy_and_configured_model(self) -> None:
        codex = RecordingExecutor("codex")
        openrouter = RecordingExecutor("openrouter")
        complete_policy = _complete_openrouter_policy()
        environ = {
            "ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "openrouter",
            "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
        }
        router = build_executor_router(
            environ=environ,
            registry={"codex-cli": codex, "openrouter": openrouter},
            role_policies=complete_policy,
        )
        context = self.context("academic-intake")

        self.assertEqual(
            router.describe_selection(context).to_dict(),
            {
                "route_name": "default",
                "executor_id": "openrouter",
                "execution_mode": "write-plan",
            },
        )
        router.execute(context, "default")

        self.assertEqual(len(codex.calls), 0)
        self.assertEqual(len(openrouter.calls), 1)

    def test_guarded_openrouter_default_rejects_missing_model_before_executor_invocation(self) -> None:
        codex = RecordingExecutor("codex")
        openrouter = RecordingExecutor("openrouter")
        router = build_executor_router(
            environ={"ACADEMIC_ENGINE_DEFAULT_EXECUTOR": "openrouter"},
            registry={"codex-cli": codex, "openrouter": openrouter},
            role_policies=_complete_openrouter_policy(),
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            router.execute(self.context("academic-intake"), "default")

        self.assertEqual(caught.exception.blocker_code, "provider-route-forbidden")
        self.assertEqual(len(codex.calls), 0)
        self.assertEqual(len(openrouter.calls), 0)


class FakeOpenRouterTransport:
    def __init__(self, *, status: int = 200, body: bytes | None = None, exc: Exception | None = None):
        self.status = status
        self.body = body if body is not None else b'{"choices":[{"message":{"content":"provider-output"}}]}'
        self.exc = exc
        self.requests: list[tuple[object, float]] = []

    def __call__(self, request: object, timeout: float) -> tuple[int, bytes]:
        self.requests.append((request, timeout))
        if self.exc is not None:
            raise self.exc
        return self.status, self.body


class OpenRouterProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.output = self.root / "roles" / "01-provider" / "output.md"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def context(self) -> RoleExecutionContext:
        return RoleExecutionContext(
            workflow_id="workflow-provider",
            role_run_id="01-academic-submission-evaluator",
            role_id="academic-submission-evaluator",
            work_id="demo",
            lane="article",
            action="review",
            sandbox_dir=self.root,
            output_file=self.output,
            use_search=False,
            model=None,
            timeout_seconds=17,
            is_evaluator=True,
        )

    def test_provider_smoke_requires_explicit_live_flag(self) -> None:
        with self.assertRaises(ProviderExecutionError) as caught:
            run_provider_smoke(
                "openrouter",
                environ={
                    "OPENROUTER_API_KEY": "secret-key",
                    "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
                },
                transport=FakeOpenRouterTransport(),
            )

        self.assertEqual(caught.exception.blocker_code, "provider-config-missing")
        self.assertIn("ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1", str(caught.exception))

    def test_provider_smoke_runs_one_safe_prompt_when_enabled(self) -> None:
        transport = FakeOpenRouterTransport(body=b'{"choices":[{"message":{"content":"provider-smoke-ok"}}]}')

        result = run_provider_smoke(
            "openrouter",
            environ={
                "OPENROUTER_API_KEY": "secret-key",
                "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
                "ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST": "1",
            },
            transport=transport,
        )

        self.assertEqual(
            result,
            ProviderSmokeResult(
                provider_id="openrouter",
                model="openrouter/test-model",
                content_length=len("provider-smoke-ok"),
                preview="provider-smoke-ok",
            ),
        )
        request, _ = transport.requests[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["messages"], [{"role": "user", "content": "Respond with exactly: provider-smoke-ok"}])

    def test_openrouter_client_builds_chat_completion_payload_and_headers(self) -> None:
        transport = FakeOpenRouterTransport()
        client = OpenRouterChatClient(transport=transport)

        content = client.complete(
            prompt="evaluate this",
            model="openrouter/test-model",
            api_key="secret-key",
            timeout_seconds=17,
            http_referer="https://deploy.example",
            app_title="Academic Engine",
        )

        self.assertEqual(content, "provider-output")
        request, timeout = transport.requests[0]
        self.assertEqual(timeout, 17)
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(
            payload,
            {
                "model": "openrouter/test-model",
                "messages": [{"role": "user", "content": "evaluate this"}],
                "stream": False,
            },
        )
        headers = dict(request.header_items())
        self.assertEqual(headers["Authorization"], "Bearer secret-key")
        self.assertEqual(headers["Content-type"], "application/json")
        self.assertEqual(headers["Accept"], "application/json")
        self.assertEqual(headers["Http-referer"], "https://deploy.example")
        self.assertEqual(headers["X-openrouter-title"], "Academic Engine")

    def test_openai_compatible_executor_requires_key_and_model(self) -> None:
        executor = build_openrouter_executor(environ={}, transport=FakeOpenRouterTransport())

        with self.assertRaises(ProviderExecutionError) as caught:
            executor.execute(self.context(), "prompt")

        self.assertEqual(caught.exception.blocker_code, "provider-config-missing")
        self.assertIn("OPENROUTER_API_KEY", str(caught.exception))
        self.assertIn("ACADEMIC_ENGINE_OPENROUTER_MODEL", str(caught.exception))

    def test_openai_compatible_executor_writes_model_content_to_output_file(self) -> None:
        transport = FakeOpenRouterTransport(
            body=b'{"choices":[{"message":{"content":"```role-result/v1\\n{}\\n```"}}]}'
        )
        executor = build_openrouter_executor(
            environ={
                "OPENROUTER_API_KEY": "secret-key",
                "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
            },
            transport=transport,
        )

        executor.execute(self.context(), "prompt")

        self.assertEqual(self.output.read_text(encoding="utf-8"), "```role-result/v1\n{}\n```")
        request, timeout = transport.requests[0]
        self.assertEqual(timeout, 17)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])

    def test_openrouter_auth_errors_use_auth_blocker(self) -> None:
        executor = build_openrouter_executor(
            environ={
                "OPENROUTER_API_KEY": "secret-key",
                "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
            },
            transport=FakeOpenRouterTransport(status=401, body=b'{"error":"bad key"}'),
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            executor.execute(self.context(), "prompt")

        self.assertEqual(caught.exception.blocker_code, "provider-auth-failed")
        self.assertNotIn("secret-key", str(caught.exception))

    def test_openrouter_http_errors_use_http_blocker(self) -> None:
        executor = build_openrouter_executor(
            environ={
                "OPENROUTER_API_KEY": "secret-key",
                "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
            },
            transport=FakeOpenRouterTransport(status=500, body=b'{"error":"temporary"}'),
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            executor.execute(self.context(), "prompt")

        self.assertEqual(caught.exception.blocker_code, "provider-http-failed")

    def test_openrouter_invalid_response_shape_uses_invalid_response_blocker(self) -> None:
        executor = build_openrouter_executor(
            environ={
                "OPENROUTER_API_KEY": "secret-key",
                "ACADEMIC_ENGINE_OPENROUTER_MODEL": "openrouter/test-model",
            },
            transport=FakeOpenRouterTransport(body=b'{"choices":[{"message":{}}]}'),
        )

        with self.assertRaises(ProviderExecutionError) as caught:
            executor.execute(self.context(), "prompt")

        self.assertEqual(caught.exception.blocker_code, "provider-response-invalid")


def _complete_openrouter_policy() -> dict[str, dict[str, str]]:
    return {
        role_id: {
            "executor_id": "openrouter",
            "execution_mode": "read-only"
            if role_id.endswith(("source-verifier", "submission-evaluator"))
            else "write-plan",
        }
        for role_id in SUPPORTED_ROLE_IDS
    }


if __name__ == "__main__":
    unittest.main()
