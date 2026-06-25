# Role Result Contract Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden `role-result/v1` acceptance with a dependency-free contract validator, documented JSON Schema, role-specific requirements, evidence-backed success, and stable error codes.

**Architecture:** Add `academic_engine.role_result_contract` as the executable contract boundary for role-result payloads. Keep `WorkflowEngine` as the only runtime authority that extracts fenced result blocks, supplies trusted context, and accepts normalized validated results for promotion decisions. Store `meta/schemas/role-result.schema.json` as documentation for agents and operators; runtime validation remains standard-library only.

**Tech Stack:** Python 3.11 standard library, `unittest`, existing `academic_engine.workflow_engine`, existing `academic_engine.verdict_parser`, JSON Schema documentation only.

---

## File Structure

- Create: `academic_engine/role_result_contract.py`
  - Public constants for `role-result/v1`, allowed statuses, role groups, blocker categories, and stable error codes.
  - `ArtifactRecord`, `RoleResultContext`, and `ValidatedRoleResult` dataclasses.
  - `validate_role_result_payload(payload, context)` for dependency-free validation.
  - Path, artifact, checkpoint-evidence, blocker, verdict, and role-specific validation helpers.
- Create: `tests/test_role_result_contract.py`
  - Unit tests for the contract validator.
  - Uses synthetic manifests and trusted context instead of running workflows.
- Create: `meta/schemas/role-result.schema.json`
  - JSON Schema documentation for `role-result/v1`.
  - Mirrors the runtime contract without introducing a `jsonschema` dependency.
- Modify: `meta/README.md`
  - List the new role-result schema beside the existing verdict schema.
- Modify: `academic_engine/workflow_engine.py`
  - Import contract dataclasses and validation function.
  - Keep fenced block extraction in `WorkflowEngine`.
  - Replace inline role-result payload validation with `validate_role_result_payload`.
  - Pass `ExecutionContract`-derived required output paths into the trusted validation context.
  - Update prompt instructions to show stable error-code expectations.
- Modify: `tests/test_workflow_engine.py`
  - Update existing fail-closed assertions to the new stable codes.
  - Add one integration assertion that `WorkflowEngine` rejects a successful result carrying blockers.

---

### Task 1: Add RED Base Contract Tests

**Files:**
- Create: `tests/test_role_result_contract.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_role_result_contract.py` with this content:

```python
from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

from academic_engine.role_result_contract import (
    ROLE_RESULT_VERSION,
    RoleResultContext,
    validate_role_result_payload,
)


HASH = "a" * 64
ARTIFACT_PATH = "works/demo/thesis/reviews/role-result.md"


class RoleResultContractBaseTests(unittest.TestCase):
    def test_valid_base_role_result_is_accepted(self) -> None:
        result, blockers = validate_role_result_payload(_valid_payload(), _context())

        self.assertEqual(blockers, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.checkpoints, ("context-loaded", "completed"))
        self.assertEqual([artifact.path for artifact in result.artifacts], [ARTIFACT_PATH])

    def test_missing_required_field_fails_schema_contract(self) -> None:
        payload = _valid_payload()
        del payload["version"]

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-schema-invalid", _codes(blockers))
        self.assertEqual(blockers[0]["details"]["missing"], ["version"])

    def test_unsupported_status_has_specific_code(self) -> None:
        payload = _valid_payload(status="finished")

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-status-invalid", _codes(blockers))


def _context(
    *,
    role_id: str = "thesis-style-editor",
    lane: str = "thesis",
    action: str = "style-pass",
    checkpoints: tuple[str, ...] = ("context-loaded", "completed"),
    artifact_path: str = ARTIFACT_PATH,
    changed_paths: tuple[str, ...] | None = None,
    evaluator: bool = False,
    finalizer: bool = False,
    required_output_paths: tuple[str, ...] = (),
) -> RoleResultContext:
    paths = changed_paths if changed_paths is not None else (artifact_path,)
    return RoleResultContext(
        workflow_id="workflow-1",
        expected_role_run_id=f"01-{role_id}",
        role_id=role_id,
        work_id="demo",
        lane=lane,
        action=action,
        required_checkpoints=checkpoints,
        sandbox_dir=Path("/tmp/role-result-contract-sandbox"),
        post_manifest={artifact_path: {"sha256": HASH, "size": 12}},
        changed_paths=paths,
        required_output_paths=required_output_paths,
        evaluator=evaluator,
        finalizer=finalizer,
    )


def _valid_payload(
    *,
    role_id: str = "thesis-style-editor",
    lane: str = "thesis",
    action: str = "style-pass",
    status: str = "succeeded",
    artifact_path: str = ARTIFACT_PATH,
    checkpoints: tuple[str, ...] = ("context-loaded", "completed"),
    checkpoint_evidence: dict[str, list[str]] | None = None,
    blockers: list[dict[str, Any]] | None = None,
    verdict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = checkpoint_evidence
    if evidence is None:
        evidence = {checkpoint: [artifact_path] for checkpoint in checkpoints}
    return {
        "version": ROLE_RESULT_VERSION,
        "workflow_id": "workflow-1",
        "role_run_id": f"01-{role_id}",
        "role_id": role_id,
        "work_id": "demo",
        "lane": lane,
        "action": action,
        "status": status,
        "checkpoints": list(checkpoints),
        "checkpoint_evidence": evidence,
        "blockers": blockers or [],
        "artifacts": [{"path": artifact_path, "sha256": HASH}],
        "verdict": verdict,
    }


def _codes(blockers: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("code")) for item in blockers}


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_role_result_contract -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'academic_engine.role_result_contract'`.

