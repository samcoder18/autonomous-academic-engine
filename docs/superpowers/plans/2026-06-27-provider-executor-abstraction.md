# Provider Executor Abstraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed provider/executor routing layer while preserving Codex CLI as the default workflow executor.

**Architecture:** Add `academic_engine.executors` as the runtime adapter boundary, then route `WorkflowEngine` role execution through `RoleExecutionContext` and `ExecutorRouter`. Keep `WorkflowEngine` as the only authority for role-result validation, write-scope checks, gates, readiness, repairs, and promotion.

**Tech Stack:** Python 3.11 standard library, `unittest`, existing CLI/workflow modules, no new runtime dependencies.

---

## File Structure

- Create `academic_engine/executors.py`
  - Owns executor context, protocol, router, Codex CLI adapter, callable adapter for backward-compatible tests, stub API executor, unavailable executor, and environment-based router factory.
- Create `tests/test_executors.py`
  - Unit tests for router selection, explicit-route behavior, environment config, callable adapter, and stub fail-closed behavior.
- Modify `academic_engine/workflow_engine.py`
  - Replace direct callable execution with typed `RoleExecutionContext`.
  - Keep existing `role_executor=` constructor compatibility by wrapping it in `CallableRoleExecutor`.
  - Add `executor_router=` as the preferred constructor argument.
  - Convert `ExecutorUnavailableError` into an `executor-unavailable` runtime blocker.
- Modify `academic_engine/work_cli.py`
  - Import and use `build_executor_router()` in `_run_role_workflow`.
  - Remove the old direct `_run_codex` workflow path after tests are updated.
- Modify `tests/test_workflow_engine.py`
  - Add workflow integration tests for context delivery and executor-unavailable failure.
- Modify `tests/test_academic_engine.py`
  - Update tests that patch `_run_codex` so they patch `build_executor_router` with a `CallableRoleExecutor` router.
  - Keep existing fake Codex binary tests as default CLI behavior coverage.

---

### Task 1: Executor Module And Unit Tests

**Files:**
- Create: `academic_engine/executors.py`
- Create: `tests/test_executors.py`

- [ ] **Step 1: Write failing executor tests**

