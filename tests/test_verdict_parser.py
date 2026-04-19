from __future__ import annotations

import unittest

from telegram_console.verdict_parser import (
    StructuredVerdict,
    VerdictParseError,
    extract_structured_verdicts,
    find_verdict_blocks,
    highest_severity_status,
    parse_verdict,
)


def _wrap(body: str) -> str:
    return f"prelude\n```verdict\n{body}\n```\ntail"


class FindVerdictBlocksTests(unittest.TestCase):
    def test_no_block(self) -> None:
        self.assertEqual(find_verdict_blocks("nothing here"), [])

    def test_multiple_blocks_order_preserved(self) -> None:
        text = _wrap('{"verdict_version":"1"}') + "\n" + _wrap('{"a":1}')
        blocks = find_verdict_blocks(text)
        self.assertEqual(len(blocks), 2)
        self.assertIn('"verdict_version":"1"', blocks[0])
        self.assertIn('"a":1', blocks[1])

    def test_case_insensitive_fence(self) -> None:
        text = "```VERDICT\n{}\n```"
        self.assertEqual(len(find_verdict_blocks(text)), 1)


class ParseVerdictTests(unittest.TestCase):
    def _valid_payload(self, **overrides: object) -> str:
        base = {
            "verdict_version": "1",
            "lane": "thesis",
            "kind": "submission-evaluator",
            "status": "reviewed",
            "summary": "ok",
        }
        base.update(overrides)
        import json

        return json.dumps(base)

    def test_happy_path(self) -> None:
        verdict = parse_verdict(self._valid_payload(), source="output")
        self.assertIsInstance(verdict, StructuredVerdict)
        assert isinstance(verdict, StructuredVerdict)
        self.assertEqual(verdict.lane, "thesis")
        self.assertEqual(verdict.kind, "submission-evaluator")
        self.assertEqual(verdict.status, "reviewed")
        self.assertEqual(verdict.summary, "ok")
        self.assertEqual(verdict.source, "output")

    def test_empty_body(self) -> None:
        err = parse_verdict("   ", source="output")
        self.assertIsInstance(err, VerdictParseError)
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "empty-body")

    def test_invalid_json(self) -> None:
        err = parse_verdict("{not json", source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "json-decode-error")
        self.assertIn("Verdict block is not valid JSON", err.message)

    def test_not_object(self) -> None:
        err = parse_verdict("[1,2,3]", source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "not-object")

    def test_version_mismatch(self) -> None:
        err = parse_verdict(self._valid_payload(verdict_version="99"), source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "version-mismatch")

    def test_invalid_lane(self) -> None:
        err = parse_verdict(self._valid_payload(lane="rocket-science"), source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "lane-invalid")

    def test_invalid_kind(self) -> None:
        err = parse_verdict(self._valid_payload(kind="nope"), source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "kind-invalid")

    def test_invalid_status(self) -> None:
        err = parse_verdict(self._valid_payload(status="awesome"), source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "status-invalid")

    def test_unknown_field(self) -> None:
        err = parse_verdict(self._valid_payload(mystery="x"), source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "unknown-field")

    def test_blocker_validation(self) -> None:
        payload = self._valid_payload(
            status="blocked-primary-support",
            blockers=[{"category": "primary-support", "code": "missing-statute", "message": "Add ФЗ-572."}],
        )
        verdict = parse_verdict(payload, source="output")
        assert isinstance(verdict, StructuredVerdict)
        self.assertEqual(len(verdict.blockers), 1)
        blocker = verdict.blockers[0]
        self.assertEqual(blocker.category, "primary-support")
        self.assertEqual(blocker.code, "missing-statute")

    def test_blocker_invalid_code(self) -> None:
        payload = self._valid_payload(
            blockers=[{"category": "primary-support", "code": "Has Spaces", "message": "nope"}],
        )
        err = parse_verdict(payload, source="output")
        assert isinstance(err, VerdictParseError)
        self.assertEqual(err.code, "blocker-code")


class ExtractAndSeverityTests(unittest.TestCase):
    def test_extract_returns_errors_and_verdicts(self) -> None:
        bad = _wrap("{malformed")
        good = _wrap('{"verdict_version":"1","lane":"thesis","kind":"submission-evaluator","status":"reviewed"}')
        verdicts, errors = extract_structured_verdicts({"output": bad + "\n" + good, "review": ""})
        self.assertEqual(len(verdicts), 1)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, "json-decode-error")

    def test_malformed_error_converts_to_blocker(self) -> None:
        err = VerdictParseError(source="output", code="json-decode-error", message="bad json")
        blocker = err.to_blocker()
        self.assertEqual(blocker.category, "verdict")
        self.assertEqual(blocker.code, "verdict-json-decode-error")
        self.assertTrue(blocker.repairable)

    def test_highest_severity(self) -> None:
        severity = {"strong-draft": 1, "strong-draft-with-blockers": 2, "submission-ready": 0}
        v1 = StructuredVerdict(lane="article", kind="submission-evaluator", status="strong-draft")
        v2 = StructuredVerdict(lane="article", kind="submission-evaluator", status="strong-draft-with-blockers")
        v3 = StructuredVerdict(lane="article", kind="submission-evaluator", status="submission-ready")
        self.assertEqual(highest_severity_status((v1, v2, v3), severity=severity), "strong-draft-with-blockers")
        self.assertIsNone(highest_severity_status((), severity=severity))


if __name__ == "__main__":
    unittest.main()
