from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib import error as urlerror
from urllib import request as urlrequest

from .utils import resolve_executable

LegacyRoleExecutor = Callable[[Path, str, Path, bool, str | None], None]
OutputStrategy = Callable[["RoleExecutionContext", str], None]
HttpTransport = Callable[[urlrequest.Request, float], tuple[int, bytes]]


OPENROUTER_EXECUTION_MODES = frozenset({"read-only", "write-plan"})
SUPPORTED_ROLE_IDS = frozenset(
    {
        "thesis-structure-architect",
        "thesis-research-synthesizer",
        "thesis-source-verifier",
        "thesis-draft-writer",
        "thesis-citation-checker",
        "thesis-argument-critic",
        "thesis-style-editor",
        "thesis-submission-evaluator",
        "academic-intake",
        "academic-source-acquirer",
        "academic-source-verifier",
        "academic-evidence-cartographer",
        "academic-draft-writer",
        "academic-citation-checker",
        "academic-counterargument-critic",
        "academic-submission-evaluator",
        "academic-repair-orchestrator",
        "academic-finalizer",
    }
)

# This map is the runtime allowlist, not the qualification matrix. A role is
# added only after its serial qualification record is complete. It intentionally
# contains just the current read-only RC baselines.
OPENROUTER_ROLE_POLICY: dict[str, dict[str, str]] = {
    "academic-source-verifier": {
        "executor_id": "openrouter",
        "execution_mode": "read-only",
    },
    "academic-submission-evaluator": {
        "executor_id": "openrouter",
        "execution_mode": "read-only",
    },
}

# Compatibility index for existing RC evidence. New code must use
# OPENROUTER_ROLE_POLICY rather than treating evaluator/verifier routes as the
# provider policy itself.
OPENROUTER_ALLOWED_ROLE_ROUTES = {
    "academic-source-verifier": "verifier",
    "academic-submission-evaluator": "evaluator",
}


class ExecutorUnavailableError(RuntimeError):
    """Raised when an explicitly selected executor cannot run a role."""


class ProviderExecutionError(ExecutorUnavailableError):
    def __init__(self, blocker_code: str, message: str):
        super().__init__(message)
        self.blocker_code = blocker_code


@dataclass(frozen=True)
class ProviderSmokeResult:
    provider_id: str
    model: str
    content_length: int
    preview: str


@dataclass(frozen=True)
class ExecutorSelection:
    route_name: str
    executor_id: str
    execution_mode: str | None = None

    def to_dict(self) -> dict[str, str]:
        payload = {
            "route_name": self.route_name,
            "executor_id": self.executor_id,
        }
        if self.execution_mode is not None:
            payload["execution_mode"] = self.execution_mode
        return payload


def _urllib_transport(request: urlrequest.Request, timeout: float) -> tuple[int, bytes]:
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            status = getattr(response, "status", response.getcode())
            return int(status), response.read()
    except urlerror.HTTPError as exc:
        return int(exc.code), exc.read()


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
    execution_mode: str | None = None


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
        """Transport raw provider output; WorkflowEngine remains the only write authority."""
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


class UnavailableExecutor:
    def __init__(self, executor_id: str):
        self.executor_id = executor_id

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        raise ExecutorUnavailableError(f"executor `{self.executor_id}` is not available")


class ForbiddenProviderRouteExecutor:
    def __init__(self, executor_id: str, route_name: str):
        self.executor_id = executor_id
        self.route_name = route_name

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        raise ProviderExecutionError(
            "provider-route-forbidden",
            f"executor `{self.executor_id}` is not allowed for {self.route_name} route in this slice",
        )