Create `tests/test_executors.py` with this content:

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from academic_engine.executors import (
    CallableRoleExecutor,
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
        self.assertEqual([call[0].role_id for call in stub.calls], ["academic-submission-evaluator", "academic-source-verifier"])

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
            router = build_executor_router()

        self.assertIsInstance(router, ExecutorRouter)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the failing executor tests**

Run:

```bash
python3 -m pytest tests/test_executors.py -q
```

Expected result:

```text
ERROR tests/test_executors.py
ModuleNotFoundError: No module named 'academic_engine.executors'
```

- [ ] **Step 3: Implement `academic_engine/executors.py`**

Create `academic_engine/executors.py` with this content:

```python
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .utils import resolve_executable

LegacyRoleExecutor = Callable[[Path, str, Path, bool, str | None], None]
OutputStrategy = Callable[["RoleExecutionContext", str], None]


class ExecutorUnavailableError(RuntimeError):
    """Raised when an explicitly selected executor cannot run a role."""


@dataclass(frozen=True)
class RoleExecutionContext:
    workflow_id: str
    role_run_id: str
    role_id: str
    work_id: str
    lane: str
    action: str
    sandbox_dir: Path
    output_file: Path
    use_search: bool
    model: str | None
    timeout_seconds: int
    is_evaluator: bool = False
    is_verifier: bool = False
    is_finalizer: bool = False


class RoleExecutorProtocol(Protocol):
    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        """Run one role and write the raw role output to context.output_file."""


class CallableRoleExecutor:
    def __init__(self, executor: LegacyRoleExecutor):
        self.executor = executor

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        self.executor(
            context.sandbox_dir,
            prompt,
            context.output_file,
            context.use_search,
            context.model,
        )


class CodexCliExecutor:
    def __init__(
        self,
        *,
        codex_bin: str | None = None,
        environ: Mapping[str, str] | None = None,
        runner=subprocess.run,
    ):
        self.codex_bin = codex_bin
        self.environ = environ if environ is not None else os.environ
        self.runner = runner

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        codex_bin = self._resolve_codex_bin()
        cmd = [codex_bin]
        if context.use_search:
            cmd.append("--search")
        cmd.extend(
            [
                "exec",
                "-C",
                str(context.sandbox_dir),
                "--skip-git-repo-check",
                "--full-auto",
                "-o",
                str(context.output_file),
            ]
        )
        chosen_model = context.model or self.environ.get("CODEX_MODEL")
        if chosen_model:
            cmd.extend(["-m", chosen_model])
        self.runner(
            cmd + ["-"],
            input=prompt,
            text=True,
            check=True,
            timeout=context.timeout_seconds,
        )

    def _resolve_codex_bin(self) -> str:
        configured = self.codex_bin or self.environ.get("CODEX_BIN")
        resolved = resolve_executable(
            configured,
            "codex",
            extra_candidates=("/Applications/Codex.app/Contents/Resources/codex",),
        )
        if resolved:
            return resolved
        requested = (configured or "codex").strip() or "codex"
        raise FileNotFoundError(requested)


class StubApiExecutor:
    def __init__(self, *, output_strategy: OutputStrategy | None = None):
        self.output_strategy = output_strategy

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        if self.output_strategy is None:
            raise ExecutorUnavailableError("stub-api executor has no output strategy configured")
        self.output_strategy(context, prompt)


class UnavailableExecutor:
    def __init__(self, executor_id: str):
        self.executor_id = executor_id

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        raise ExecutorUnavailableError(f"executor `{self.executor_id}` is not available")


@dataclass(frozen=True)
class ExecutorRouter:
    default_executor: RoleExecutorProtocol
    evaluator_executor: RoleExecutorProtocol | None = None
    verifier_executor: RoleExecutorProtocol | None = None

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        executor = self._select(context)
        executor.execute(context, prompt)

    def _select(self, context: RoleExecutionContext) -> RoleExecutorProtocol:
        if context.is_evaluator and self.evaluator_executor is not None:
            return self.evaluator_executor
        if context.is_verifier and self.verifier_executor is not None:
            return self.verifier_executor
        return self.default_executor


def build_executor_router(
    *,
    environ: Mapping[str, str] | None = None,
    registry: Mapping[str, RoleExecutorProtocol] | None = None,
) -> ExecutorRouter:
    env = environ if environ is not None else os.environ
    available = dict(registry) if registry is not None else _default_registry(env)

    default_id = _clean_executor_id(env.get("ACADEMIC_ENGINE_DEFAULT_EXECUTOR")) or "codex-cli"
    evaluator_id = _clean_executor_id(env.get("ACADEMIC_ENGINE_EVALUATOR_EXECUTOR"))
    verifier_id = _clean_executor_id(env.get("ACADEMIC_ENGINE_VERIFIER_EXECUTOR"))

    return ExecutorRouter(
        default_executor=_executor_for(default_id, available),
        evaluator_executor=_executor_for(evaluator_id, available) if evaluator_id else None,
        verifier_executor=_executor_for(verifier_id, available) if verifier_id else None,
    )


def _default_registry(environ: Mapping[str, str]) -> dict[str, RoleExecutorProtocol]:
    return {
        "codex-cli": CodexCliExecutor(environ=environ),
        "stub-api": StubApiExecutor(),
    }


def _executor_for(executor_id: str, registry: Mapping[str, RoleExecutorProtocol]) -> RoleExecutorProtocol:
    return registry.get(executor_id) or UnavailableExecutor(executor_id)


def _clean_executor_id(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None
```

- [ ] **Step 4: Run executor tests and fix import/lint errors**

Run:

```bash
python3 -m pytest tests/test_executors.py -q
```

Expected result:

```text
10 passed
```

- [ ] **Step 5: Commit executor module**

Run:

```bash
git add academic_engine/executors.py tests/test_executors.py
git commit -m "feat: add role executor router"
```

Expected result:

```text
[main <hash>] feat: add role executor router
```

---

### Task 2: WorkflowEngine Routed Execution

**Files:**
- Modify: `academic_engine/workflow_engine.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Write failing workflow integration tests**

In `tests/test_workflow_engine.py`, add this import near the existing imports:

```python
from academic_engine.executors import ExecutorUnavailableError, RoleExecutionContext
```

Add these tests to `WorkflowEngineTests` after `test_transient_role_failure_retries_once`:

```python
    def test_executor_router_receives_trusted_role_context(self) -> None:
        contexts: list[RoleExecutionContext] = []
        target = self.target
        root = self.root

        class RecordingRouter:
            def execute(self, context: RoleExecutionContext, prompt: str) -> None:
                contexts.append(context)
                if context.role_id == "thesis-style-editor":
                    path = context.sandbox_dir / target.relative_to(root)
                    path.write_text("# Updated through router\n", encoding="utf-8")
                _write_role_result(
                    context.output_file,
                    prompt,
                    context.sandbox_dir,
                    [target.relative_to(root)],
                    verdict=(
                        _evaluator_payload("submission-ready")
                        if context.role_id == "thesis-submission-evaluator"
                        else None
                    ),
                )

        router = RecordingRouter()

        result = WorkflowEngine(self.root, executor_router=router).run(
            work_id="demo",
            work_dir=self.work_dir,
            lane="thesis",
            action="style-pass",
            contract=self.contract(),
            base_prompt="test",
            use_search=True,
            model="test-model",
        )

        self.assertEqual(result.execution_status, "succeeded")
        self.assertEqual([context.role_id for context in contexts], ["thesis-style-editor", "thesis-submission-evaluator"])
        first = contexts[0]
        self.assertEqual(first.workflow_id, result.workflow_id)
        self.assertEqual(first.role_run_id, "01-thesis-style-editor")
        self.assertEqual(first.work_id, "demo")
        self.assertEqual(first.lane, "thesis")
        self.assertEqual(first.action, "style-pass")
        self.assertTrue(first.use_search)
        self.assertEqual(first.model, "test-model")
        self.assertFalse(first.is_evaluator)
        self.assertFalse(first.is_verifier)
        self.assertFalse(first.is_finalizer)
        second = contexts[1]
        self.assertTrue(second.is_evaluator)
        self.assertFalse(second.is_verifier)
        self.assertEqual(self.target.read_text(encoding="utf-8"), "# Updated through router\n")

    def test_executor_unavailable_fails_closed_with_stable_blocker(self) -> None:
        class UnavailableRouter:
            def execute(self, context: RoleExecutionContext, prompt: str) -> None:
                raise ExecutorUnavailableError("executor `stub-api` is not available")

        result = WorkflowEngine(self.root, executor_router=UnavailableRouter()).run(
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
        self.assertEqual(result.role_runs[0].attempt_count, 1)
        self.assertTrue(any(item["code"] == "executor-unavailable" for item in result.blockers))
        self.assertEqual(result.promotion.status, "blocked")
```

- [ ] **Step 2: Run failing workflow tests**

Run:

```bash
python3 -m pytest tests/test_workflow_engine.py::WorkflowEngineTests::test_executor_router_receives_trusted_role_context tests/test_workflow_engine.py::WorkflowEngineTests::test_executor_unavailable_fails_closed_with_stable_blocker -q
```

Expected result:

```text
FAILED tests/test_workflow_engine.py::WorkflowEngineTests::test_executor_router_receives_trusted_role_context
TypeError: WorkflowEngine.__init__() got an unexpected keyword argument 'executor_router'
```

- [ ] **Step 3: Update `workflow_engine.py` imports and constructor**

In `academic_engine/workflow_engine.py`, replace the current `RoleExecutor` alias area with:

```python
from .executors import (
    CallableRoleExecutor,
    ExecutorRouter,
    ExecutorUnavailableError,
    LegacyRoleExecutor,
    RoleExecutionContext,
    RoleExecutorProtocol,
    build_executor_router,
)
```

Keep the existing `Callable` import only if another part of the file still uses it. If no longer used, remove `Callable` from:

```python
from collections.abc import Callable, Iterable
```

so it becomes:

```python
from collections.abc import Iterable
```

Change `WorkflowEngine.__init__` to:

```python
class WorkflowEngine:
    def __init__(
        self,
        root_dir: str | Path,
        *,
        executor_router: RoleExecutorProtocol | None = None,
        role_executor: LegacyRoleExecutor | None = None,
        role_timeout_seconds: int = ROLE_TIMEOUT_SECONDS,
        workflow_timeout_seconds: int = WORKFLOW_TIMEOUT_SECONDS,
    ):
        if executor_router is not None and role_executor is not None:
            raise ValueError("Pass either executor_router or role_executor, not both.")
        self.root_dir = Path(root_dir).resolve()
        if executor_router is not None:
            self.executor_router = executor_router
        elif role_executor is not None:
            self.executor_router = ExecutorRouter(default_executor=CallableRoleExecutor(role_executor))
        else:
            self.executor_router = build_executor_router()
        self.role_timeout_seconds = role_timeout_seconds
        self.workflow_timeout_seconds = workflow_timeout_seconds
```

- [ ] **Step 4: Route role execution through `RoleExecutionContext`**

In `_run_role()`, replace:

```python
self.role_executor(sandbox_dir, prompt, output_file, use_search, model)
```

with:

```python
context = RoleExecutionContext(
    workflow_id=workflow.workflow_id,
    role_run_id=role_run_id,
    role_id=node.role_id,
    work_id=workflow.work_id,
    lane=workflow.lane,
    action=workflow.action,
    sandbox_dir=sandbox_dir,
    output_file=output_file,
    use_search=use_search,
    model=model,
    timeout_seconds=self.role_timeout_seconds,
    is_evaluator=node.evaluator,
    is_verifier=node.role_id in {"thesis-source-verifier", "academic-source-verifier"},
    is_finalizer=node.finalizer,
)
self.executor_router.execute(context, prompt)
```

Keep the existing elapsed-time timeout check immediately after execution:

```python
if time.monotonic() - started > self.role_timeout_seconds:
    raise TimeoutError(f"Role `{node.role_id}` exceeded {self.role_timeout_seconds} seconds.")
```

- [ ] **Step 5: Add fail-closed handling for unavailable executors**

In the `_run_role()` attempt loop, add a specific `ExecutorUnavailableError` branch before the transient retry branch:

```python
            except ExecutorUnavailableError as exc:
                error = exc
                break
            except (OSError, TimeoutError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                error = exc
                if attempt >= 2:
                    break
```

Then replace the error blocker block:

```python
if error is not None:
    role.status = "failed"
    role.error = str(error)
    role.blockers.append(_runtime_blocker("role-execution-failed", f"{node.role_id}: {error}"))
```

with:

```python
if error is not None:
    role.status = "failed"
    role.error = str(error)
    if isinstance(error, ExecutorUnavailableError):
        role.blockers.append(_runtime_blocker("executor-unavailable", f"{node.role_id}: {error}"))
    else:
        role.blockers.append(_runtime_blocker("role-execution-failed", f"{node.role_id}: {error}"))
```

- [ ] **Step 6: Run focused workflow tests**

Run:

```bash
python3 -m pytest tests/test_workflow_engine.py::WorkflowEngineTests::test_executor_router_receives_trusted_role_context tests/test_workflow_engine.py::WorkflowEngineTests::test_executor_unavailable_fails_closed_with_stable_blocker tests/test_workflow_engine.py::WorkflowEngineTests::test_transient_role_failure_retries_once -q
```

Expected result:

```text
3 passed
```

- [ ] **Step 7: Run the full workflow engine test file**

Run:

```bash
python3 -m pytest tests/test_workflow_engine.py -q
```

Expected result:

```text
passed
```

- [ ] **Step 8: Commit workflow integration**

Run:

```bash
git add academic_engine/workflow_engine.py tests/test_workflow_engine.py
git commit -m "feat: route workflow roles through executor context"
```

Expected result:

```text
[main <hash>] feat: route workflow roles through executor context
```

---

### Task 3: CLI Wiring And Test Updates

**Files:**
- Modify: `academic_engine/work_cli.py`
- Modify: `tests/test_academic_engine.py`

- [ ] **Step 1: Update CLI workflow construction**

In `academic_engine/work_cli.py`, add:

```python
from .executors import build_executor_router
```

Then change `_run_role_workflow()` from:

```python
engine = WorkflowEngine(root_dir, role_executor=_run_codex)
```

to:

```python
engine = WorkflowEngine(root_dir, executor_router=build_executor_router())
```

- [ ] **Step 2: Remove direct Codex subprocess helper from `work_cli.py`**

Delete these functions from `academic_engine/work_cli.py`:

```python
def _run_codex(root_dir: Path, prompt: str, out_file: Path, use_search: bool, model: str | None) -> None:
    codex_bin = _resolve_codex_bin()
    cmd = [codex_bin]
    if use_search:
        cmd.append("--search")
    cmd.extend(["exec", "-C", str(root_dir), "--skip-git-repo-check", "--full-auto", "-o", str(out_file)])
    chosen_model = model or os.environ.get("CODEX_MODEL")
    if chosen_model:
        cmd.extend(["-m", chosen_model])
    try:
        subprocess.run(
            cmd + ["-"],
            input=prompt,
            text=True,
            check=True,
            timeout=ROLE_TIMEOUT_SECONDS,
        )
    except FileNotFoundError:
        print(
            f"Ошибка: не найден исполняемый файл `{codex_bin}`. "
            "Установите Codex CLI или задайте переменную окружения CODEX_BIN.",
            file=sys.stderr,
        )
        raise
    except subprocess.CalledProcessError as exc:
        print(
            f"Ошибка: команда Codex завершилась с кодом {exc.returncode}. См. вывод процесса выше.",
            file=sys.stderr,
        )
        raise
    except subprocess.TimeoutExpired:
        print(
            f"Ошибка: роль Codex превысила timeout {ROLE_TIMEOUT_SECONDS} секунд.",
            file=sys.stderr,
        )
        raise
```

Delete `_resolve_codex_bin()` from `academic_engine/work_cli.py`:

```python
def _resolve_codex_bin() -> str:
    configured = os.environ.get("CODEX_BIN")
    resolved = resolve_executable(
        configured,
        "codex",
        extra_candidates=("/Applications/Codex.app/Contents/Resources/codex",),
    )
    if resolved:
        return resolved
    requested = (configured or "codex").strip() or "codex"
    print(
        f"Ошибка: не найден исполняемый файл `{requested}`. "
        "Установите Codex CLI или задайте переменную окружения CODEX_BIN.",
        file=sys.stderr,
    )
    raise FileNotFoundError(requested)
```

After deletion, remove imports that become unused in `work_cli.py`. Run `python3 -m ruff check academic_engine/work_cli.py` in Step 6 to identify them.

- [ ] **Step 3: Update tests that patched `_run_codex`**

In `tests/test_academic_engine.py`, add this import near the other project imports:

```python
from academic_engine.executors import CallableRoleExecutor, ExecutorRouter
```

Replace each block shaped like:

```python
with patch.object(work_cli_module, "_run_codex", side_effect=fake_run_codex):
```

with:

```python
router = ExecutorRouter(default_executor=CallableRoleExecutor(fake_run_codex))
with patch.object(work_cli_module, "build_executor_router", return_value=router):
```

There are two expected replacements:

```bash
rg -n "patch.object\\(work_cli_module, \"_run_codex\"" tests/test_academic_engine.py
```

Expected before replacement:

```text
tests/test_academic_engine.py:5406:            with patch.object(work_cli_module, "_run_codex", side_effect=fake_run_codex):
tests/test_academic_engine.py:5529:            with patch.object(work_cli_module, "_run_codex", side_effect=fake_run_codex):
```

Expected after replacement:

```text
```

- [ ] **Step 4: Add a CLI-level explicit route failure test**

Add this test to `ArticleBundleLifecycleTests` in `tests/test_academic_engine.py`:

```python
    def test_launch_academic_explicit_unknown_evaluator_executor_fails_closed(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        env = {
            "CODEX_BIN": str(self.fake_codex),
            "ACADEMIC_ENGINE_EVALUATOR_EXECUTOR": "missing-executor",
        }

        with patch.dict(os.environ, env, clear=False):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    [
                        "launch-academic",
                        "review",
                        "articles/drafts/demo.md",
                        "--no-search",
                        "--workflow-id",
                        "worker-unknown-executor-test",
                    ],
                    root_dir=self.root,
                )

        self.assertEqual(code, 0)
        self.assertEqual(stderr.getvalue(), "")
        workflow_path = self.root / "output" / "runs" / "worker-unknown-executor-test" / "workflow.json"
        self.assertTrue(workflow_path.exists())
        payload = json.loads(workflow_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["execution_status"], "failed")
        self.assertTrue(any(item["code"] == "executor-unavailable" for item in payload["blockers"]))
```

- [ ] **Step 5: Run focused CLI tests**

Run:

```bash
python3 -m pytest tests/test_academic_engine.py::ArticleBundleLifecycleTests::test_launch_academic_run_writes_article_bundle_manifest tests/test_academic_engine.py::ArticleBundleLifecycleTests::test_launch_academic_explicit_unknown_evaluator_executor_fails_closed -q
```

Expected result:

```text
2 passed
```

- [ ] **Step 6: Run lint on changed Python files**

Run:

```bash
python3 -m ruff check academic_engine/executors.py academic_engine/workflow_engine.py academic_engine/work_cli.py tests/test_executors.py tests/test_workflow_engine.py tests/test_academic_engine.py
```

Expected result:

```text
All checks passed!
```

- [ ] **Step 7: Commit CLI wiring**

Run:

```bash
git add academic_engine/work_cli.py tests/test_academic_engine.py
git commit -m "feat: wire CLI workflows through executor router"
```

Expected result:

```text
[main <hash>] feat: wire CLI workflows through executor router
```

---

### Task 4: Verification And Closeout

**Files:**
- Verify: `academic_engine/executors.py`
- Verify: `academic_engine/workflow_engine.py`
- Verify: `academic_engine/work_cli.py`
- Verify: `tests/test_executors.py`
- Verify: `tests/test_workflow_engine.py`
- Verify: `tests/test_academic_engine.py`

- [ ] **Step 1: Run executor and workflow regression tests**

Run:

```bash
python3 -m pytest tests/test_executors.py tests/test_workflow_engine.py -q
```

Expected result:

```text
passed
```

- [ ] **Step 2: Run focused CLI regression tests**

Run:

```bash
python3 -m pytest tests/test_academic_engine.py::ArticleBundleLifecycleTests -q
```

Expected result:

```text
passed
```

- [ ] **Step 3: Run the full Python regression suite if time allows**

Run:

```bash
python3 -m pytest -q
```

Expected result:

```text
passed
```

If the full suite is too slow for the execution window, record the exact focused test commands from Steps 1 and 2 in the final response.

- [ ] **Step 4: Check worktree cleanliness**

Run:

```bash
git status --short
```

Expected result:

```text
```

- [ ] **Step 5: Report implementation evidence**

Final response must include:

- commits created;
- tests run and results;
- whether the full suite was run;
- any residual risk, especially if only focused tests were run.