- [ ] **Step 3: Commit RED tests**

Run:

```bash
git add tests/test_role_result_contract.py
git commit -m "test: cover base role result contract"
```

---

### Task 2: Add Schema Document And Minimal Contract Module

**Files:**
- Create: `academic_engine/role_result_contract.py`
- Create: `meta/schemas/role-result.schema.json`
- Modify: `meta/README.md`

- [ ] **Step 1: Add schema documentation**

Create `meta/schemas/role-result.schema.json` with this content:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://legal-academic-workspace.local/schemas/role-result.schema.json",
  "title": "Role Result v1",
  "description": "Machine-readable result emitted by workflow roles via fenced role-result JSON block. Runtime validation is dependency-free in academic_engine.role_result_contract.",
  "type": "object",
  "required": [
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
    "verdict"
  ],
  "additionalProperties": false,
  "properties": {
    "version": { "type": "string", "enum": ["role-result/v1"] },
    "workflow_id": { "type": "string", "minLength": 1 },
    "role_run_id": { "type": "string", "minLength": 1 },
    "role_id": { "type": "string", "minLength": 1 },
    "work_id": { "type": "string", "minLength": 1 },
    "lane": { "type": "string", "enum": ["thesis", "article"] },
    "action": { "type": "string", "minLength": 1 },
    "status": { "type": "string", "enum": ["succeeded", "blocked", "failed"] },
    "checkpoints": {
      "type": "array",
      "items": { "type": "string", "minLength": 1 },
      "uniqueItems": true
    },
    "checkpoint_evidence": {
      "type": "object",
      "additionalProperties": {
        "type": "array",
        "items": { "type": "string", "minLength": 1 },
        "minItems": 1
      }
    },
    "blockers": {
      "type": "array",
      "items": { "$ref": "#/$defs/blocker" }
    },
    "artifacts": {
      "type": "array",
      "items": { "$ref": "#/$defs/artifact" }
    },
    "verdict": {
      "type": ["object", "null"]
    }
  },
  "$defs": {
    "artifact": {
      "type": "object",
      "required": ["path", "sha256"],
      "additionalProperties": false,
      "properties": {
        "path": { "type": "string", "minLength": 1 },
        "sha256": { "type": "string", "pattern": "^[0-9a-f]{64}$" }
      }
    },
    "blocker": {
      "type": "object",
      "required": ["category", "code", "message"],
      "additionalProperties": false,
      "properties": {
        "category": {
          "type": "string",
          "enum": [
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
            "verification"
          ]
        },
        "code": { "type": "string", "pattern": "^[a-z0-9][a-z0-9-]*$" },
        "message": { "type": "string", "minLength": 1 },
        "repairable": { "type": "boolean" },
        "blocks_statuses": {
          "type": "array",
          "items": { "type": "string", "minLength": 1 }
        },
        "details": { "type": "object" }
      }
    }
  }
}
```

- [ ] **Step 2: Update schema index**

Modify `meta/README.md` under the `schemas/` list so it reads:

```markdown
- [schemas/](schemas) — JSON-схемы для runtime артефактов:
  - [schemas/verdict.schema.json](schemas/verdict.schema.json) — контракт для structured verdict-блоков evaluator'ов.
  - [schemas/role-result.schema.json](schemas/role-result.schema.json) — контракт для fenced `role-result/v1` блоков workflow roles.
