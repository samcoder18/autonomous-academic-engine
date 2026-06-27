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
