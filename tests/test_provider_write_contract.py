from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from academic_engine.provider_write_contract import (
    MAX_PROVIDER_WRITE_PLAN_BYTES,
    ProviderWritePlanContext,
    parse_provider_write_plan,
    validate_provider_write_plan,
)


class ProviderWriteContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.target = self.root / "works" / "demo" / "articles" / "drafts" / "draft.md"
        self.target.parent.mkdir(parents=True)
        self.target.write_text("# Before\n", encoding="utf-8")
        self.target_path = self.target.relative_to(self.root).as_posix()
        self.target_sha256 = _sha256(self.target.read_bytes())
        self.context = ProviderWritePlanContext(
            workflow_id="workflow-1",
            role_run_id="01-academic-draft-writer",
            role_id="academic-draft-writer",
            work_id="demo",
            sandbox_dir=self.root,
            allowed_write_scopes=(self.target.parent.relative_to(self.root).as_posix(),),
            pre_write_manifest={
                self.target_path: {
                    "sha256": self.target_sha256,
                    "size": self.target.stat().st_size,
                }
            },
        )

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_valid_full_file_replacement_is_parsed_and_validated(self) -> None:
        payload = self.plan()

        parsed, parse_blockers = parse_provider_write_plan(_fence(payload))
        validated, validation_blockers = validate_provider_write_plan(parsed, self.context)

        self.assertEqual(parse_blockers, [])
        self.assertEqual(validation_blockers, [])
        self.assertIsNotNone(validated)
        assert validated is not None
        self.assertEqual(validated.operations[0].path, self.target_path)
        self.assertEqual(validated.operations[0].content, "# After\n")

    def test_path_traversal_is_rejected(self) -> None:
        payload = self.plan(path="works/demo/articles/drafts/../../outside.md")

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-path-forbidden"])

    def test_out_of_scope_path_is_rejected(self) -> None:
        payload = self.plan(path="works/demo/articles/reviews/review.md")

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-path-forbidden"])

    def test_symlink_path_is_rejected_even_inside_an_allowed_scope(self) -> None:
        outside = self.root / "works" / "demo" / "articles" / "reviews" / "outside.md"
        outside.parent.mkdir(parents=True)
        outside.write_text("# Outside\n", encoding="utf-8")
        symlink = self.target.parent / "linked.md"
        symlink.symlink_to(outside)
        symlink_path = symlink.relative_to(self.root).as_posix()
        self.context = ProviderWritePlanContext(
            workflow_id=self.context.workflow_id,
            role_run_id=self.context.role_run_id,
            role_id=self.context.role_id,
            work_id=self.context.work_id,
            sandbox_dir=self.context.sandbox_dir,
            allowed_write_scopes=self.context.allowed_write_scopes,
            pre_write_manifest={
                **self.context.pre_write_manifest,
                symlink_path: {"sha256": _sha256(outside.read_bytes()), "size": outside.stat().st_size},
            },
        )
        payload = self.plan(path=symlink_path, base_sha256=_sha256(outside.read_bytes()))

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-path-forbidden"])

    def test_stale_base_sha256_is_rejected(self) -> None:
        payload = self.plan(base_sha256="0" * 64)

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-base-hash-mismatch"])

    def test_manifest_hash_is_rejected_after_target_is_mutated(self) -> None:
        payload = self.plan()
        self.target.write_text("# Changed after manifest capture\n", encoding="utf-8")

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-base-hash-mismatch"])

    def test_deletion_or_rename_operation_fields_are_rejected(self) -> None:
        for forbidden_field in ("delete", "rename_to"):
            with self.subTest(forbidden_field=forbidden_field):
                payload = self.plan()
                payload["operations"][0][forbidden_field] = True

                parsed, parse_blockers = parse_provider_write_plan(_fence(payload))

                self.assertIsNone(parsed)
                self.assertEqual(_blocker_codes(parse_blockers), ["provider-write-plan-schema-invalid"])

    def test_duplicate_paths_are_rejected(self) -> None:
        payload = self.plan()
        payload["operations"].append(dict(payload["operations"][0]))

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-path-duplicate"])

    def test_invalid_json_or_fence_is_rejected(self) -> None:
        with self.subTest("invalid json"):
            parsed, blockers = parse_provider_write_plan("```provider-write-plan\n{not json}\n```")
            self.assertIsNone(parsed)
            self.assertEqual(_blocker_codes(blockers), ["provider-write-plan-json-invalid"])

        with self.subTest("wrong fence"):
            parsed, blockers = parse_provider_write_plan(_fence(self.plan(), label="json"))
            self.assertIsNone(parsed)
            self.assertEqual(_blocker_codes(blockers), ["provider-write-plan-block-missing"])

    def test_payload_size_limit_is_rejected_before_validation(self) -> None:
        payload = self.plan(content="x" * MAX_PROVIDER_WRITE_PLAN_BYTES)

        parsed, blockers = parse_provider_write_plan(_fence(payload))

        self.assertIsNone(parsed)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-plan-payload-too-large"])

    def test_direct_validator_rejects_an_oversized_payload(self) -> None:
        payload = self.plan(content="x" * MAX_PROVIDER_WRITE_PLAN_BYTES)

        validated, blockers = validate_provider_write_plan(payload, self.context)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-plan-payload-too-large"])

    def test_live_target_change_after_manifest_capture_is_rejected(self) -> None:
        payload = self.plan()
        self.target.write_text("# Changed after manifest\n", encoding="utf-8")

        validated, blockers = self.validate(payload)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-base-hash-mismatch"])

    def test_direct_validator_rejects_payload_larger_than_limit(self) -> None:
        payload = self.plan(content="x" * MAX_PROVIDER_WRITE_PLAN_BYTES)

        validated, blockers = validate_provider_write_plan(payload, self.context)

        self.assertIsNone(validated)
        self.assertEqual(_blocker_codes(blockers), ["provider-write-plan-payload-too-large"])

    def plan(
        self,
        *,
        path: str | None = None,
        base_sha256: str | None = None,
        content: str = "# After\n",
    ) -> dict[str, object]:
        return {
            "version": "provider-write-plan/v1",
            "workflow_id": self.context.workflow_id,
            "role_run_id": self.context.role_run_id,
            "role_id": self.context.role_id,
            "work_id": self.context.work_id,
            "operations": [
                {
                    "path": path or self.target_path,
                    "base_sha256": base_sha256 or self.target_sha256,
                    "content": content,
                }
            ],
        }

    def validate(self, payload: dict[str, object]) -> tuple[object, list[dict[str, object]]]:
        parsed, parse_blockers = parse_provider_write_plan(_fence(payload))
        self.assertEqual(parse_blockers, [])
        return validate_provider_write_plan(parsed, self.context)


def _fence(payload: dict[str, object], *, label: str = "provider-write-plan") -> str:
    return f"```{label}\n{json.dumps(payload)}\n```"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _blocker_codes(blockers: list[dict[str, object]]) -> list[str]:
    return [str(blocker["code"]) for blocker in blockers]


if __name__ == "__main__":
    unittest.main()
