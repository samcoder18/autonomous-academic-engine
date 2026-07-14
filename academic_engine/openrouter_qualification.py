from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from .action_specs import AllowedWriteScope, ExecutionContract, RepairPolicy, RequiredArtifact
from .executors import ExecutorRouter, ProviderExecutionError, UnavailableExecutor, build_openrouter_executor
from .workflow_engine import RoleNode, WorkflowEngine, WorkflowRun, _write_json


@dataclass(frozen=True)
class QualificationCandidate:
    role_id: str
    work_id: str
    lane: str
    action: str
    seed_path: str
    execution_mode: str


QUALIFICATION_CANDIDATES = (
    QualificationCandidate(
        role_id="academic-intake",
        work_id="openrouter-live-smoke",
        lane="article",
        action="qualify-intake",
        seed_path="works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md",
        execution_mode="write-plan",
    ),
)


class QualificationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def run_openrouter_role_qualification(
    root_dir: str | Path,
    candidate_role_id: str,
    work_id: str,
    seed_path: str,
    use_search: bool,
    model: str | None,
    router: ExecutorRouter | None = None,
) -> WorkflowRun:
    """Run the sole intake write-plan qualification without production routing."""
    root = Path(root_dir).expanduser().resolve()
    candidate = _candidate_for(candidate_role_id)
    canonical_seed = _validate_candidate_input(root, candidate, work_id, seed_path)
    canonical_before = _sha256(canonical_seed)
    metadata = {
        "candidate_id": candidate.role_id,
        "allowed_path": candidate.seed_path,
        "before_sha256": canonical_before,
        "after_sha256": canonical_before,
        "canonical_unchanged": True,
    }
    contract = _qualification_contract(candidate, canonical_seed)
    if router is not None:
        _validate_injected_qualification_router(router, candidate)
    executor_router = router if router is not None else _qualification_router(candidate)
    engine = WorkflowEngine(root, executor_router=executor_router)
    result = engine.run(
        work_id=candidate.work_id,
        work_dir=root / "works" / candidate.work_id,
        lane=candidate.lane,
        action=candidate.action,
        contract=contract,
        base_prompt=_qualification_prompt(canonical_seed),
        use_search=use_search,
        model=model,
        metadata=metadata,
        role_plan=(
            RoleNode(
                role_id=candidate.role_id,
                policy_path="agents/academic-intake.md",
                checkpoints=("qualification:academic-intake",),
            ),
        ),
        promotion_enabled=False,
    )

    canonical_after = _sha256_or_missing(canonical_seed)
    result.metadata = {
        "candidate_id": candidate.role_id,
        "allowed_path": candidate.seed_path,
        "before_sha256": canonical_before,
        "after_sha256": canonical_after,
        "canonical_unchanged": canonical_before == canonical_after,
    }
    if canonical_before != canonical_after:
        result.blockers.append(
            {
                "category": "runtime",
                "code": "qualification-canonical-drift",
                "message": "Qualification canonical fixture changed during the sandbox-only run.",
                "repairable": False,
                "blocks_statuses": ["submission-ready"],
            }
        )
        result.status = "failed"
        result.execution_status = "failed"
        result.readiness_status = "strong-draft-with-blockers"
    _write_json(Path(result.workflow_dir) / "workflow.json", result.to_dict())
    return result


def _candidate_for(candidate_role_id: str) -> QualificationCandidate:
    for candidate in QUALIFICATION_CANDIDATES:
        if candidate.role_id == candidate_role_id:
            return candidate
    raise ProviderExecutionError(
        "provider-route-forbidden",
        "The requested role is not enabled for OpenRouter qualification.",
    )


def _validate_candidate_input(
    root: Path,
    candidate: QualificationCandidate,
    work_id: str,
    seed_path: str,
) -> Path:
    if work_id != candidate.work_id:
        raise QualificationError(
            "qualification-work-mismatch",
            "The requested work is not approved for this qualification candidate.",
        )
    if Path(seed_path).as_posix() != candidate.seed_path or Path(seed_path).is_absolute():
        raise QualificationError(
            "qualification-seed-forbidden",
            "The requested seed is not the approved qualification fixture.",
        )

    work_dir = root / "works" / candidate.work_id
    if not work_dir.is_dir():
        raise QualificationError(
            "qualification-work-missing",
            "The approved qualification work is unavailable in this workspace.",
        )
    expected_seed = root / candidate.seed_path
    if not expected_seed.exists() or not expected_seed.is_file():
        raise QualificationError(
            "qualification-seed-missing",
            "The approved qualification fixture is unavailable in this workspace.",
        )
    if _has_symlink_component(root, Path(candidate.seed_path)):
        raise QualificationError(
            "qualification-seed-forbidden",
            "The approved qualification fixture must not resolve through a symbolic link.",
        )
    try:
        canonical_seed = expected_seed.resolve(strict=True)
        canonical_seed.relative_to(root)
    except (OSError, ValueError) as exc:
        raise QualificationError(
            "qualification-workspace-invalid",
            "The approved qualification fixture is outside this workspace.",
        ) from exc
    return canonical_seed


