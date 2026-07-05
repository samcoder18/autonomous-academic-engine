# OpenRouter Provider Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deployment-ready OpenRouter route for evaluator and verifier roles while keeping Codex CLI as the default executor.

**Architecture:** Extend the existing `academic_engine.executors` boundary with a reusable OpenAI-compatible executor and an `openrouter` registry entry. `WorkflowEngine` remains the authority for prompts, retries, role-result validation, write scopes, gates, readiness, repairs, and promotion; the provider executor only sends a chat-completions request and writes the returned content to `output.md`.

**Tech Stack:** Python 3.11 standard library only, `urllib.request`, `json`, existing `unittest` test suite, existing CLI parser, no SDK and no new runtime dependencies.

---

## File Structure

- Modify `academic_engine/executors.py`
  - Add provider-specific exception types, stable blocker codes, a small standard-library OpenRouter HTTP client, `OpenAICompatibleExecutor`, OpenRouter factory helpers, router registration, route guardrails, and provider smoke helpers.
- Modify `academic_engine/workflow_engine.py`
  - Preserve current retry behavior, but map provider-specific executor failures to their provider blocker codes instead of collapsing them all into `executor-unavailable`.
- Modify `academic_engine/work_cli.py`
  - Add `provider-smoke openrouter` CLI command and safe output formatting.
- Modify `tests/test_executors.py`
  - Add deterministic tests for provider payloads, headers, response parsing, missing config, auth errors, HTTP errors, route selection, default-route guardrail, and smoke helpers.
- Modify `tests/test_workflow_engine.py`
  - Add workflow-level coverage proving provider blocker codes survive normal role execution handling.
- Modify `tests/test_academic_engine.py`
  - Add CLI smoke success and failure tests.
- Modify `README.md`
  - Add a short OpenRouter operator recipe near the existing source connector/live-mode section.

---

### Task 1: Provider Client And Executor

**Files:**
- Modify: `academic_engine/executors.py`
- Modify: `tests/test_executors.py`

- [ ] **Step 1: Add failing provider client and executor tests**

Append this test helper and test class to `tests/test_executors.py`, before the `if __name__ == "__main__":` block:

```python
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
        transport = FakeOpenRouterTransport(body=b'{"choices":[{"message":{"content":"```role-result/v1\\n{}\\n```"}}]}')
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
```

Also extend the imports at the top of `tests/test_executors.py`:

```python
import json
```

and add these names to the `academic_engine.executors` import block:

```python
OpenRouterChatClient,
ProviderExecutionError,
build_openrouter_executor,
```

- [ ] **Step 2: Run the provider tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_executors.py -q
```

Expected: FAIL with import errors for `OpenRouterChatClient`, `ProviderExecutionError`, and `build_openrouter_executor`.

- [ ] **Step 3: Implement provider errors, client, and executor**

Modify `academic_engine/executors.py`.

Add these imports:

```python
import json
from dataclasses import dataclass
from urllib import error as urlerror
from urllib import request as urlrequest
```

Replace the existing dataclass import line with the combined import above if needed. Keep `from pathlib import Path` and existing typing imports.

Add these definitions after `OutputStrategy`:

```python
HttpTransport = Callable[[urlrequest.Request, float], tuple[int, bytes]]


class ProviderExecutionError(ExecutorUnavailableError):
    def __init__(self, blocker_code: str, message: str):
        super().__init__(message)
        self.blocker_code = blocker_code


