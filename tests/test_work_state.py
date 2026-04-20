from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram_console.work_state import build_work_state


def _artifact(path: Path, *, exists: bool, artifact_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {"path": str(path), "exists": exists}
    if artifact_id is not None:
        payload["artifact_id"] = artifact_id
    return payload


def _candidate_dissertation_payload(
    dissertation_root: Path,
    *,
    maps_complete: bool,
    dissertation_review_exists: bool,
    counterargument_review_exists: bool,
    metadata_exists: bool,
    publication_evidence_exists: bool,
    publication_matrix_exists: bool,
    formal_artifacts_complete: bool,
    character_count: int = 240000,
) -> dict[str, object]:
    if not maps_complete:
        suggested_next_action = "build-maps"
    elif not dissertation_review_exists:
        suggested_next_action = "verify-claims"
    elif not counterargument_review_exists:
        suggested_next_action = "counterargument-pass"
    elif not (
        metadata_exists and publication_evidence_exists and publication_matrix_exists and formal_artifacts_complete
    ):
        suggested_next_action = "formal-artifacts"
    else:
        suggested_next_action = "draft-author-position"

    return {
        "available": True,
        "profile_id": "dissertation-candidate",
        "metadata": _artifact(dissertation_root / "metadata.toml", exists=metadata_exists),
        "maps": [
            _artifact(
                dissertation_root / "maps" / "historiography-map.md",
                artifact_id="historiography-map",
                exists=maps_complete,
            ),
            _artifact(
                dissertation_root / "maps" / "novelty-contribution-map.md",
                artifact_id="novelty-map",
                exists=maps_complete,
            ),
            _artifact(
                dissertation_root / "maps" / "dissertation-claim-map.md",
                artifact_id="claim-map",
                exists=maps_complete,
            ),
        ],
        "chapter_contracts": [
            _artifact(dissertation_root / "chapter-contracts" / "01-chapter-contract.md", exists=maps_complete),
            _artifact(dissertation_root / "chapter-contracts" / "02-chapter-contract.md", exists=maps_complete),
            _artifact(dissertation_root / "chapter-contracts" / "03-chapter-contract.md", exists=maps_complete),
        ],
        "reviews": [
            _artifact(
                dissertation_root / "reviews" / "counterargument-review.md",
                artifact_id="counterargument-review",
                exists=counterargument_review_exists,
            ),
            _artifact(
                dissertation_root / "reviews" / "dissertation-review.md",
                artifact_id="dissertation-review",
                exists=dissertation_review_exists,
            ),
        ],
        "artifacts": [
            _artifact(
                dissertation_root / "artifacts" / "author-abstract.md",
                artifact_id="author-abstract",
                exists=formal_artifacts_complete,
            ),
            _artifact(
                dissertation_root / "artifacts" / "defense-checklist.md",
                artifact_id="defense-checklist",
                exists=formal_artifacts_complete,
            ),
        ],
        "publication_artifacts": [
            _artifact(
                dissertation_root / "publications" / "publication-claim-matrix.md",
                artifact_id="publication-claim-matrix",
                exists=publication_matrix_exists,
            )
        ],
        "publication_claim_matrix": _artifact(
            dissertation_root / "publications" / "publication-claim-matrix.md",
            exists=publication_matrix_exists,
        ),
        "publication_evidence": _artifact(
            dissertation_root / "publications" / "publication-evidence.md",
            exists=publication_evidence_exists,
        ),
        "suggested_next_action": suggested_next_action,
        "character_count": character_count,
    }


def _build_candidate_work_state(
    root: Path,
    *,
    review_exists: bool,
    reviewed_count: int,
    dissertation_payload: dict[str, object],
) -> dict[str, object]:
    section_path = root / "works" / "phd-law" / "thesis" / "manuscript" / "sections" / "01-introduction.md"
    section_path.parent.mkdir(parents=True, exist_ok=True)
    section_path.write_text("# Введение\n", encoding="utf-8")
    return build_work_state(
        root_dir=root,
        work_id="phd-law",
        work_title="Кандидатская диссертация",
        active_lanes=("thesis",),
        thesis_overview={
            "sections": [
                {
                    "target": str(section_path),
                    "review_exists": review_exists,
                    "summary": {
                        "target": str(section_path),
                        "blocker_count": 0,
                        "suggested_next_action": "review-section" if review_exists else "write-section",
                    },
                }
            ],
            "summary": {"section_count": 1, "reviewed_count": reviewed_count, "blocked_count": 0},
            "dissertation": dissertation_payload,
        },
        thesis_ledger_advisory=None,
        article_overview=None,
        standards_profiles={},
        runtime_records=(),
    )


class DissertationWorkStateTests(unittest.TestCase):
    def test_dissertation_next_action_prioritizes_maps_over_export(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dissertation_root = root / "works" / "phd-law" / "thesis" / "dissertation"
            payload = _candidate_dissertation_payload(
                dissertation_root,
                maps_complete=False,
                dissertation_review_exists=False,
                counterargument_review_exists=False,
                metadata_exists=False,
                publication_evidence_exists=False,
                publication_matrix_exists=False,
                formal_artifacts_complete=False,
                character_count=0,
            )
            state = _build_candidate_work_state(
                root,
                review_exists=True,
                reviewed_count=1,
                dissertation_payload=payload,
            )
            next_action = state["suggested_next_action"]
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "dissertation-build-maps")
            self.assertEqual(
                next_action["command"],
                "launch-thesis build-maps works/phd-law/thesis/dissertation/maps/historiography-map.md",
            )

    def test_candidate_next_action_moves_to_verify_claims_after_maps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dissertation_root = root / "works" / "phd-law" / "thesis" / "dissertation"
            state = _build_candidate_work_state(
                root,
                review_exists=True,
                reviewed_count=1,
                dissertation_payload=_candidate_dissertation_payload(
                    dissertation_root,
                    maps_complete=True,
                    dissertation_review_exists=False,
                    counterargument_review_exists=False,
                    metadata_exists=False,
                    publication_evidence_exists=False,
                    publication_matrix_exists=False,
                    formal_artifacts_complete=False,
                ),
            )
            next_action = state["suggested_next_action"]
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "dissertation-verify-claims")

    def test_candidate_next_action_moves_to_counterargument_after_claim_review(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dissertation_root = root / "works" / "phd-law" / "thesis" / "dissertation"
            state = _build_candidate_work_state(
                root,
                review_exists=True,
                reviewed_count=1,
                dissertation_payload=_candidate_dissertation_payload(
                    dissertation_root,
                    maps_complete=True,
                    dissertation_review_exists=True,
                    counterargument_review_exists=False,
                    metadata_exists=False,
                    publication_evidence_exists=False,
                    publication_matrix_exists=False,
                    formal_artifacts_complete=False,
                ),
            )
            next_action = state["suggested_next_action"]
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "dissertation-counterargument-pass")

    def test_formal_artifacts_do_not_jump_ahead_of_author_position_for_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dissertation_root = root / "works" / "phd-law" / "thesis" / "dissertation"
            state = _build_candidate_work_state(
                root,
                review_exists=False,
                reviewed_count=0,
                dissertation_payload=_candidate_dissertation_payload(
                    dissertation_root,
                    maps_complete=True,
                    dissertation_review_exists=True,
                    counterargument_review_exists=True,
                    metadata_exists=False,
                    publication_evidence_exists=False,
                    publication_matrix_exists=True,
                    formal_artifacts_complete=False,
                ),
            )
            dissertation_summary = state["thesis"]["dissertation"]["summary"]
            next_action = state["suggested_next_action"]
            self.assertTrue(dissertation_summary["publication_matrix_complete"])
            self.assertTrue(dissertation_summary["review_sequence_complete"])
            self.assertFalse(dissertation_summary["candidate_intellectual_maturity_complete"])
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "dissertation-draft-author-position")
            self.assertEqual(
                next_action["command"],
                "launch-thesis draft-author-position works/phd-law/thesis/manuscript/sections/01-introduction.md",
            )

    def test_candidate_next_action_returns_to_formal_artifacts_after_author_position(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dissertation_root = root / "works" / "phd-law" / "thesis" / "dissertation"
            state = _build_candidate_work_state(
                root,
                review_exists=True,
                reviewed_count=1,
                dissertation_payload=_candidate_dissertation_payload(
                    dissertation_root,
                    maps_complete=True,
                    dissertation_review_exists=True,
                    counterargument_review_exists=True,
                    metadata_exists=False,
                    publication_evidence_exists=False,
                    publication_matrix_exists=True,
                    formal_artifacts_complete=False,
                ),
            )
            dissertation_summary = state["thesis"]["dissertation"]["summary"]
            next_action = state["suggested_next_action"]
            self.assertTrue(dissertation_summary["candidate_intellectual_maturity_complete"])
            self.assertEqual(
                dissertation_summary["next_target"],
                "works/phd-law/thesis/dissertation/metadata.toml",
            )
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "dissertation-formal-artifacts")
            self.assertEqual(
                next_action["command"],
                "launch-thesis formal-artifacts works/phd-law/thesis/dissertation/metadata.toml",
            )


