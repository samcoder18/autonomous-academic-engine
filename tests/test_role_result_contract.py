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

    def test_extra_top_level_field_fails_schema_contract(self) -> None:
        payload = _valid_payload()
        payload["notes"] = "free-form extra field"

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-schema-invalid", _codes(blockers))
        self.assertEqual(blockers[0]["details"]["unexpected"], ["notes"])

    def test_extra_artifact_field_fails_schema_contract(self) -> None:
        payload = _valid_payload()
        payload["artifacts"][0]["label"] = "extra artifact metadata"

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-artifacts-invalid", _codes(blockers))


class RoleResultContractStatusTests(unittest.TestCase):
    def test_succeeded_requires_checkpoint_evidence(self) -> None:
        payload = _valid_payload(checkpoint_evidence={})

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-success-without-evidence", _codes(blockers))

    def test_succeeded_requires_at_least_one_checkpoint(self) -> None:
        payload = _valid_payload(checkpoints=(), checkpoint_evidence={})

        result, blockers = validate_role_result_payload(payload, _context(checkpoints=()))

        self.assertIsNone(result)
        self.assertIn("role-result-success-without-evidence", _codes(blockers))

    def test_succeeded_requires_evidence_even_without_required_checkpoint_context(self) -> None:
        payload = _valid_payload(
            checkpoints=("self-check",),
            checkpoint_evidence={},
        )

        result, blockers = validate_role_result_payload(payload, _context(checkpoints=()))

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

    def test_blocker_repairable_must_be_boolean_when_present(self) -> None:
        payload = _valid_payload(
            status="blocked",
            blockers=[_valid_blocker(repairable="false")],
        )

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-blocker-schema-invalid", _codes(blockers))

    def test_blocker_details_must_be_object_when_present(self) -> None:
        payload = _valid_payload(
            status="blocked",
            blockers=[_valid_blocker(details="citation support is missing")],
        )

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-blocker-schema-invalid", _codes(blockers))

    def test_blocker_core_fields_must_be_strings(self) -> None:
        cases = {
            "category": _StringLike("citation"),
            "code": 404,
            "message": 404,
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                payload = _valid_payload(
                    status="blocked",
                    blockers=[_valid_blocker(**{field: value})],
                )

                result, blockers = validate_role_result_payload(payload, _context())

                self.assertIsNone(result)
                self.assertIn("role-result-blocker-schema-invalid", _codes(blockers))

    def test_blocker_blocks_statuses_must_be_non_empty_strings(self) -> None:
        cases = (
            "submission-ready",
            [123],
            [""],
            ["submission-ready", ""],
        )
        for value in cases:
            with self.subTest(blocks_statuses=value):
                payload = _valid_payload(
                    status="blocked",
                    blockers=[_valid_blocker(blocks_statuses=value)],
                )

                result, blockers = validate_role_result_payload(payload, _context())

                self.assertIsNone(result)
                self.assertIn("role-result-blocker-schema-invalid", _codes(blockers))

    def test_extra_blocker_field_fails_schema_contract(self) -> None:
        payload = _valid_payload(
            status="blocked",
            blockers=[_valid_blocker(note="extra blocker metadata")],
        )

        result, blockers = validate_role_result_payload(payload, _context())

        self.assertIsNone(result)
        self.assertIn("role-result-blocker-schema-invalid", _codes(blockers))


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

    def test_evidence_role_structured_verdict_blockers_do_not_fail_successful_execution(self) -> None:
        artifact_path = "works/demo/articles/reviews/citation-check.md"
        context = _context(
            role_id="academic-citation-checker",
            lane="article",
            action="article",
            artifact_path=artifact_path,
        )
        payload = _valid_payload(
            role_id="academic-citation-checker",
            lane="article",
            action="article",
            artifact_path=artifact_path,
            verdict={
                "verdict_version": "1",
                "lane": "article",
                "kind": "citation-checker",
                "status": "blocked-citation",
                "summary": "Citation pass found unresolved support gaps.",
                "blockers": [
                    {
                        "category": "citation",
                        "code": "citation-safety-gap",
                        "message": "Primary support is still incomplete.",
                        "repairable": True,
                        "blocks_statuses": ["submission-ready"],
                    }
                ],
            },
        )

        result, blockers = validate_role_result_payload(payload, context)

        self.assertEqual(blockers, [])
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.verdict["status"], "blocked-citation")
        self.assertIn("citation-safety-gap", _codes(list(result.blockers)))

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


def _valid_blocker(**overrides: Any) -> dict[str, Any]:
    blocker: dict[str, Any] = {
        "category": "citation",
        "code": "citation-gap",
        "message": "Citation support remains incomplete.",
        "repairable": True,
    }
    blocker.update(overrides)
    return blocker


class _StringLike:
    def __init__(self, text: str) -> None:
        self._text = text

    def __str__(self) -> str:
        return self._text


def _codes(blockers: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("code")) for item in blockers}


if __name__ == "__main__":
    unittest.main()
