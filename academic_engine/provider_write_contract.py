from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

PROVIDER_WRITE_PLAN_VERSION = "provider-write-plan/v1"
MAX_PROVIDER_WRITE_PLAN_BYTES = 1024 * 1024
_PLAN_FIELDS = {
    "version",
    "workflow_id",
    "role_run_id",
    "role_id",
    "work_id",
    "operations",
}
_OPERATION_FIELDS = {"path", "base_sha256", "content"}
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FENCED_PLAN_PATTERN = re.compile(
    r"```[ \t]*provider-write-plan[ \t]*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


@dataclass(frozen=True)
class ProviderWriteOperation:
    path: str
    base_sha256: str
    content: str


@dataclass(frozen=True)
class ProviderWritePlan:
    workflow_id: str
    role_run_id: str
    role_id: str
    work_id: str
    operations: tuple[ProviderWriteOperation, ...]


@dataclass(frozen=True)
class ProviderWritePlanContext:
    """Trusted engine context; allowed scopes are sandbox-relative paths only."""

    workflow_id: str
    role_run_id: str
    role_id: str
    work_id: str
    sandbox_dir: Path
    allowed_write_scopes: tuple[str, ...]
    pre_write_manifest: Mapping[str, Mapping[str, Any]]


def parse_provider_write_plan(text: object) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Parse one strict fenced provider-write-plan payload without applying it."""
    if not isinstance(text, str):
        return None, [_blocker("provider-write-plan-schema-invalid", "Provider write plan must be text.")]
    if _encoded_size(text) > MAX_PROVIDER_WRITE_PLAN_BYTES:
        return None, [
            _blocker(
                "provider-write-plan-payload-too-large",
                "Provider write plan exceeds the maximum payload size.",
            )
        ]

    matches = list(_FENCED_PLAN_PATTERN.finditer(text))
    if not matches:
        return None, [_blocker("provider-write-plan-block-missing", "Provider returned no provider-write-plan block.")]
    if len(matches) != 1:
        return None, [
            _blocker(
                "provider-write-plan-block-count-invalid",
                f"Expected exactly one provider-write-plan block, found {len(matches)}.",
            )
        ]

    body = matches[0].group("body")
    if _encoded_size(body) > MAX_PROVIDER_WRITE_PLAN_BYTES:
        return None, [
            _blocker(
                "provider-write-plan-payload-too-large",
                "Provider write plan exceeds the maximum payload size.",
            )
        ]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        return None, [_blocker("provider-write-plan-json-invalid", f"Provider write plan is not valid JSON: {exc}.")]

    schema_error = _schema_error(payload)
    if schema_error is not None:
        return None, [schema_error]
    assert isinstance(payload, dict)
    return payload, []


def validate_provider_write_plan(
    payload: object,
    context: ProviderWritePlanContext,
) -> tuple[ProviderWritePlan | None, list[dict[str, Any]]]:
    """Validate a parsed plan against one trusted sandbox and pre-write manifest."""
    schema_error = _schema_error(payload)
    if schema_error is not None:
        return None, [schema_error]
    assert isinstance(payload, dict)
    if _serialized_size(payload) > MAX_PROVIDER_WRITE_PLAN_BYTES:
        return None, [
            _blocker(
                "provider-write-plan-payload-too-large",
                "Provider write plan exceeds the maximum payload size.",
            )
        ]

    identity = {
        "workflow_id": context.workflow_id,
        "role_run_id": context.role_run_id,
        "role_id": context.role_id,
        "work_id": context.work_id,
    }
    mismatched = sorted(field for field, expected in identity.items() if payload[field] != expected)
    if mismatched:
        return None, [
            _blocker(
                "provider-write-plan-identity-invalid",
                "Provider write plan identity does not match the active role run.",
                details={"fields": mismatched},
            )
        ]

    allowed = _normalized_scopes(context.allowed_write_scopes, context.sandbox_dir)
    operations: list[ProviderWriteOperation] = []
    seen_paths: set[str] = set()
    for raw_operation in payload["operations"]:
        assert isinstance(raw_operation, dict)
        raw_path = str(raw_operation["path"])
        path = _sandbox_relative_path(raw_path, context.sandbox_dir)
        if path is None or not _path_is_allowed(path, allowed):
            return None, [
                _blocker(
                    "provider-write-path-forbidden",
                    "Provider write plan path is outside the allowed sandbox scopes.",
                    details={"path": raw_path},
                )
            ]
        if path in seen_paths:
            return None, [
                _blocker(
                    "provider-write-path-duplicate",
                    "Provider write plan includes the same path more than once.",
                    details={"path": path},
                )
            ]
        seen_paths.add(path)

        actual = context.pre_write_manifest.get(path)
        expected_sha256 = str(actual.get("sha256", "")) if actual is not None else ""
        live_sha256 = _file_sha256(context.sandbox_dir / path)
        if raw_operation["base_sha256"] != expected_sha256 or raw_operation["base_sha256"] != live_sha256:
            return None, [
                _blocker(
                    "provider-write-base-hash-mismatch",
                    "Provider write plan base hash does not match the sandbox manifest.",
                    details={"path": path},
                )
            ]
        operations.append(
            ProviderWriteOperation(
                path=path,
                base_sha256=str(raw_operation["base_sha256"]),
                content=str(raw_operation["content"]),
            )
        )

    return ProviderWritePlan(
        workflow_id=str(payload["workflow_id"]),
        role_run_id=str(payload["role_run_id"]),
        role_id=str(payload["role_id"]),
        work_id=str(payload["work_id"]),
        operations=tuple(operations),
    ), []


def _schema_error(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return _blocker("provider-write-plan-schema-invalid", "Provider write plan must be a JSON object.")
    missing = sorted(_PLAN_FIELDS - set(payload))
    unexpected = sorted(str(field) for field in payload if field not in _PLAN_FIELDS)
    if missing or unexpected:
        return _blocker(
            "provider-write-plan-schema-invalid",
            "Provider write plan has unsupported fields.",
            details={"missing": missing, "unexpected": unexpected},
        )
    if payload["version"] != PROVIDER_WRITE_PLAN_VERSION:
        return _blocker(
            "provider-write-plan-schema-invalid",
            "Provider write plan version is not supported.",
            details={"version": payload["version"]},
        )
    for field in ("workflow_id", "role_run_id", "role_id", "work_id"):
        if not isinstance(payload[field], str) or not payload[field]:
            return _blocker(
                "provider-write-plan-schema-invalid",
                "Provider write plan identity fields must be non-empty strings.",
                details={"field": field},
            )
    operations = payload["operations"]
    if not isinstance(operations, list) or not operations:
        return _blocker(
            "provider-write-plan-schema-invalid",
            "Provider write plan operations must be a non-empty list.",
        )
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            return _blocker(
                "provider-write-plan-schema-invalid",
                "Provider write plan operation must be a JSON object.",
                details={"index": index},
            )
        missing_fields = sorted(_OPERATION_FIELDS - set(operation))
        unexpected_fields = sorted(str(field) for field in operation if field not in _OPERATION_FIELDS)
        if missing_fields or unexpected_fields:
            return _blocker(
                "provider-write-plan-schema-invalid",
                "Provider write plan operation has unsupported fields.",
                details={"index": index, "missing": missing_fields, "unexpected": unexpected_fields},
            )
        if not isinstance(operation["path"], str) or not operation["path"]:
            return _blocker(
                "provider-write-plan-schema-invalid",
                "Provider write plan operation path must be a non-empty string.",
                details={"index": index},
            )
        if not isinstance(operation["base_sha256"], str) or not _SHA256_PATTERN.fullmatch(operation["base_sha256"]):
            return _blocker(
                "provider-write-plan-schema-invalid",
                "Provider write plan base hash must be a 64-character lowercase SHA-256.",
                details={"index": index},
            )
        if not isinstance(operation["content"], str):
            return _blocker(
                "provider-write-plan-schema-invalid",
                "Provider write plan replacement content must be text.",
                details={"index": index},
            )
    return None


def _normalized_scopes(scopes: tuple[str, ...], sandbox_dir: Path) -> tuple[str, ...]:
    normalized: list[str] = []
    for scope in scopes:
        candidate = _sandbox_relative_path(scope, sandbox_dir)
        if candidate is not None:
            normalized.append(candidate)
    return tuple(dict.fromkeys(normalized))


def _sandbox_relative_path(raw_path: str, sandbox_dir: Path) -> str | None:
    if "\\" in raw_path:
        return None
    path = PurePosixPath(raw_path)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        return None
    root = sandbox_dir.resolve()
    candidate = sandbox_dir.joinpath(*path.parts)
    cursor = sandbox_dir
    for part in path.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        candidate.resolve(strict=False).relative_to(root)
    except (OSError, ValueError):
        return None
    return path.as_posix()


def _path_is_allowed(path: str, allowed_scopes: tuple[str, ...]) -> bool:
    candidate = PurePosixPath(path)
    return any(
        candidate == PurePosixPath(scope) or PurePosixPath(scope) in candidate.parents
        for scope in allowed_scopes
    )


def _encoded_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _serialized_size(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _file_sha256(path: Path) -> str | None:
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _blocker(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "category": "artifact",
        "code": code,
        "message": message,
        "repairable": False,
        "blocks_statuses": ["submission-ready"],
    }
    if details:
        payload["details"] = details
    return payload