```

- [ ] **Step 3: Add minimal contract module**

Create `academic_engine/role_result_contract.py` with this content:

```python
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
```

- [ ] **Step 4: Run base contract tests**

Run:

```bash
python3 -m unittest tests.test_role_result_contract -q
```

Expected: PASS.

- [ ] **Step 5: Commit base implementation**

Run:

```bash
git add academic_engine/role_result_contract.py meta/schemas/role-result.schema.json meta/README.md
git commit -m "feat: add role result contract validator"
```

---

### Task 3: Add RED Status And Blocker Semantics Tests

**Files:**
- Modify: `tests/test_role_result_contract.py`

- [ ] **Step 1: Add failing tests for evidence-backed success and blocker status rules**

Insert this test class above the helper functions in `tests/test_role_result_contract.py`:

```python
class RoleResultContractStatusTests(unittest.TestCase):
    def test_succeeded_requires_checkpoint_evidence(self) -> None:
        payload = _valid_payload(checkpoint_evidence={})

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-success-without-evidence", _codes(blockers))

    def test_succeeded_cannot_carry_blockers(self) -> None:
        payload = _valid_payload(
            blockers=[
                {
                    "category": "citation",
                    "code": "citation-gap",
                    "message": "Citation support remains incomplete.",
                    "repairable": True,
                }
            ]
        )

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-success-with-blockers", _codes(blockers))

    def test_blocked_requires_blockers(self) -> None:
        payload = _valid_payload(status="blocked")

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-blocked-without-blockers", _codes(blockers))

    def test_failed_requires_blockers(self) -> None:
        payload = _valid_payload(status="failed")

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-failed-without-blockers", _codes(blockers))

    def test_blocker_code_must_be_stable_machine_code(self) -> None:
        payload = _valid_payload(
            status="blocked",
            blockers=[
                {
                    "category": "citation",
                    "code": "Citation Gap",
                    "message": "Citation support remains incomplete.",
                    "repairable": True,
                }
            ],
        )

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-blocker-code-invalid", _codes(blockers))
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_role_result_contract.RoleResultContractStatusTests -q
```

Expected: FAIL because successful results with blockers are accepted, blocked/failed results without blockers are accepted, and blocker codes are not validated yet.

- [ ] **Step 3: Commit RED tests**

Run:

```bash
git add tests/test_role_result_contract.py
git commit -m "test: cover role result status semantics"
```

---

### Task 4: Implement Status And Blocker Semantics

**Files:**
- Modify: `academic_engine/role_result_contract.py`

- [ ] **Step 1: Add blocker taxonomy constants**

Add these constants below `REQUIRED_ROLE_RESULT_FIELDS`:

```python
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
```

- [ ] **Step 2: Enforce status/blocker consistency**

In `validate_role_result_payload`, immediately after the `_validate_blockers(...)` block, replace the current return path with:

```python
    status_errors = _validate_status_blocker_consistency(str(status), blockers)
    if status_errors:
        return None, status_errors

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
```

Add this helper below `_validate_blockers`:

```python
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
```

- [ ] **Step 3: Replace blocker validation**

Replace `_validate_blockers` with:

```python
def _validate_blockers(raw_blockers: object) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(raw_blockers, list):
        return [], [_blocker("role-result-blocker-schema-invalid", "Role blockers must be a JSON list.")]

    blockers: list[dict[str, Any]] = []
    for item in raw_blockers:
        if not isinstance(item, dict):
            return [], [_blocker("role-result-blocker-schema-invalid", "Role blocker must be an object.")]

        category = str(item.get("category") or "").strip()
        code = str(item.get("code") or "").strip()
        message = str(item.get("message") or "").strip()
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

        normalized: dict[str, Any] = {
            "category": category,
            "code": code,
            "message": message,
            "repairable": bool(item.get("repairable", True)),
        }
        raw_statuses = item.get("blocks_statuses")
        if isinstance(raw_statuses, list):
            statuses = [str(value).strip() for value in raw_statuses if str(value).strip()]
            if statuses:
                normalized["blocks_statuses"] = statuses
        details = item.get("details")
        if isinstance(details, dict) and details:
            normalized["details"] = details
        blockers.append(normalized)

    return blockers, []
