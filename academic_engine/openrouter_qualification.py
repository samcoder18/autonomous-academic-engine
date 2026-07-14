from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
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
    context_paths: tuple[str, ...] = ()
    write_path: str = ""
    policy_path: str = ""
    checkpoint: str = ""
    requires_no_search: bool = False


_CANONICAL_QUALIFICATION_CANDIDATES = (
    QualificationCandidate(
        role_id="academic-intake",
        work_id="openrouter-live-smoke",
        lane="article",
        action="qualify-intake",
        seed_path="works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md",
        execution_mode="write-plan",
        context_paths=("works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md",),
        write_path="works/openrouter-live-smoke/articles/briefs/academic-intake-qualification.md",
        policy_path="agents/academic-intake.md",
        checkpoint="qualification:academic-intake",
    ),
    QualificationCandidate(
        role_id="academic-source-acquirer",
        work_id="openrouter-live-smoke",
        lane="article",
        action="qualify-source-acquirer",
        seed_path="works/openrouter-live-smoke/articles/briefs/academic-source-acquirer-qualification.md",
        execution_mode="write-plan",
        context_paths=("works/openrouter-live-smoke/articles/briefs/academic-source-acquirer-qualification.md",),
        write_path="works/openrouter-live-smoke/articles/evidence/academic-source-acquirer-qualification.md",
        policy_path="agents/academic-source-acquirer.md",
        checkpoint="qualification:academic-source-acquirer",
        requires_no_search=True,
    ),
)

# Public for tests and operator-facing inspection, but verified against the
# immutable registry before a qualification can reach any executor.
QUALIFICATION_CANDIDATES = _CANONICAL_QUALIFICATION_CANDIDATES


class QualificationError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class QualificationFixtures:
    context_paths: tuple[tuple[str, Path], ...]
    write_path: tuple[str, Path]
    canonical_paths: tuple[tuple[str, Path], ...]


def run_openrouter_role_qualification(
    root_dir: str | Path,
    candidate_role_id: str,
    work_id: str,
    seed_path: str,
    use_search: bool,
    model: str | None,
    router: ExecutorRouter | None = None,
    *,
    target_path: str | None = None,
) -> WorkflowRun:
    """Run one static OpenRouter qualification without production routing."""
    root = Path(root_dir).expanduser().resolve()
    candidate = _candidate_for(candidate_role_id)
    fixtures = _validate_candidate_inputs(root, candidate, work_id, seed_path, target_path)
    if candidate.requires_no_search and use_search:
        raise QualificationError(
            "qualification-search-forbidden",
            "This qualification candidate requires --no-search and cannot invoke source acquisition.",
        )
    before_hashes = _fixture_hashes(fixtures, _sha256)
    metadata = _qualification_metadata(candidate, before_hashes, before_hashes)
    contract = _qualification_contract(candidate, fixtures)
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
        base_prompt=_qualification_prompt(candidate, fixtures),
        use_search=use_search,
        model=model,
        metadata=metadata,
        role_plan=(
            RoleNode(
                role_id=candidate.role_id,
                policy_path=candidate.policy_path,
                checkpoints=(candidate.checkpoint,),
            ),
        ),
        promotion_enabled=False,
    )

    after_hashes = _fixture_hashes(fixtures, _sha256_or_missing)
    result.metadata = _qualification_metadata(candidate, before_hashes, after_hashes)
    if before_hashes != after_hashes:
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
    if QUALIFICATION_CANDIDATES != _CANONICAL_QUALIFICATION_CANDIDATES:
        _raise_forbidden_candidate()
    for candidate in QUALIFICATION_CANDIDATES:
        if candidate.role_id == candidate_role_id:
            return candidate
    _raise_forbidden_candidate()


def _raise_forbidden_candidate() -> None:
    raise ProviderExecutionError(
        "provider-route-forbidden",
        "The requested role is not enabled for OpenRouter qualification.",
    )


