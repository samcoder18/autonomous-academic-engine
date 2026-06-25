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