```

- [ ] **Step 4: Run status tests**

Run:

```bash
python3 -m unittest tests.test_role_result_contract.RoleResultContractStatusTests -q
```

Expected: PASS.

- [ ] **Step 5: Run all contract tests**

Run:

```bash
python3 -m unittest tests.test_role_result_contract -q
```

Expected: PASS.

- [ ] **Step 6: Commit status semantics**

Run:

```bash
git add academic_engine/role_result_contract.py
git commit -m "feat: enforce role result status semantics"
```

---

### Task 5: Add RED Role-Specific Tests

**Files:**
- Modify: `tests/test_role_result_contract.py`

- [ ] **Step 1: Add failing tests for evaluator, evidence, and finalizer roles**

Insert this class above the helper functions in `tests/test_role_result_contract.py`:

```python
class RoleResultContractRoleSpecificTests(unittest.TestCase):
    def test_evaluator_requires_structured_verdict(self) -> None:
        context = _context(
            role_id="thesis-submission-evaluator",
            evaluator=True,
        )
        payload = _valid_payload(role_id="thesis-submission-evaluator")

        result, blockers = validate_role_result_payload(payload, context)

        self.assertIsNone(result)
        self.assertIn("role-result-evaluator-verdict-missing", _codes(blockers))

    def test_evidence_role_blockers_use_evidence_taxonomy(self) -> None:
        context = _context(role_id="academic-source-verifier", lane="article", action="article")
        payload = _valid_payload(
            role_id="academic-source-verifier",
            lane="article",
            action="article",
            status="blocked",
            blockers=[
                {
                    "category": "logic",
                    "code": "logic-gap",
                    "message": "This is not an evidence-role blocker category.",
                    "repairable": True,
                }
            ],
        )

        result, blockers = validate_role_result_payload(payload, context)

        self.assertIsNone(result)
        self.assertIn("role-result-role-contract-invalid", _codes(blockers))

    def test_finalizer_requires_required_output_artifact(self) -> None:
        artifact_path = "works/demo/articles/final/unrelated.md"
        checklist_path = "works/demo/articles/final/demo-checklist.md"
        context = _context(
            role_id="academic-finalizer",
            lane="article",
            action="finalize",
            checkpoints=(
                "context-loaded",
                "finalizer-gates-checked",
                "checklist-updated",
                "docx-exported-or-skipped",
                "terminal-status-issued",
            ),
            artifact_path=artifact_path,
            finalizer=True,
            required_output_paths=(checklist_path,),
        )
        payload = _valid_payload(
            role_id="academic-finalizer",
            lane="article",
            action="finalize",
            artifact_path=artifact_path,
            checkpoints=(
                "context-loaded",
                "finalizer-gates-checked",
                "checklist-updated",
                "docx-exported-or-skipped",
                "terminal-status-issued",
            ),
        )

        result, blockers = validate_role_result_payload(payload, context)

        self.assertIsNone(result)
        self.assertIn("role-result-finalizer-artifact-missing", _codes(blockers))
```

- [ ] **Step 2: Run role-specific tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_role_result_contract.RoleResultContractRoleSpecificTests -q
```

