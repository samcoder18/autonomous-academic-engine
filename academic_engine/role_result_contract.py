from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROLE_RESULT_VERSION = "role-result/v1"
ROLE_RESULT_STATUSES = {"succeeded", "blocked", "failed"}
REQUIRED_ROLE_RESULT_FIELDS = (
    "version",
    "workflow_id",
    "role_run_id",
    "role_id",
    "work_id",
    "lane",
    "action",
    "status",
    "checkpoints",
    "checkpoint_evidence",
    "blockers",
    "artifacts",
    "verdict",
)


@dataclass(frozen=True)
class ArtifactRecord:
    path: str
    sha256: str
    size: int
    media_type: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "media_type": self.media_type,
        }


@dataclass(frozen=True)
class RoleResultContext:
    workflow_id: str
    expected_role_run_id: str
    role_id: str
    work_id: str
    lane: str
    action: str
    required_checkpoints: tuple[str, ...]
    sandbox_dir: Path
    post_manifest: Mapping[str, Mapping[str, Any]]
    changed_paths: tuple[str, ...]
    required_output_paths: tuple[str, ...] = ()
    evaluator: bool = False
    finalizer: bool = False


@dataclass(frozen=True)
class ValidatedRoleResult:
    status: str
    checkpoints: tuple[str, ...]
    blockers: tuple[dict[str, Any], ...]
    artifacts: tuple[ArtifactRecord, ...]
    verdict: dict[str, Any] | None


def validate_role_result_payload(
    payload: object,
    context: RoleResultContext,
) -> tuple[ValidatedRoleResult | None, list[dict[str, Any]]]:
    if not isinstance(payload, dict):
        return None, [_blocker("role-result-schema-invalid", "Role result must be a JSON object.")]

    missing = [field for field in REQUIRED_ROLE_RESULT_FIELDS if field not in payload]
    if missing:
        return None, [
            _blocker(
                "role-result-schema-invalid",
                "Role result omitted required fields.",
                details={"missing": missing},
            )
        ]

    identity_errors = _identity_errors(payload, context)
    if identity_errors:
        return None, identity_errors

    status = payload.get("status")
    if status not in ROLE_RESULT_STATUSES:
        return None, [
            _blocker(
                "role-result-status-invalid",
                f"Unsupported role status: {status!r}.",
                details={"allowed": sorted(ROLE_RESULT_STATUSES), "actual": status},
            )
        ]

    checkpoints = _checkpoint_list(payload.get("checkpoints"))
    missing_checkpoints = sorted(set(context.required_checkpoints) - set(checkpoints))
    if missing_checkpoints:
        return None, [
            _blocker(
                "role-result-checkpoint-missing",
                "Role result omitted mandatory checkpoints.",
                category="process",
                details={"missing": missing_checkpoints},
            )
        ]

    artifacts, artifact_errors = _validate_artifacts(
        payload.get("artifacts"),
        context=context,
    )
    if artifact_errors:
        return None, artifact_errors

    checkpoint_errors = _validate_checkpoint_evidence(
        payload.get("checkpoint_evidence"),
        context=context,
        artifacts=artifacts,
        status=str(status),
    )
    if checkpoint_errors:
        return None, checkpoint_errors

    blockers, blocker_errors = _validate_blockers(payload.get("blockers"))
    if blocker_errors:
        return None, blocker_errors

    return (
        ValidatedRoleResult(
            status=str(status),
            checkpoints=tuple(context.required_checkpoints),
            blockers=tuple(blockers),
            artifacts=tuple(artifacts),
            verdict=payload.get("verdict") if isinstance(payload.get("verdict"), dict) else None,
        ),
        [],
    )


def _identity_errors(payload: dict[str, Any], context: RoleResultContext) -> list[dict[str, Any]]:
    expected = {
        "version": ROLE_RESULT_VERSION,
        "workflow_id": context.workflow_id,
        "role_run_id": context.expected_role_run_id,
        "role_id": context.role_id,
        "work_id": context.work_id,
        "lane": context.lane,
        "action": context.action,
    }
    mismatches = {
        key: {"expected": value, "actual": payload.get(key)}
        for key, value in expected.items()
        if payload.get(key) != value
    }
    if not mismatches:
        return []
    return [
        _blocker(
            "role-result-identity-mismatch",
            "Role result identity does not match the selected workflow/work.",
            repairable=False,
            details=mismatches,
        )
    ]