def _urllib_transport(request: urlrequest.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", response.getcode())
            return int(status), response.read()
    except urlerror.HTTPError as exc:
        return int(exc.code), exc.read()
```

Add these classes after `StubApiExecutor`:

```python
class OpenRouterChatClient:
    def __init__(
        self,
        *,
        base_url: str = "https://openrouter.ai/api/v1",
        transport: HttpTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.transport = transport or _urllib_transport

    def complete(
        self,
        *,
        prompt: str,
        model: str,
        api_key: str,
        timeout_seconds: int,
        http_referer: str | None = None,
        app_title: str | None = None,
    ) -> str:
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if http_referer:
            headers["HTTP-Referer"] = http_referer
        if app_title:
            headers["X-OpenRouter-Title"] = app_title
        request = urlrequest.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            status, body = self.transport(request, float(timeout_seconds))
        except (OSError, TimeoutError, urlerror.URLError) as exc:
            raise ProviderExecutionError("provider-http-failed", f"openrouter request failed: {exc}") from exc
        if status in {401, 403}:
            raise ProviderExecutionError("provider-auth-failed", f"openrouter authentication failed with HTTP {status}")
        if status >= 400:
            raise ProviderExecutionError("provider-http-failed", f"openrouter request failed with HTTP {status}")
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderExecutionError("provider-response-invalid", "openrouter returned invalid JSON") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderExecutionError(
                "provider-response-invalid",
                "openrouter response lacks choices[0].message.content",
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise ProviderExecutionError("provider-response-invalid", "openrouter response content is empty")
        return content


class OpenAICompatibleExecutor:
    def __init__(
        self,
        *,
        provider_id: str,
        client: OpenRouterChatClient,
        api_key_env: str,
        model_env: str,
        environ: Mapping[str, str] | None = None,
        http_referer_env: str | None = None,
        app_title_env: str | None = None,
    ):
        self.provider_id = provider_id
        self.client = client
        self.api_key_env = api_key_env
        self.model_env = model_env
        self.environ = environ if environ is not None else os.environ
        self.http_referer_env = http_referer_env
        self.app_title_env = app_title_env

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        api_key = _clean_env_value(self.environ.get(self.api_key_env))
        model = _clean_env_value(self.environ.get(self.model_env))
        if api_key is None or model is None:
            raise ProviderExecutionError(
                "provider-config-missing",
                f"{self.provider_id} requires {self.api_key_env} and {self.model_env}",
            )
        content = self.client.complete(
            prompt=prompt,
            model=model,
            api_key=api_key,
            timeout_seconds=context.timeout_seconds,
            http_referer=_clean_env_value(self.environ.get(self.http_referer_env or "")),
            app_title=_clean_env_value(self.environ.get(self.app_title_env or "")),
        )
        context.output_file.parent.mkdir(parents=True, exist_ok=True)
        context.output_file.write_text(content, encoding="utf-8")
```

Add these helpers near `_default_registry`:

```python
def build_openrouter_executor(
    *,
    environ: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> OpenAICompatibleExecutor:
    env = environ if environ is not None else os.environ
    return OpenAICompatibleExecutor(
        provider_id="openrouter",
        client=OpenRouterChatClient(transport=transport),
        api_key_env="OPENROUTER_API_KEY",
        model_env="ACADEMIC_ENGINE_OPENROUTER_MODEL",
        environ=env,
        http_referer_env="ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER",
        app_title_env="ACADEMIC_ENGINE_OPENROUTER_APP_TITLE",
    )
```

Add this helper near `_clean_executor_id`:

```python
def _clean_env_value(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None
```

- [ ] **Step 4: Run provider tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_executors.py -q
```

Expected: PASS for the new provider tests and existing executor tests.

- [ ] **Step 5: Commit provider client and executor**

```bash
git add academic_engine/executors.py tests/test_executors.py
git commit -m "feat: add openrouter provider executor"
```

---

### Task 2: Router Registration And Default Route Guardrail

**Files:**
- Modify: `academic_engine/executors.py`
- Modify: `tests/test_executors.py`

- [ ] **Step 1: Add failing router tests for `openrouter`**

Append these methods to `ExecutorTests` in `tests/test_executors.py`:

```python
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
```

Add this import to the `academic_engine.executors` import block:

```python
OpenAICompatibleExecutor,
```

- [ ] **Step 2: Run router tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_executors.py::ExecutorTests::test_build_router_registers_openrouter_for_evaluator_and_verifier_routes tests/test_executors.py::ExecutorTests::test_openrouter_default_route_fails_closed_with_provider_code -q
```

Expected: FAIL because `openrouter` is not in the default registry and the default-route guardrail is not implemented.

- [ ] **Step 3: Implement registry and guardrail**

In `academic_engine/executors.py`, add this class after `UnavailableExecutor`:

```python
class ForbiddenProviderRouteExecutor:
    def __init__(self, executor_id: str, route_name: str):
        self.executor_id = executor_id
        self.route_name = route_name

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        raise ProviderExecutionError(
            "provider-route-forbidden",
            f"executor `{self.executor_id}` is not allowed for {self.route_name} route in this slice",
        )
```

Change `_default_registry` to include OpenRouter:

```python
def _default_registry(environ: Mapping[str, str]) -> dict[str, RoleExecutorProtocol]:
    return {
        "codex-cli": CodexCliExecutor(environ=environ),
        "stub-api": StubApiExecutor(),
        "openrouter": build_openrouter_executor(environ=environ),
    }
```

Change the `build_executor_router` return block to pass route names:

```python
    return ExecutorRouter(
        default_executor=_executor_for(default_id, available, route_name="default"),
        evaluator_executor=_executor_for(evaluator_id, available, route_name="evaluator") if evaluator_id else None,
        verifier_executor=_executor_for(verifier_id, available, route_name="verifier") if verifier_id else None,
    )
```

Replace `_executor_for` with:

```python
def _executor_for(
    executor_id: str,
    registry: Mapping[str, RoleExecutorProtocol],
    *,
    route_name: str,
) -> RoleExecutorProtocol:
    if route_name == "default" and executor_id == "openrouter":
        return ForbiddenProviderRouteExecutor(executor_id, route_name)
    return registry.get(executor_id) or UnavailableExecutor(executor_id)
```

- [ ] **Step 4: Run router tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_executors.py -q
```

Expected: all executor tests pass.

- [ ] **Step 5: Commit router registration**

```bash
git add academic_engine/executors.py tests/test_executors.py
git commit -m "feat: route evaluator verifier roles to openrouter"
```

---

### Task 3: Workflow Provider Blocker Mapping

**Files:**
- Modify: `academic_engine/workflow_engine.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Add failing workflow test for provider blocker code**

Add `ProviderExecutionError` to the import from `academic_engine.executors` in `tests/test_workflow_engine.py`:

```python
from academic_engine.executors import ExecutorUnavailableError, ProviderExecutionError, RoleExecutionContext
```

Add this test method to `WorkflowEngineTests` near `test_executor_unavailable_fails_closed_with_stable_blocker`:

```python
    def test_provider_execution_error_records_provider_blocker_code(self) -> None:
        class ProviderFailureRouter:
            def __init__(self) -> None:
                self.calls = 0

            def execute(self, context: RoleExecutionContext, prompt: str) -> None:
                self.calls += 1
                raise ProviderExecutionError("provider-auth-failed", "openrouter authentication failed with HTTP 401")

        router = ProviderFailureRouter()

        result = WorkflowEngine(self.root, executor_router=router).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=False,
            model=None,
        )

        self.assertEqual(router.calls, 1)
        self.assertEqual(result.execution_status, "failed")
        self.assertTrue(any(item["code"] == "provider-auth-failed" for item in result.blockers))
        self.assertFalse(any(item["code"] == "executor-unavailable" for item in result.blockers))
```

- [ ] **Step 2: Run workflow test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_workflow_engine.py::WorkflowEngineTests::test_provider_execution_error_records_provider_blocker_code -q
```

Expected: FAIL because provider errors are still recorded as `executor-unavailable`.

- [ ] **Step 3: Map provider blocker codes in `WorkflowEngine`**

In `academic_engine/workflow_engine.py`, find this block inside `_run_role`:

```python
            if isinstance(error, ExecutorUnavailableError):
                role.blockers.append(_runtime_blocker("executor-unavailable", f"{node.role_id}: {error}"))
            else:
                role.blockers.append(_runtime_blocker("role-execution-failed", f"{node.role_id}: {error}"))
```

Replace it with:

```python
            if isinstance(error, ExecutorUnavailableError):
                blocker_code = getattr(error, "blocker_code", "executor-unavailable")
                role.blockers.append(_runtime_blocker(str(blocker_code), f"{node.role_id}: {error}"))
            else:
                role.blockers.append(_runtime_blocker("role-execution-failed", f"{node.role_id}: {error}"))
```

- [ ] **Step 4: Run workflow tests**

Run:

```bash
python3 -m pytest tests/test_workflow_engine.py::WorkflowEngineTests::test_provider_execution_error_records_provider_blocker_code tests/test_workflow_engine.py::WorkflowEngineTests::test_executor_unavailable_fails_closed_with_stable_blocker -q
```

Expected: both tests pass.

- [ ] **Step 5: Commit provider blocker mapping**

```bash
git add academic_engine/workflow_engine.py tests/test_workflow_engine.py
git commit -m "fix: preserve provider blocker codes"
```

---

### Task 4: Provider Smoke Helpers And CLI

**Files:**
- Modify: `academic_engine/executors.py`
- Modify: `academic_engine/work_cli.py`
- Modify: `tests/test_executors.py`
- Modify: `tests/test_academic_engine.py`

- [ ] **Step 1: Add failing smoke helper tests**

Add these names to the `academic_engine.executors` import block in `tests/test_executors.py`:

```python
ProviderSmokeResult,
run_provider_smoke,
```

Add these methods to `OpenRouterProviderTests`:

```python
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
```

- [ ] **Step 2: Add failing CLI tests**

Add these names to the `academic_engine.executors` import in `tests/test_academic_engine.py`:

```python
ProviderExecutionError,
ProviderSmokeResult,
```

Change:

```python
from academic_engine.executors import CallableRoleExecutor, ExecutorRouter
```

to:

```python
from academic_engine.executors import CallableRoleExecutor, ExecutorRouter, ProviderExecutionError, ProviderSmokeResult
```

Add these methods to `WorkCliTests` in `tests/test_academic_engine.py`:

```python
    def test_provider_smoke_cli_prints_safe_success_summary(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        result = ProviderSmokeResult(
            provider_id="openrouter",
            model="openrouter/test-model",
            content_length=17,
            preview="provider-smoke-ok",
        )

        with patch.object(work_cli_module, "run_provider_smoke", return_value=result):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["provider-smoke", "openrouter"])

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("[provider-smoke] provider: openrouter", stdout.getvalue())
        self.assertIn("[provider-smoke] model: openrouter/test-model", stdout.getvalue())
        self.assertIn("[provider-smoke] response_chars: 17", stdout.getvalue())
        self.assertIn("[provider-smoke] preview: provider-smoke-ok", stdout.getvalue())
        self.assertNotIn("OPENROUTER_API_KEY", stdout.getvalue())

    def test_provider_smoke_cli_reports_provider_error_without_secret(self) -> None:
        stdout = StringIO()
        stderr = StringIO()

        with patch.object(
            work_cli_module,
            "run_provider_smoke",
            side_effect=ProviderExecutionError("provider-auth-failed", "openrouter authentication failed with HTTP 401"),
        ):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["provider-smoke", "openrouter"])

        self.assertEqual(code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("provider-auth-failed", stderr.getvalue())
        self.assertIn("openrouter authentication failed", stderr.getvalue())
        self.assertNotIn("secret", stderr.getvalue())
```

- [ ] **Step 3: Run smoke tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_executors.py::OpenRouterProviderTests::test_provider_smoke_requires_explicit_live_flag tests/test_executors.py::OpenRouterProviderTests::test_provider_smoke_runs_one_safe_prompt_when_enabled tests/test_academic_engine.py::WorkCliTests::test_provider_smoke_cli_prints_safe_success_summary tests/test_academic_engine.py::WorkCliTests::test_provider_smoke_cli_reports_provider_error_without_secret -q
```

Expected: FAIL because smoke helpers and CLI command do not exist.

- [ ] **Step 4: Implement smoke helpers**

In `academic_engine/executors.py`, add `tempfile` to imports:

```python
import tempfile
```

Add this dataclass after `ProviderExecutionError`:

```python
@dataclass(frozen=True)
class ProviderSmokeResult:
    provider_id: str
    model: str
    content_length: int
    preview: str
```

Add this function near `build_openrouter_executor`:

```python
def run_provider_smoke(
    provider_id: str,
    *,
    environ: Mapping[str, str] | None = None,
    transport: HttpTransport | None = None,
) -> ProviderSmokeResult:
    if provider_id != "openrouter":
        raise ProviderExecutionError("provider-config-missing", f"provider `{provider_id}` is not supported")
    env = environ if environ is not None else os.environ
    if _clean_env_value(env.get("ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST")) != "1":
        raise ProviderExecutionError(
            "provider-config-missing",
            "Set ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1 to run provider smoke.",
        )
    model = _clean_env_value(env.get("ACADEMIC_ENGINE_OPENROUTER_MODEL"))
    if model is None:
        raise ProviderExecutionError(
            "provider-config-missing",
            "openrouter requires ACADEMIC_ENGINE_OPENROUTER_MODEL",
        )
    with tempfile.TemporaryDirectory() as tempdir:
        root = Path(tempdir)
        output_file = root / "output.md"
        context = RoleExecutionContext(
            workflow_id="provider-smoke-openrouter",
            role_run_id="01-provider-smoke-openrouter",
            role_id="provider-smoke-openrouter",
            work_id="provider-smoke",
            lane="provider",
            action="smoke",
            sandbox_dir=root,
            output_file=output_file,
            use_search=False,
            model=None,
            timeout_seconds=30,
        )
        executor = build_openrouter_executor(environ=env, transport=transport)
        executor.execute(context, "Respond with exactly: provider-smoke-ok")
        content = output_file.read_text(encoding="utf-8")
    preview = " ".join(content.split())[:120]
    return ProviderSmokeResult(
        provider_id="openrouter",
        model=model,
        content_length=len(content),
        preview=preview,
    )
```

- [ ] **Step 5: Implement CLI command**

In `academic_engine/work_cli.py`, change:

```python
from .executors import build_executor_router
```

to:

```python
from .executors import ProviderExecutionError, build_executor_router, run_provider_smoke
```

Add the parser near `work-status`:

```python
    provider_smoke_parser = subparsers.add_parser("provider-smoke")
    provider_smoke_parser.add_argument("provider", choices=("openrouter",))
```

Add the handler in `main` before `runtime-index`:

```python
        if args.command == "provider-smoke":
            return provider_smoke_cli(args.provider)
```

Add this function near `work_status` or before `runtime_index_cli`:

```python
def provider_smoke_cli(provider: str) -> int:
    try:
        result = run_provider_smoke(provider)
    except ProviderExecutionError as exc:
        print(f"[provider-smoke] {exc.blocker_code}: {exc}", file=sys.stderr)
        return 1
    print(f"[provider-smoke] provider: {result.provider_id}")
    print(f"[provider-smoke] model: {result.model}")
    print(f"[provider-smoke] response_chars: {result.content_length}")
    print(f"[provider-smoke] preview: {result.preview}")
    return 0
```

- [ ] **Step 6: Run smoke tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_executors.py::OpenRouterProviderTests::test_provider_smoke_requires_explicit_live_flag tests/test_executors.py::OpenRouterProviderTests::test_provider_smoke_runs_one_safe_prompt_when_enabled tests/test_academic_engine.py::WorkCliTests::test_provider_smoke_cli_prints_safe_success_summary tests/test_academic_engine.py::WorkCliTests::test_provider_smoke_cli_reports_provider_error_without_secret -q
```

Expected: all four tests pass.

- [ ] **Step 7: Commit smoke helpers and CLI**

```bash
git add academic_engine/executors.py academic_engine/work_cli.py tests/test_executors.py tests/test_academic_engine.py
git commit -m "feat: add openrouter provider smoke command"
```

---

### Task 5: README Operator Recipe

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add OpenRouter README section**

Add this section after the existing `### Source connectors (stub/live)` section in `README.md`:

````markdown
### OpenRouter provider route

Codex CLI remains the default executor. OpenRouter can be enabled only for evaluator and verifier roles in this slice:

```bash
export OPENROUTER_API_KEY="sk-or-v1-redacted"
export ACADEMIC_ENGINE_OPENROUTER_MODEL="provider/model-slug"
export ACADEMIC_ENGINE_EVALUATOR_EXECUTOR=openrouter
export ACADEMIC_ENGINE_VERIFIER_EXECUTOR=openrouter
```

Optional deploy attribution:

```bash
export ACADEMIC_ENGINE_OPENROUTER_HTTP_REFERER="https://your-deploy-domain.example"
export ACADEMIC_ENGINE_OPENROUTER_APP_TITLE="Academic Engine"
```

Run an explicit live smoke check before using the route:

```bash
export ACADEMIC_ENGINE_OPENROUTER_LIVE_TEST=1
python3 -m academic_engine.work_cli provider-smoke openrouter
```

Ordinary CI and unit tests do not call OpenRouter. `ACADEMIC_ENGINE_DEFAULT_EXECUTOR=openrouter` is intentionally rejected until a safe file-write bridge exists for writer/finalizer roles.
````

- [ ] **Step 2: Inspect Markdown rendering around code fences**

Run:

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("README.md").read_text(encoding="utf-8")
needle = "### OpenRouter provider route"
assert needle in text
start = text.index(needle)
print(text[start:start + 1200])
PY
```

Expected: the printed README section contains balanced fenced code blocks and no real API key.

- [ ] **Step 3: Commit README update**

```bash
git add README.md
git commit -m "docs: document openrouter provider route"
```

---

### Task 6: Focused Verification And Formatting

**Files:**
- Verify only.

- [ ] **Step 1: Run focused provider and workflow tests**

Run:

```bash
python3 -m pytest tests/test_executors.py tests/test_workflow_engine.py::WorkflowEngineTests::test_provider_execution_error_records_provider_blocker_code tests/test_academic_engine.py::WorkCliTests::test_provider_smoke_cli_prints_safe_success_summary tests/test_academic_engine.py::WorkCliTests::test_provider_smoke_cli_reports_provider_error_without_secret -q
```

Expected: all selected tests pass.

- [ ] **Step 2: Run repository lint and format checks**

Run:

```bash
python3 -m ruff check academic_engine tests
python3 -m ruff format --check academic_engine tests
```

Expected: both commands pass.

- [ ] **Step 3: Run the full unit gate**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: the full test suite passes without network access.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: no uncommitted changes if each implementation task was committed. If formatting changed files during verification, commit those formatting-only changes with:

```bash
git add academic_engine tests README.md
git commit -m "style: format openrouter provider integration"
```

---

## Implementation Notes

- Do not run a live OpenRouter request during ordinary tests.
- Do not print `OPENROUTER_API_KEY` or the Bearer value.
- Do not let `context.model` override `ACADEMIC_ENGINE_OPENROUTER_MODEL` in this slice.
- Do not add `httpx`, OpenAI SDK, or any other runtime HTTP dependency.
- Do not move `role-result/v1` validation into the executor.
- Do not allow `openrouter` on the default executor route.
- Keep provider failures as `ExecutorUnavailableError` subclasses so the existing role retry boundary remains intact.