Expected: FAIL because evaluator verdicts, evidence-role blocker categories, and finalizer required outputs are not checked yet.

- [ ] **Step 3: Commit RED role-specific tests**

Run:

```bash
git add tests/test_role_result_contract.py
git commit -m "test: cover role-specific result contracts"
```

---

### Task 6: Implement Role-Specific Validation

**Files:**
- Modify: `academic_engine/role_result_contract.py`

- [ ] **Step 1: Import verdict parser and add role group constants**

Modify the imports at the top of `academic_engine/role_result_contract.py`:

```python
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .verdict_parser import extract_structured_verdicts
```

Add these constants below `BLOCKER_CODE_PATTERN`:

```python
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
```

- [ ] **Step 2: Add role-specific validation call**

In `validate_role_result_payload`, after `status_errors` and before returning `ValidatedRoleResult`, add:

```python
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
            checkpoints=tuple(context.required_checkpoints),
            blockers=tuple(blockers),
            artifacts=tuple(artifacts),
            verdict=verdict,
        ),
        [],
    )
```

Remove the older return block that used `payload.get("verdict")` directly.

- [ ] **Step 3: Add verdict validation helper**

Add this helper below `_validate_status_blocker_consistency`:

```python
def _validate_verdict(
    raw_verdict: object,
    *,
    context: RoleResultContext,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    requires_verdict = context.evaluator or context.role_id in EVALUATOR_ROLE_IDS
    if raw_verdict is None:
        if requires_verdict:
            return None, [], [
                _blocker(
                    "role-result-evaluator-verdict-missing",
                    "Independent evaluator did not report a structured verdict.",
                    category="verdict",
                )
            ]
        return None, [], []

    if not isinstance(raw_verdict, dict):
        return None, [], [
            _blocker(
                "role-result-evaluator-verdict-invalid" if requires_verdict else "role-result-role-contract-invalid",
                "Reported verdict must be an object or null.",
                category="verdict",
            )
        ]

    verdicts, errors = extract_structured_verdicts(
        {"role-result": f"```verdict\n{json.dumps(raw_verdict, ensure_ascii=False)}\n```"}
    )
    if errors:
        return None, [], [
            _blocker(
                "role-result-evaluator-verdict-invalid" if requires_verdict else "role-result-role-contract-invalid",
                "Reported verdict does not match the role/lane contract.",
                category="verdict",
                details={"errors": [error.code for error in errors]},
            )
        ]

    candidates = [item for item in verdicts if item.lane == context.lane]
    if requires_verdict:
        candidates = [item for item in candidates if item.kind == "submission-evaluator"]
    if len(candidates) != 1:
        return None, [], [
            _blocker(
                "role-result-evaluator-verdict-invalid" if requires_verdict else "role-result-role-contract-invalid",
                "Reported verdict does not match the role/lane contract.",
                category="verdict",
            )
        ]

    verdict = candidates[0]
    return verdict.to_dict(), [item.to_dict() for item in verdict.blockers], []
```

Top-level role-result blockers make `status: "succeeded"` invalid. Verdict blockers are different: they express readiness downgrade from a structured evaluator verdict and should be appended to the normalized blocker list without turning a syntactically valid evaluator execution into a runtime contract failure.

- [ ] **Step 4: Add role-specific helper**

Add this helper below `_validate_verdict`:

```python
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
```

Add these supporting helpers below it:

```python
def _validate_evidence_role_blockers(
    *,
    context: RoleResultContext,
    blockers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if context.role_id not in EVIDENCE_ROLE_IDS:
        return []
    invalid = [
        item
        for item in blockers
        if str(item.get("category") or "").strip() not in EVIDENCE_BLOCKER_CATEGORIES
    ]
    if not invalid:
        return []
    return [
        _blocker(
            "role-result-role-contract-invalid",
            "Evidence role blockers must use evidence-support categories.",
            category="process",
            details={
                "invalid_categories": sorted({str(item.get("category")) for item in invalid}),
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
```

- [ ] **Step 5: Run role-specific tests**

Run:

```bash
python3 -m unittest tests.test_role_result_contract.RoleResultContractRoleSpecificTests -q
```

Expected: PASS.

- [ ] **Step 6: Run all contract tests**

Run:

```bash
python3 -m unittest tests.test_role_result_contract -q
```

Expected: PASS.

- [ ] **Step 7: Commit role-specific implementation**

Run:

```bash
git add academic_engine/role_result_contract.py
git commit -m "feat: enforce role-specific result contracts"
```

---

### Task 7: Integrate Validator Into WorkflowEngine

**Files:**
- Modify: `academic_engine/workflow_engine.py`
- Modify: `tests/test_workflow_engine.py`

- [ ] **Step 1: Add failing integration assertion for successful result with blockers**

Insert this test after `test_missing_checkpoint_evidence_fails_closed` in `tests/test_workflow_engine.py`:

```python
    def test_successful_role_result_with_blockers_fails_closed(self) -> None:
        blocker = {
            "category": "citation",
            "code": "citation-gap",
            "message": "Citation support remains incomplete.",
            "repairable": True,
        }

        def executor(sandbox: Path, prompt: str, output: Path, use_search: bool, model: str | None) -> None:
            _write_role_result(
                output,
                prompt,
                sandbox,
                [self.target.relative_to(self.root)],
                verdict=None,
                blockers=[blocker],
            )

        result = WorkflowEngine(self.root, role_executor=executor).run(
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
        self.assertTrue(any(item["code"] == "role-result-success-with-blockers" for item in result.blockers))
```

- [ ] **Step 2: Update existing expected error codes**

In `tests/test_workflow_engine.py`, change these assertions:

```python
self.assertTrue(any(item["code"] == "role-result-invalid" for item in result.blockers))
```

to:

```python
self.assertTrue(any(item["code"] == "role-result-block-missing" for item in result.blockers))
```

Change:

```python
self.assertTrue(any(item["code"] == "checkpoint-evidence-invalid" for item in result.blockers))
```

to:

```python
self.assertTrue(any(item["code"] == "role-result-success-without-evidence" for item in result.blockers))
```

Keep `role-result-identity-mismatch` unchanged.

- [ ] **Step 3: Run workflow tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_workflow_engine -q
```

Expected: FAIL because `WorkflowEngine` still emits old codes and still accepts `succeeded` results with blockers.

- [ ] **Step 4: Commit RED integration tests**

Run:

```bash
git add tests/test_workflow_engine.py
git commit -m "test: expect strict workflow role result codes"
```

- [ ] **Step 5: Wire imports**

In `academic_engine/workflow_engine.py`, replace:

```python
from .utils import utc_now
from .verdict_parser import extract_structured_verdicts
```

with:

```python
from .role_result_contract import (
    ROLE_RESULT_VERSION,
    ArtifactRecord,
    RoleResultContext,
    validate_role_result_payload,
)
from .utils import utc_now
```

Delete the local constant:

```python
ROLE_RESULT_VERSION = "role-result/v1"
```

Keep `WORKFLOW_VERSION = "workflow-run/v1"`.

- [ ] **Step 6: Pass contract context into parser**

In `_run_role`, change the `_parse_role_result(...)` call from:

```python
            role_result, result_blockers = _parse_role_result(
                output_file,
                workflow=workflow,
                node=node,
                sandbox_dir=sandbox_dir,
                after=after,
                changed_paths=changed,
            )
```

to:

```python
            role_result, result_blockers = _parse_role_result(
                output_file,
                workflow=workflow,
                node=node,
                contract=contract,
                root_dir=self.root_dir,
                sandbox_dir=sandbox_dir,
                after=after,
                changed_paths=changed,
            )