def _validate_candidate_inputs(
    root: Path,
    candidate: QualificationCandidate,
    work_id: str,
    seed_path: str,
    target_path: str | None,
) -> QualificationFixtures:
    if work_id != candidate.work_id:
        raise QualificationError(
            "qualification-work-mismatch",
            "The requested work is not approved for this qualification candidate.",
        )
    if not _matches_fixed_relative_path(seed_path, candidate.seed_path):
        raise QualificationError(
            "qualification-seed-forbidden",
            "The requested seed is not the approved qualification fixture.",
        )
    if candidate.write_path != candidate.seed_path:
        if target_path is None:
            raise QualificationError(
                "qualification-target-required",
                "This qualification candidate requires the approved evidence template target.",
            )
        if not _matches_fixed_relative_path(target_path, candidate.write_path):
            raise QualificationError(
                "qualification-target-forbidden",
                "The requested target is not the approved qualification evidence template.",
            )
    elif target_path is not None and not _matches_fixed_relative_path(target_path, candidate.write_path):
        raise QualificationError(
            "qualification-target-forbidden",
            "The requested target is not the approved qualification fixture.",
        )

    work_dir = root / "works" / candidate.work_id
    if not work_dir.is_dir():
        raise QualificationError(
            "qualification-work-missing",
            "The approved qualification work is unavailable in this workspace.",
        )

    context_paths = tuple(
        (
            relative_path,
            _validate_workspace_fixture(
                root,
                relative_path,
                missing_code="qualification-seed-missing",
                missing_message="The approved qualification fixture is unavailable in this workspace.",
            ),
        )
        for relative_path in candidate.context_paths
    )
    write_path = (
        candidate.write_path,
        _validate_workspace_fixture(
            root,
            candidate.write_path,
            missing_code="qualification-target-missing",
            missing_message="The approved qualification evidence template is unavailable in this workspace.",
        ),
    )
    canonical_by_path = {relative_path: path for relative_path, path in context_paths}
    canonical_by_path[write_path[0]] = write_path[1]
    return QualificationFixtures(
        context_paths=context_paths,
        write_path=write_path,
        canonical_paths=tuple(canonical_by_path.items()),
    )


def _matches_fixed_relative_path(raw_path: object, expected_path: str) -> bool:
    if not isinstance(raw_path, str) or not raw_path:
        return False
    path = Path(raw_path)
    return not path.is_absolute() and path.as_posix() == expected_path


def _validate_workspace_fixture(
    root: Path,
    relative_path: str,
    *,
    missing_code: str,
    missing_message: str,
) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or path.as_posix() != relative_path or _has_symlink_component(root, path):
        raise QualificationError(
            "qualification-fixture-forbidden",
            "The approved qualification fixture must not resolve through a symbolic link.",
        )
    expected_path = root / path
    if not expected_path.exists() or not expected_path.is_file():
        raise QualificationError(missing_code, missing_message)
    try:
        canonical_path = expected_path.resolve(strict=True)
        canonical_path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise QualificationError(
            "qualification-workspace-invalid",
            "The approved qualification fixture is outside this workspace.",
        ) from exc
    return canonical_path


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
        or set(policy) != {"executor_id", "execution_mode"}
        or policy.get("executor_id") != "openrouter"
        or policy.get("execution_mode") != candidate.execution_mode
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


def _qualification_contract(candidate: QualificationCandidate, fixtures: QualificationFixtures) -> ExecutionContract:
    if candidate.role_id == "academic-intake":
        seed = str(fixtures.write_path[1])
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
            required_checkpoints=(candidate.checkpoint,),
            terminal_statuses=("strong-draft",),
            quality_gates=(),
            repair_policy=_qualification_repair_policy(),
            transitions=(),
            metadata=(("qualification_candidate", candidate.role_id),),
        )

    context_artifacts = tuple(
        RequiredArtifact(
            "qualification-source-dossier",
            str(path),
            "required",
            "Committed read-only source-acquirer qualification dossier.",
        )
        for _relative_path, path in fixtures.context_paths
    )
    write_relative_path, write_path = fixtures.write_path
    return ExecutionContract(
        lane=candidate.lane,
        action=candidate.action,
        title="OpenRouter source-acquirer qualification",
        summary="One-role sandbox-only qualification of the source-acquirer write-plan handoff.",
        target_kind="qualification evidence template",
        target_validation="Only the committed source dossier and evidence template are accepted.",
        prompt_rules=(
            "Use only the supplied qualification dossier and writable evidence template.",
            "Do not search, use connectors, request tools, or claim live source acquisition.",
        ),
        deliverables=(
            "Return a strict role-result/v1 after the engine applies a valid write plan to the evidence template.",
        ),
        required_context=context_artifacts,
        allowed_write_scopes=(
            AllowedWriteScope(
                "qualification-evidence-template",
                str(write_path),
                "Only permitted sandbox write target for the source-acquirer handoff.",
            ),
        ),
        required_outputs=(
            RequiredArtifact(
                "qualification-evidence-template",
                str(write_path),
                "required",
                "Qualified sandbox evidence-pack handoff.",
            ),
        ),
        required_checkpoints=(candidate.checkpoint,),
        terminal_statuses=("strong-draft",),
        quality_gates=(),
        repair_policy=_qualification_repair_policy(),
        transitions=(),
        metadata=(
            ("qualification_candidate", candidate.role_id),
            ("qualification_write_path", write_relative_path),
        ),
    )