def _checkpoint_list(raw_checkpoints: object) -> list[str]:
    if not isinstance(raw_checkpoints, list):
        return []
    return [item for item in raw_checkpoints if isinstance(item, str)]


def _validate_artifacts(
    raw_artifacts: object,
    *,
    context: RoleResultContext,
) -> tuple[list[ArtifactRecord], list[dict[str, Any]]]:
    if not isinstance(raw_artifacts, list):
        return [], [_blocker("role-result-artifacts-invalid", "Role artifacts must be a JSON list.")]

    records: list[ArtifactRecord] = []
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_artifacts:
        if not isinstance(item, dict):
            errors.append(_blocker("role-result-artifacts-invalid", "Artifact entry must be an object."))
            continue
        path = _sandbox_relative_path(item.get("path"), context.sandbox_dir)
        sha256 = str(item.get("sha256") or "").strip().casefold()
        if path is None or not re.fullmatch(r"[0-9a-f]{64}", sha256):
            errors.append(_blocker("role-result-artifacts-invalid", "Artifact entry has invalid path or SHA-256."))
            continue
        actual = context.post_manifest.get(path)
        if actual is None or actual.get("sha256") != sha256:
            errors.append(
                _blocker(
                    "role-result-artifact-hash-mismatch",
                    f"Reported artifact hash does not match sandbox post-manifest: {path}.",
                    category="artifact",
                    repairable=False,
                )
            )
            continue
        if path in seen:
            continue
        seen.add(path)
        records.append(
            ArtifactRecord(
                path=path,
                sha256=str(actual["sha256"]),
                size=int(actual["size"]),
                media_type=_media_type(Path(path)),
            )
        )

    changed_files = {path for path in context.changed_paths if path in context.post_manifest}
    missing_changed = sorted(changed_files - seen)
    if missing_changed:
        errors.append(
            _blocker(
                "role-result-artifact-manifest-incomplete",
                "Role result omitted created or modified artifacts.",
                category="artifact",
                details={"paths": missing_changed},
            )
        )
    return records, errors


def _validate_checkpoint_evidence(
    raw_evidence: object,
    *,
    context: RoleResultContext,
    artifacts: list[ArtifactRecord],
    status: str,
) -> list[dict[str, Any]]:
    if not context.required_checkpoints:
        return []
    if not isinstance(raw_evidence, dict):
        code = "role-result-success-without-evidence" if status == "succeeded" else "role-result-checkpoint-evidence-missing"
        return [_blocker(code, "Checkpoint evidence must be an object.", category="process")]

    artifact_paths = {item.path for item in artifacts}
    missing: list[str] = []
    invalid: dict[str, list[str]] = {}
    for checkpoint in context.required_checkpoints:
        raw_paths = raw_evidence.get(checkpoint)
        paths = [item for item in raw_paths if isinstance(item, str)] if isinstance(raw_paths, list) else []
        if not paths:
            missing.append(checkpoint)
            continue
        absent = [path for path in paths if path not in artifact_paths]
        if absent:
            invalid[checkpoint] = absent

    if not missing and not invalid:
        return []

    code = "role-result-success-without-evidence" if status == "succeeded" else "role-result-checkpoint-evidence-invalid"
    return [
        _blocker(
            code,
            "Mandatory checkpoints lack hash-verified artifact evidence.",
            category="process",
            details={"missing": missing, "unverified_paths": invalid},
        )
    ]


def _validate_blockers(raw_blockers: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(raw_blockers, list):
        return [], [_blocker("role-result-blocker-schema-invalid", "Role blockers must be a JSON list.")]
    return [dict(item) for item in raw_blockers if isinstance(item, dict)], []


def _sandbox_relative_path(raw_path: object, sandbox_dir: Path) -> str | None:
    text = str(raw_path or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_absolute():
        try:
            return path.resolve().relative_to(sandbox_dir.resolve()).as_posix()
        except ValueError:
            return None
    candidate = (sandbox_dir / path).resolve()
    try:
        return candidate.relative_to(sandbox_dir.resolve()).as_posix()
    except ValueError:
        return None


def _media_type(path: Path) -> str:
    return {
        ".md": "text/markdown",
        ".json": "application/json",
        ".toml": "application/toml",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(path.suffix.casefold(), "application/octet-stream")


def _blocker(
    code: str,
    message: str,
    *,
    category: str = "runtime",
    repairable: bool = True,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": category,
        "code": code,
        "message": message,
        "repairable": repairable,
        "blocks_statuses": ["submission-ready"],
    }
    if details:
        payload["details"] = details
    return payload