```

Update `_parse_role_result` signature to:

```python
def _parse_role_result(
    path: Path,
    *,
    workflow: WorkflowRun,
    node: RoleNode,
    contract: ExecutionContract,
    root_dir: Path,
    sandbox_dir: Path,
    after: dict[str, dict[str, Any]],
    changed_paths: list[str],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
```

- [ ] **Step 7: Replace inline parser validation**

Replace the body of `_parse_role_result` from the `if not path.exists()` line through the return block with:

```python
    if not path.exists():
        return None, [_runtime_blocker("role-result-block-missing", "Role produced no output.")]
    text = path.read_text(encoding="utf-8", errors="replace")
    matches = list(_ROLE_RESULT_PATTERN.finditer(text))
    if not matches:
        return None, [_runtime_blocker("role-result-block-missing", "Role produced no role-result block.")]
    if len(matches) != 1:
        return None, [
            _runtime_blocker(
                "role-result-block-count-invalid",
                f"Expected exactly one role-result block, found {len(matches)}.",
            )
        ]
    try:
        payload = json.loads(matches[0].group("body"))
    except json.JSONDecodeError as exc:
        return None, [_runtime_blocker("role-result-json-invalid", f"Role result is not valid JSON: {exc}.")]

    validated, result_blockers = validate_role_result_payload(
        payload,
        RoleResultContext(
            workflow_id=workflow.workflow_id,
            expected_role_run_id=f"{len(workflow.role_runs) + 1:02d}-{node.role_id}",
            role_id=node.role_id,
            work_id=workflow.work_id,
            lane=workflow.lane,
            action=workflow.action,
            required_checkpoints=node.checkpoints,
            sandbox_dir=sandbox_dir,
            post_manifest=after,
            changed_paths=tuple(changed_paths),
            required_output_paths=_required_output_paths(root_dir, contract),
            evaluator=node.evaluator,
            finalizer=node.finalizer,
        ),
    )
    if validated is None:
        return None, result_blockers

    return {
        "status": validated.status,
        "checkpoints": list(validated.checkpoints),
        "blockers": list(validated.blockers),
        "artifacts": list(validated.artifacts),
        "verdict": validated.verdict,
    }, []
```

- [ ] **Step 8: Add required output path helper**

Add this helper near `_role_allowed_write_scopes`:

```python
def _required_output_paths(root_dir: Path, contract: ExecutionContract) -> tuple[str, ...]:
    paths: list[str] = []
    for item in contract.required_outputs:
        path = Path(item.path)
        if path.is_absolute():
            try:
                paths.append(path.resolve().relative_to(root_dir.resolve()).as_posix())
            except ValueError:
                continue
        else:
            paths.append(path.as_posix())
    return tuple(dict.fromkeys(paths))
```

- [ ] **Step 9: Remove duplicated inline validation helpers**

Delete these functions from `academic_engine/workflow_engine.py` after `_parse_role_result` is integrated:

```python
def _validate_reported_artifacts(...)
def _validate_checkpoint_evidence(...)
def _validate_reported_blockers(...)
def _validate_reported_verdict(...)
def _sandbox_relative_path(...)
def _artifact_from_manifest(...)
def _media_type(...)
```

Also remove the now-unused import:

```python
from .verdict_parser import extract_structured_verdicts
```

- [ ] **Step 10: Run workflow tests**

Run:

```bash
python3 -m unittest tests.test_workflow_engine -q
```

Expected: PASS.

- [ ] **Step 11: Run contract and workflow tests together**

Run:

```bash
python3 -m unittest tests.test_role_result_contract tests.test_workflow_engine -q
```

Expected: PASS.

- [ ] **Step 12: Commit workflow integration**

Run:

```bash
git add academic_engine/workflow_engine.py tests/test_workflow_engine.py
git commit -m "feat: route workflow role results through contract validator"
```

---

### Task 8: Update Role Prompt Contract Text

**Files:**
- Modify: `academic_engine/workflow_engine.py`

- [ ] **Step 1: Update prompt rules in `_role_prompt`**

In `_role_prompt`, replace the rules block lines:

```python
- Report every required checkpoint and map it to at least one artifact whose SHA-256 you computed.
- List every created or modified artifact with its sandbox-relative path and SHA-256.
- Put the structured verdict object in `verdict`; do not copy example verdicts from the role policy.
```

with:

```python
- Report every required checkpoint and map it to at least one artifact whose SHA-256 you computed.
- A `succeeded` result is invalid unless all required checkpoints have hash-verified artifact evidence.
- If blockers remain, use status `blocked` or `failed`; never report `succeeded` with blockers.
- Every blocker must use a stable lowercase machine code such as `primary-support-missing`, not free-form prose.
- List every created or modified artifact with its sandbox-relative path and SHA-256.
- Put the structured verdict object in `verdict`; evaluator roles must not use `null`.
```

- [ ] **Step 2: Update required role result shape example**

Keep the JSON example's successful path blocker-free. Immediately after the fenced JSON example, add this Python f-string fragment:

~~~python

If the role cannot honestly satisfy the checkpoints, return status `blocked` or `failed` and include blockers like:
```role-result
{
  "version": "{ROLE_RESULT_VERSION}",
  "workflow_id": "{workflow.workflow_id}",
  "role_run_id": "{role_run_id}",
  "role_id": "{node.role_id}",
  "work_id": "{workflow.work_id}",
  "lane": "{workflow.lane}",
  "action": "{workflow.action}",
  "status": "blocked",
  "checkpoints": {json.dumps(list(node.checkpoints), ensure_ascii=False)},
  "checkpoint_evidence": {{"<checkpoint>": ["works/{workflow.work_id}/path/to/artifact.md"]}},
  "blockers": [
    {{"category": "primary-support", "code": "primary-support-missing", "message": "Primary support is still missing.", "repairable": true}}
  ],
  "artifacts": [
    {{"path": "works/{workflow.work_id}/path/to/artifact.md", "sha256": "<64 lowercase hex>"}}
  ],
  "verdict": null
}
```
~~~

Ensure the nested prompt text remains inside the outer Python triple-quoted f-string.

- [ ] **Step 3: Run workflow tests after prompt change**

Run:

```bash
python3 -m unittest tests.test_workflow_engine -q
```

Expected: PASS.

- [ ] **Step 4: Commit prompt update**

Run:

```bash
git add academic_engine/workflow_engine.py
git commit -m "docs: tighten role result prompt contract"
```

---

### Task 9: Final Verification

**Files:**
- Review all changed files.

- [ ] **Step 1: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_role_result_contract tests.test_workflow_engine -q
```

Expected: PASS.

- [ ] **Step 2: Run broad test suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: PASS.

- [ ] **Step 3: Run lint if available**

Run:

```bash
python3 -m ruff check academic_engine tests
```

Expected: PASS. If `ruff` is not installed in the active environment, record that explicitly in the implementation summary and do not claim lint passed.

- [ ] **Step 4: Inspect diff**

Run:

```bash
git diff --stat HEAD
git diff -- academic_engine/role_result_contract.py academic_engine/workflow_engine.py tests/test_role_result_contract.py tests/test_workflow_engine.py meta/schemas/role-result.schema.json meta/README.md
```

Expected: the diff is limited to role-result contract hardening, schema documentation, prompt text, and tests.

- [ ] **Step 5: Commit final cleanup if any**

If verification required small cleanup edits, run:

```bash
git add academic_engine/role_result_contract.py academic_engine/workflow_engine.py tests/test_role_result_contract.py tests/test_workflow_engine.py meta/schemas/role-result.schema.json meta/README.md
git commit -m "test: verify role result contract hardening"
```

If no cleanup edits were needed, do not create an empty commit.

---

## Self-Review Notes

Spec coverage:

- JSON Schema validation is covered by `meta/schemas/role-result.schema.json` and runtime mirror validation in `role_result_contract.py`.
- Mandatory fields are covered by `REQUIRED_ROLE_RESULT_FIELDS`, identity checks, checkpoint checks, artifact checks, blocker checks, verdict checks, and role-specific finalizer/evidence rules.
- Successful result without evidence is blocked by `role-result-success-without-evidence`.
- Separate error codes are represented as stable `role-result-*` blocker codes.
- No runtime dependency is added.

Implementation boundary:

- `WorkflowEngine` still extracts the fenced block and remains the only acceptance point.
- The contract module receives trusted context; it does not read workspace config or promote artifacts.
- Existing fail-closed write-scope and deletion checks remain in `WorkflowEngine`.