def _qualification_repair_policy() -> RepairPolicy:
    return RepairPolicy(
        eligible=False,
        max_iterations=0,
        safe_only=True,
        triggers=(),
        terminal_reasons=(),
    )


def _qualification_prompt(candidate: QualificationCandidate, fixtures: QualificationFixtures) -> str:
    if candidate.role_id == "academic-intake":
        return "\n".join(
            (
                "Perform only the academic-intake qualification task for the supplied fixture.",
                "The fixture below is the complete task input; do not infer or request other workspace context.",
                "--- QUALIFICATION SEED ---",
                fixtures.context_paths[0][1].read_text(encoding="utf-8"),
                "--- END QUALIFICATION SEED ---",
            )
        )

    lines = [
        "Perform only the academic-source-acquirer qualification task.",
        "The labelled fixtures below are the complete provider-visible input.",
        "Do not search, use connectors, request tools, inspect files, call a shell, use Git, or request secrets.",
        "Do not claim live acquisition, primary verification, triangulation, coverage, or submission readiness.",
    ]
    for relative_path, path in fixtures.context_paths:
        lines.extend(
            (
                f"--- READ-ONLY QUALIFICATION DOSSIER: {relative_path} ---",
                path.read_text(encoding="utf-8"),
                "--- END READ-ONLY QUALIFICATION DOSSIER ---",
            )
        )
    write_relative_path, write_path = fixtures.write_path
    lines.extend(
        (
            f"--- WRITABLE EVIDENCE TEMPLATE: {write_relative_path} ---",
            write_path.read_text(encoding="utf-8"),
            "--- END WRITABLE EVIDENCE TEMPLATE ---",
            "The template label above is the sole permitted write-plan path;",
            "its canonical source file must not change.",
        )
    )
    return "\n".join(lines)


def _fixture_hashes(
    fixtures: QualificationFixtures,
    hasher: Callable[[Path], str],
) -> dict[str, str]:
    return {relative_path: hasher(path) for relative_path, path in fixtures.canonical_paths}


def _qualification_metadata(
    candidate: QualificationCandidate,
    before_hashes: Mapping[str, str],
    after_hashes: Mapping[str, str],
) -> dict[str, object]:
    canonical_unchanged = before_hashes == after_hashes
    if candidate.role_id == "academic-intake":
        seed_path = candidate.seed_path
        return {
            "candidate_id": candidate.role_id,
            "allowed_path": seed_path,
            "before_sha256": before_hashes[seed_path],
            "after_sha256": after_hashes[seed_path],
            "canonical_unchanged": canonical_unchanged,
        }
    if candidate.role_id == "academic-source-acquirer":
        context_path = candidate.context_paths[0]
        write_path = candidate.write_path
        return {
            "candidate_id": candidate.role_id,
            "context_path": context_path,
            "write_path": write_path,
            "context_before_sha256": before_hashes[context_path],
            "context_after_sha256": after_hashes[context_path],
            "write_before_sha256": before_hashes[write_path],
            "write_after_sha256": after_hashes[write_path],
            "canonical_unchanged": canonical_unchanged,
        }
    _raise_forbidden_candidate()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_or_missing(path: Path) -> str:
    try:
        return _sha256(path)
    except OSError:
        return ""