class SingleLaneDraftNextActionTests(unittest.TestCase):
    def test_article_only_work_state_suggests_article_draft_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = build_work_state(
                root_dir=root,
                work_id="starter-work",
                work_title="Starter Work",
                active_lanes=("article",),
                thesis_overview=None,
                thesis_ledger_advisory=None,
                article_overview={
                    "bundles": [],
                    "summary": {
                        "bundle_count": 0,
                        "blocked_count": 0,
                        "review_missing_count": 0,
                    },
                },
                standards_profiles={},
                runtime_records=(),
            )

            next_action = state["suggested_next_action"]
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "draft-next")
            self.assertEqual(next_action["lane"], "article")
            self.assertEqual(next_action["label"], "Draft article artifact")
            self.assertEqual(next_action["command"], "launch-academic article --topic <topic>")

    def test_thesis_only_work_state_suggests_thesis_draft_command(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = build_work_state(
                root_dir=root,
                work_id="thesis-work",
                work_title="Thesis Work",
                active_lanes=("thesis",),
                thesis_overview={
                    "sections": [],
                    "summary": {
                        "section_count": 0,
                        "reviewed_count": 0,
                        "blocked_count": 0,
                    },
                },
                thesis_ledger_advisory=None,
                article_overview=None,
                standards_profiles={},
                runtime_records=(),
            )

            next_action = state["suggested_next_action"]
            self.assertIsNotNone(next_action)
            assert next_action is not None
            self.assertEqual(next_action["action_id"], "draft-next")
            self.assertEqual(next_action["lane"], "thesis")
            self.assertEqual(next_action["label"], "Draft thesis artifact")
            self.assertEqual(next_action["command"], "launch-thesis write-section <section>")


if __name__ == "__main__":
    unittest.main()