def _has_symlink_component(root: Path, relative_path: Path) -> bool:
    cursor = root
    for part in relative_path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return True
    return False


def _qualification_router(candidate: QualificationCandidate) -> ExecutorRouter:
    return ExecutorRouter(
        default_executor=UnavailableExecutor("qualification-default"),
        default_executor_id="qualification-default",
        role_executors={candidate.role_id: build_openrouter_executor()},
        role_executor_ids={candidate.role_id: "openrouter"},
        role_policies={
            candidate.role_id: {
                "executor_id": "openrouter",
                "execution_mode": candidate.execution_mode,
            }
        },
    )


def _validate_injected_qualification_router(router: object, candidate: QualificationCandidate) -> None:
    if not isinstance(router, ExecutorRouter):
        _raise_forbidden_injected_router()

    expected_role_ids = {candidate.role_id}
    policy = router.role_policies.get(candidate.role_id)
    if (
        set(router.role_executors) != expected_role_ids
        or set(router.role_executor_ids) != expected_role_ids
        or set(router.role_policies) != expected_role_ids
        or router.role_executor_ids.get(candidate.role_id) != "openrouter"
        or not isinstance(policy, Mapping)
        or policy.get("executor_id") != "openrouter"
        or policy.get("execution_mode") != "write-plan"
        or not isinstance(router.default_executor, UnavailableExecutor)
        or router.default_executor.executor_id != "qualification-default"
        or router.default_executor_id != "qualification-default"
        or router.evaluator_executor is not None
        or router.evaluator_executor_id is not None
        or router.verifier_executor is not None
        or router.verifier_executor_id is not None
    ):
        _raise_forbidden_injected_router()


def _raise_forbidden_injected_router() -> None:
    raise ProviderExecutionError(
        "provider-route-forbidden",
        "The injected router does not match the bounded OpenRouter qualification route.",
    )


def _qualification_contract(candidate: QualificationCandidate, canonical_seed: Path) -> ExecutionContract:
    seed = str(canonical_seed)
    return ExecutionContract(
        lane=candidate.lane,
        action=candidate.action,
        title="OpenRouter intake qualification",
        summary="One-role sandbox-only qualification of the intake write-plan route.",
        target_kind="qualification fixture",
        target_validation="Only the committed qualification fixture is accepted.",
        prompt_rules=("Use only the supplied qualification seed as task input.",),
        deliverables=("Return a strict role-result/v1 after the engine applies a valid write plan.",),
        required_context=(
            RequiredArtifact("qualification-seed", seed, "required", "Committed intake qualification fixture."),
        ),
        allowed_write_scopes=(
            AllowedWriteScope("qualification-seed", seed, "Only permitted sandbox write target."),
        ),
        required_outputs=(
            RequiredArtifact("qualification-seed", seed, "required", "Qualified sandbox fixture output."),
        ),
        required_checkpoints=("qualification:academic-intake",),
        terminal_statuses=("strong-draft",),
        quality_gates=(),
        repair_policy=RepairPolicy(
            eligible=False,
            max_iterations=0,
            safe_only=True,
            triggers=(),
            terminal_reasons=(),
        ),
        transitions=(),
        metadata=(("qualification_candidate", candidate.role_id),),
    )


def _qualification_prompt(canonical_seed: Path) -> str:
    return "\n".join(
        (
            "Perform only the academic-intake qualification task for the supplied fixture.",
            "The fixture below is the complete task input; do not infer or request other workspace context.",
            "--- QUALIFICATION SEED ---",
            canonical_seed.read_text(encoding="utf-8"),
            "--- END QUALIFICATION SEED ---",
        )
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_or_missing(path: Path) -> str:
    try:
        return _sha256(path)
    except OSError:
        return ""