@dataclass(frozen=True)
class ExecutorRouter:
    default_executor: RoleExecutorProtocol
    evaluator_executor: RoleExecutorProtocol | None = None
    verifier_executor: RoleExecutorProtocol | None = None
    default_executor_id: str = "custom"
    evaluator_executor_id: str | None = None
    verifier_executor_id: str | None = None
    role_executors: Mapping[str, RoleExecutorProtocol] = field(default_factory=dict)
    role_executor_ids: Mapping[str, str] = field(default_factory=dict)
    role_policies: Mapping[str, Mapping[str, str]] = field(default_factory=lambda: OPENROUTER_ROLE_POLICY)

    def execute(self, context: RoleExecutionContext, prompt: str) -> None:
        executor = self._select(context)
        executor.execute(context, prompt)

    def describe_selection(self, context: RoleExecutionContext) -> ExecutorSelection:
        role_executor_id = self.role_executor_ids.get(context.role_id)
        if role_executor_id is not None:
            return ExecutorSelection(
                "role",
                role_executor_id,
                _openrouter_execution_mode(context.role_id, role_executor_id, self.role_policies),
            )
        if context.is_evaluator and self.evaluator_executor is not None:
            executor_id = self.evaluator_executor_id or "custom"
            return ExecutorSelection(
                "evaluator",
                executor_id,
                _openrouter_execution_mode(context.role_id, executor_id, self.role_policies),
            )
        if context.is_verifier and self.verifier_executor is not None:
            executor_id = self.verifier_executor_id or "custom"
            return ExecutorSelection(
                "verifier",
                executor_id,
                _openrouter_execution_mode(context.role_id, executor_id, self.role_policies),
            )
        return ExecutorSelection("default", self.default_executor_id)

    def _select(self, context: RoleExecutionContext) -> RoleExecutorProtocol:
        selection = self.describe_selection(context)
        if selection.executor_id == "openrouter" and selection.execution_mode is None:
            return ForbiddenProviderRouteExecutor(selection.executor_id, selection.route_name)
        if context.role_id in self.role_executors:
            return self.role_executors[context.role_id]
        if context.is_evaluator and self.evaluator_executor is not None:
            return self.evaluator_executor
        if context.is_verifier and self.verifier_executor is not None:
            return self.verifier_executor
        return self.default_executor


def build_executor_router(
    environ: Mapping[str, str] | None = None,
    registry: Mapping[str, RoleExecutorProtocol] | None = None,
) -> ExecutorRouter:
    env = environ if environ is not None else os.environ
    available = dict(registry) if registry is not None else _default_registry(env)

    default_id = _clean_executor_id(env.get("ACADEMIC_ENGINE_DEFAULT_EXECUTOR")) or "codex-cli"
    evaluator_id = _clean_executor_id(env.get("ACADEMIC_ENGINE_EVALUATOR_EXECUTOR"))
    verifier_id = _clean_executor_id(env.get("ACADEMIC_ENGINE_VERIFIER_EXECUTOR"))
    role_executor_ids = {
        role_id: executor_id
        for role_id in sorted(SUPPORTED_ROLE_IDS)
        if (executor_id := _clean_executor_id(env.get(_role_executor_env_name(role_id)))) is not None
    }

    return ExecutorRouter(
        default_executor=_executor_for(default_id, available, route_name="default"),
        evaluator_executor=_executor_for(evaluator_id, available, route_name="evaluator") if evaluator_id else None,
        verifier_executor=_executor_for(verifier_id, available, route_name="verifier") if verifier_id else None,
        default_executor_id=default_id,
        evaluator_executor_id=evaluator_id,
        verifier_executor_id=verifier_id,
        role_executors={
            role_id: _executor_for(executor_id, available, route_name="role")
            for role_id, executor_id in role_executor_ids.items()
        },
        role_executor_ids=role_executor_ids,
        role_policies=OPENROUTER_ROLE_POLICY,
    )


def _default_registry(environ: Mapping[str, str]) -> dict[str, RoleExecutorProtocol]:
    return {
        "codex-cli": CodexCliExecutor(environ=environ),
        "stub-api": StubApiExecutor(),
        "openrouter": build_openrouter_executor(environ=environ),
    }


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


def _executor_for(
    executor_id: str,
    registry: Mapping[str, RoleExecutorProtocol],
    *,
    route_name: str,
) -> RoleExecutorProtocol:
    if route_name == "default" and executor_id == "openrouter":
        return ForbiddenProviderRouteExecutor(executor_id, route_name)
    return registry.get(executor_id) or UnavailableExecutor(executor_id)


def _clean_executor_id(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None


def _role_executor_env_name(role_id: str) -> str:
    normalized = role_id.upper().replace("-", "_")
    return f"ACADEMIC_ENGINE_ROLE_EXECUTOR_{normalized}"


def _openrouter_execution_mode(
    role_id: str,
    executor_id: str,
    policies: Mapping[str, Mapping[str, str]],
) -> str | None:
    if executor_id != "openrouter":
        return None
    policy = policies.get(role_id)
    if not isinstance(policy, Mapping) or set(policy) != {"executor_id", "execution_mode"}:
        return None
    if policy.get("executor_id") != "openrouter":
        return None
    mode = policy.get("execution_mode")
    return mode if mode in OPENROUTER_EXECUTION_MODES else None


def _clean_env_value(value: str | None) -> str | None:
    clean = (value or "").strip()
    return clean or None
