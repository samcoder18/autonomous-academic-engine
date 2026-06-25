from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .verdict_parser import extract_structured_verdicts

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
ROLE_RESULT_FIELD_SET = set(REQUIRED_ROLE_RESULT_FIELDS)
ARTIFACT_FIELDS = {"path", "sha256"}
BLOCKER_FIELDS = {"category", "code", "message", "repairable", "blocks_statuses", "details"}
ALLOWED_BLOCKER_CATEGORIES = {
    "artifact",
    "citation",
    "codex",
    "docx-conformance",
    "dynamic-material",
    "external",
    "gost-bibliography",
    "logic",
    "originality",
    "primary-support",
    "process",
    "review",
    "runtime",
    "standards",
    "standards-consistency",
    "verdict",
    "verification",
}
BLOCKER_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
EVALUATOR_ROLE_IDS = {"thesis-submission-evaluator", "academic-submission-evaluator"}
EVIDENCE_ROLE_IDS = {
    "thesis-research-synthesizer",
    "thesis-source-verifier",
    "thesis-citation-checker",
    "academic-source-acquirer",
    "academic-source-verifier",
    "academic-evidence-cartographer",
    "academic-citation-checker",
}
EVIDENCE_BLOCKER_CATEGORIES = {
    "citation",
    "dynamic-material",
    "primary-support",
    "process",
    "verification",
}
FINALIZER_ROLE_IDS = {"academic-finalizer"}


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
    unexpected = sorted(str(field) for field in payload if field not in ROLE_RESULT_FIELD_SET)
    if unexpected:
        return None, [
            _blocker(
                "role-result-schema-invalid",
                "Role result included unsupported fields.",
                details={"unexpected": unexpected},
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
    if str(status) == "succeeded" and (
        not checkpoints or not _has_any_checkpoint_evidence(payload.get("checkpoint_evidence"))
    ):
        return None, [
            _blocker(
                "role-result-success-without-evidence",
                "Successful role result must include checkpoint evidence.",
                category="process",
            )
        ]

    blockers, blocker_errors = _validate_blockers(payload.get("blockers"))
    if blocker_errors:
        return None, blocker_errors

    status_errors = _validate_status_blocker_consistency(str(status), blockers)
    if status_errors:
        return None, status_errors

    verdict, verdict_blockers, verdict_errors = _validate_verdict(payload.get("verdict"), context=context)
    if verdict_errors:
        return None, verdict_errors
    blockers.extend(verdict_blockers)

    role_errors = _validate_role_specific_contract(
        status=str(status),
        context=context,
        blockers=blockers,
        artifacts=artifacts,
        checkpoint_evidence=payload.get("checkpoint_evidence"),
    )
    if role_errors:
        return None, role_errors

    return (
        ValidatedRoleResult(
            status=str(status),
            checkpoints=tuple(checkpoints),
            blockers=tuple(blockers),
            artifacts=tuple(artifacts),
            verdict=verdict,
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
        unexpected = sorted(str(field) for field in item if field not in ARTIFACT_FIELDS)
        if unexpected:
            errors.append(
                _blocker(
                    "role-result-artifacts-invalid",
                    "Artifact entry included unsupported fields.",
                    details={"unexpected": unexpected},
                )
            )
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
        code = (
            "role-result-success-without-evidence"
            if status == "succeeded"
            else "role-result-checkpoint-evidence-missing"
        )
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

    code = (
        "role-result-success-without-evidence" if status == "succeeded" else "role-result-checkpoint-evidence-invalid"
    )
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

    blockers: list[dict[str, Any]] = []
    for item in raw_blockers:
        if not isinstance(item, dict):
            return [], [_blocker("role-result-blocker-schema-invalid", "Role blocker must be an object.")]
        unexpected = sorted(str(field) for field in item if field not in BLOCKER_FIELDS)
        if unexpected:
            return [], [
                _blocker(
                    "role-result-blocker-schema-invalid",
                    "Role blocker included unsupported fields.",
                    details={"unexpected": unexpected},
                )
            ]

        raw_category = item.get("category")
        raw_code = item.get("code")
        raw_message = item.get("message")
        if not isinstance(raw_category, str) or not isinstance(raw_code, str) or not isinstance(raw_message, str):
            return [], [
                _blocker(
                    "role-result-blocker-schema-invalid",
                    "Role blocker must include string category, code, and message.",
                )
            ]

        category = raw_category.strip()
        code = raw_code.strip()
        message = raw_message.strip()
        if not category or not code or not message:
            return [], [
                _blocker(
                    "role-result-blocker-schema-invalid",
                    "Role blocker must include category, code, and message.",
                )
            ]
        if category not in ALLOWED_BLOCKER_CATEGORIES:
            return [], [
                _blocker(
                    "role-result-blocker-schema-invalid",
                    "Role blocker category is not in the allowed taxonomy.",
                    details={"category": category, "allowed": sorted(ALLOWED_BLOCKER_CATEGORIES)},
                )
            ]
        if not BLOCKER_CODE_PATTERN.fullmatch(code):
            return [], [
                _blocker(
                    "role-result-blocker-code-invalid",
                    "Role blocker code must be a stable lowercase machine code.",
                    details={"code": code},
                )
            ]

        repairable = item.get("repairable", True)
        if "repairable" in item and not isinstance(repairable, bool):
            return [], [
                _blocker(
                    "role-result-blocker-schema-invalid",
                    "Role blocker repairable flag must be boolean.",
                )
            ]

        normalized: dict[str, Any] = {
            "category": category,
            "code": code,
            "message": message,
            "repairable": repairable,
        }
        if "blocks_statuses" in item:
            raw_statuses = item.get("blocks_statuses")
            if not isinstance(raw_statuses, list):
                return [], [
                    _blocker(
                        "role-result-blocker-schema-invalid",
                        "Role blocker blocks_statuses must be a list of non-empty strings.",
                    )
                ]
            statuses: list[str] = []
            for value in raw_statuses:
                if not isinstance(value, str) or not value.strip():
                    return [], [
                        _blocker(
                            "role-result-blocker-schema-invalid",
                            "Role blocker blocks_statuses must be a list of non-empty strings.",
                        )
                    ]
                statuses.append(value.strip())
            normalized["blocks_statuses"] = statuses
        if "details" in item:
            details = item.get("details")
            if not isinstance(details, dict):
                return [], [
                    _blocker(
                        "role-result-blocker-schema-invalid",
                        "Role blocker details must be an object.",
                    )
                ]
            if details:
                normalized["details"] = details
        blockers.append(normalized)

    return blockers, []


def _validate_status_blocker_consistency(status: str, blockers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if status == "succeeded" and blockers:
        return [
            _blocker(
                "role-result-success-with-blockers",
                "Successful role result cannot carry blockers.",
                category="process",
                details={"blocker_codes": [item.get("code") for item in blockers]},
            )
        ]
    if status == "blocked" and not blockers:
        return [
            _blocker(
                "role-result-blocked-without-blockers",
                "Blocked role result must include at least one structured blocker.",
                category="process",
            )
        ]
    if status == "failed" and not blockers:
        return [
            _blocker(
                "role-result-failed-without-blockers",
                "Failed role result must include at least one structured blocker.",
                category="process",
            )
        ]
    return []


def _validate_verdict(
    raw_verdict: object,
    *,
    context: RoleResultContext,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    requires_verdict = context.evaluator or context.role_id in EVALUATOR_ROLE_IDS
    if raw_verdict is None:
        if requires_verdict:
            return (
                None,
                [],
                [
                    _blocker(
                        "role-result-evaluator-verdict-missing",
                        "Independent evaluator did not report a structured verdict.",
                        category="verdict",
                    )
                ],
            )
        return None, [], []

    if not isinstance(raw_verdict, dict):
        return (
            None,
            [],
            [
                _blocker(
                    "role-result-evaluator-verdict-invalid"
                    if requires_verdict
                    else "role-result-role-contract-invalid",
                    "Reported verdict must be an object or null.",
                    category="verdict",
                )
            ],
        )

    verdicts, errors = extract_structured_verdicts(
        {"role-result": f"```verdict\n{json.dumps(raw_verdict, ensure_ascii=False)}\n```"}
    )
    if errors:
        return (
            None,
            [],
            [
                _blocker(
                    "role-result-evaluator-verdict-invalid"
                    if requires_verdict
                    else "role-result-role-contract-invalid",
                    "Reported verdict does not match the role/lane contract.",
                    category="verdict",
                    details={"errors": [error.code for error in errors]},
                )
            ],
        )

    candidates = [item for item in verdicts if item.lane == context.lane]
    if requires_verdict:
        candidates = [item for item in candidates if item.kind == "submission-evaluator"]
    if len(candidates) != 1:
        return (
            None,
            [],
            [
                _blocker(
                    "role-result-evaluator-verdict-invalid"
                    if requires_verdict
                    else "role-result-role-contract-invalid",
                    "Reported verdict does not match the role/lane contract.",
                    category="verdict",
                )
            ],
        )

    verdict = candidates[0]
    return verdict.to_dict(), [item.to_dict() for item in verdict.blockers], []


def _validate_role_specific_contract(
    *,
    status: str,
    context: RoleResultContext,
    blockers: list[dict[str, Any]],
    artifacts: list[ArtifactRecord],
    checkpoint_evidence: object,
) -> list[dict[str, Any]]:
    if status != "succeeded":
        evidence_errors = _validate_evidence_role_blockers(context=context, blockers=blockers)
        return evidence_errors

    if context.finalizer or context.role_id in FINALIZER_ROLE_IDS:
        required_outputs = set(context.required_output_paths)
        artifact_paths = {artifact.path for artifact in artifacts}
        if required_outputs and not artifact_paths.intersection(required_outputs):
            return [
                _blocker(
                    "role-result-finalizer-artifact-missing",
                    "Successful finalizer result must include at least one required finalization artifact.",
                    category="artifact",
                    details={
                        "required_output_paths": sorted(required_outputs),
                        "artifact_paths": sorted(artifact_paths),
                    },
                )
            ]

    if context.role_id in EVIDENCE_ROLE_IDS and not _has_any_checkpoint_evidence(checkpoint_evidence):
        return [
            _blocker(
                "role-result-success-without-evidence",
                "Successful evidence role result must include checkpoint evidence.",
                category="process",
            )
        ]

    return []


def _validate_evidence_role_blockers(
    *,
    context: RoleResultContext,
    blockers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if context.role_id not in EVIDENCE_ROLE_IDS:
        return []
    invalid = [item for item in blockers if item.get("category") not in EVIDENCE_BLOCKER_CATEGORIES]
    if not invalid:
        return []
    return [
        _blocker(
            "role-result-role-contract-invalid",
            "Evidence role blockers must use evidence-support categories.",
            category="process",
            details={
                "invalid_categories": sorted(
                    {item["category"] for item in invalid if isinstance(item.get("category"), str)}
                ),
                "allowed_categories": sorted(EVIDENCE_BLOCKER_CATEGORIES),
            },
        )
    ]


def _has_any_checkpoint_evidence(raw_evidence: object) -> bool:
    if not isinstance(raw_evidence, dict):
        return False
    for value in raw_evidence.values():
        if isinstance(value, list) and any(isinstance(item, str) and item.strip() for item in value):
            return True
    return False


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
