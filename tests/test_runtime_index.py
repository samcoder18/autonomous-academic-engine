from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from academic_engine.runtime_index import RuntimeIndex, _work_artifact_rows, runtime_index_path
from academic_engine.runtime_status import build_runtime_status, write_status


class RuntimeIndexPathTests(unittest.TestCase):
    def test_default_index_path_lives_under_output_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            self.assertEqual(runtime_index_path(root), root.resolve() / "output" / "runtime" / "runtime-index.sqlite")


class RuntimeIndexMissingDatabaseTests(unittest.TestCase):
    def test_get_index_reports_missing_without_claiming_fresh_data(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            index = RuntimeIndex(root)

            payload = index.get_index()

            self.assertEqual(payload["kind"], "runtime-index")
            self.assertEqual(payload["version"], "v1")
            self.assertEqual(payload["status"], "missing")
            self.assertEqual(payload["refreshed_at"], None)
            self.assertEqual(payload["works"], [])
            self.assertEqual(payload["recent_runs"], [])
            self.assertEqual(payload["blockers"], [])
            self.assertEqual(payload["artifacts"], [])
            self.assertFalse(runtime_index_path(root).exists())


MINIMAL_WORKSPACE_TOML = """\
version = 1
default_work = "starter-work"
supported_lanes = ["thesis", "article"]

[default_profiles]
thesis = "thesis-v1"
article = "ru-law-article-v1"

[outputs]
runs_dir = "output/runs"
docx_dir = "output/docx"

[works]
starter-work = "works/starter-work"
"""


def prepare_minimal_workspace(root: Path) -> None:
    (root / "workspace.toml").write_text(MINIMAL_WORKSPACE_TOML, encoding="utf-8")
    standards_dir = root / "meta" / "standards"
    (standards_dir / "normalized").mkdir(parents=True, exist_ok=True)
    (standards_dir / "normalized" / "ru-law-article-v1.md").write_text("# Article profile\n", encoding="utf-8")
    raw_dir = standards_dir / "raw" / "ru-law-article-v1"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "manifest.json").write_text(
        json.dumps(
            {
                "profile_id": "ru-law-article-v1",
                "synced_at": "2026-07-03T10:00:00+00:00",
                "sources": [],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    work_dir = root / "works" / "starter-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "work.toml").write_text(
        'slug = "starter-work"\n'
        'title = "Starter work"\n'
        'artifact_type = "article"\n'
        'active_lanes = ["article"]\n'
        'language = "ru"\n'
        'topic = "Demo topic"\n'
        'work_canon = "work-canon.md"\n'
        '\n[article]\n'
        'profile = "ru-law-article-v1"\n'
        'root_dir = "articles"\n'
        'docx_subdir = "articles"\n'
        'briefs_dir = "articles/briefs"\n'
        'evidence_dir = "articles/evidence"\n'
        'claim_maps_dir = "articles/claim-maps"\n'
        'drafts_dir = "articles/drafts"\n'
        'reviews_dir = "articles/reviews"\n'
        'final_dir = "articles/final"\n'
        '[article.paths]\n'
        'briefs = "articles/briefs"\n'
        'evidence = "articles/evidence"\n'
        'drafts = "articles/drafts"\n'
        'reviews = "articles/reviews"\n'
        'final = "articles/final"\n'
        'checklists = "articles/checklists"\n'
        'output_runs_dir = "output/runs/starter-work/article"\n',
        encoding="utf-8",
    )
    (work_dir / "work-canon.md").write_text("# Starter work\n", encoding="utf-8")


class RuntimeIndexRefreshMetadataTests(unittest.TestCase):
    def test_refresh_creates_sqlite_schema_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)

            payload = RuntimeIndex(root).refresh()

            self.assertEqual(payload["kind"], "runtime-index-refresh")
            self.assertEqual(payload["version"], "v1")
            self.assertEqual(payload["status"], "refreshed")
            self.assertEqual(payload["works_indexed"], 1)
            self.assertTrue(runtime_index_path(root).exists())
            with sqlite3.connect(runtime_index_path(root)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertGreaterEqual(
                    tables, {"index_metadata", "works", "runs", "blockers", "artifacts"}
                )
                refreshed_at = conn.execute(
                    "SELECT value FROM index_metadata WHERE key = 'refreshed_at'"
                ).fetchone()
                self.assertIsNotNone(refreshed_at)


def write_runtime_fixture(root: Path) -> Path:
    run_dir = root / "output" / "runtime" / "runs" / "article-review-runtime"
    artifact_path = root / "works" / "starter-work" / "articles" / "drafts" / "demo.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("# Draft\n", encoding="utf-8")
    write_status(
        run_dir / "status.json",
        build_runtime_status(
            record_id="default:20260703-article-review",
            entity_kind="workflow-run",
            status="succeeded",
            stage="completed",
            project_id="default",
            project_title=root.name,
            project_root=str(root.resolve()),
            work_id="starter-work",
            work_title="Starter work",
            lane="article",
            action="review",
            started_at="2026-07-03T10:00:00+00:00",
            finished_at="2026-07-03T10:05:00+00:00",
            summary="Article review found a blocker.",
            blockers=[
                {
                    "category": "primary-support",
                    "code": "missing-evidence",
                    "message": "Evidence pack is missing.",
                    "repairable": True,
                    "blocks_statuses": ["submission-ready"],
                }
            ],
            attachments={
                "draft": {"path": str(artifact_path), "exists": True},
                "missing-evidence": {
                    "path": str(root / "works" / "starter-work" / "articles" / "evidence" / "demo.md"),
                    "exists": False,
                },
            },
        ),
    )
    return run_dir


class RuntimeIndexRefreshContentTests(unittest.TestCase):
    def test_refresh_indexes_work_state_runs_blockers_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)

            refresh = RuntimeIndex(root).refresh()
            payload = RuntimeIndex(root).get_index()

            self.assertEqual(refresh["works_indexed"], 1)
            self.assertEqual(refresh["runs_indexed"], 1)
            self.assertGreaterEqual(refresh["blockers_indexed"], 1)
            self.assertGreaterEqual(refresh["artifacts_indexed"], 2)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["works"][0]["work_id"], "starter-work")
            self.assertEqual(payload["works"][0]["known_blocker_count"], 1)
            self.assertEqual(payload["recent_runs"][0]["record_id"], "default:20260703-article-review")
            self.assertEqual(payload["recent_runs"][0]["status"], "succeeded")
            self.assertEqual(payload["blockers"][0]["code"], "missing-evidence")
            artifact_paths = {item["path"] for item in payload["artifacts"]}
            self.assertTrue(any(path.endswith("articles/drafts/demo.md") for path in artifact_paths))
            self.assertTrue(any(path.endswith("articles/evidence/demo.md") for path in artifact_paths))

    def test_refresh_deduplicates_runtime_blockers_in_flat_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)

            refresh = RuntimeIndex(root).refresh()
            payload = RuntimeIndex(root).get_index()

            self.assertEqual(refresh["blockers_indexed"], 1)
            self.assertEqual(len(payload["blockers"]), 1)
            self.assertEqual(payload["blockers"][0]["source"], "work-state")
            self.assertEqual(payload["blockers"][0]["code"], "missing-evidence")

    def test_artifact_hydration_preserves_exists_boolean(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)

            RuntimeIndex(root).refresh()
            payload = RuntimeIndex(root).get_index()

            draft = next(item for item in payload["artifacts"] if item["path"].endswith("articles/drafts/demo.md"))
            evidence = next(item for item in payload["artifacts"] if item["path"].endswith("articles/evidence/demo.md"))
            self.assertIs(draft["exists"], True)
            self.assertIs(evidence["exists"], False)

    def test_get_index_filters_unknown_work_to_empty_payloads(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)

            RuntimeIndex(root).refresh()
            payload = RuntimeIndex(root).get_index(work_id="missing-work")

            self.assertEqual(payload["works"], [])
            self.assertEqual(payload["recent_runs"], [])
            self.assertEqual(payload["blockers"], [])
            self.assertEqual(payload["artifacts"], [])

    def test_zero_limit_keeps_works_and_blockers_but_omits_runs_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)

            RuntimeIndex(root).refresh()
            payload = RuntimeIndex(root).get_index(limit=0)

            self.assertEqual(payload["works"][0]["work_id"], "starter-work")
            self.assertGreaterEqual(len(payload["blockers"]), 1)
            self.assertEqual(payload["recent_runs"], [])
            self.assertEqual(payload["artifacts"], [])

    def test_work_artifact_rows_ignore_path_dicts_without_exists_flag(self) -> None:
        rows = _work_artifact_rows(
            "starter-work",
            {
                "note": {"path": "works/starter-work/readme.md"},
                "artifact": {"path": "works/starter-work/articles/drafts/demo.md", "exists": True},
            },
            "2026-07-03T10:00:00+00:00",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][5], "works/starter-work/articles/drafts/demo.md")
        self.assertEqual(rows[0][6], 1)

    def test_malformed_json_columns_fall_back_to_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)
            RuntimeIndex(root).refresh()

            with sqlite3.connect(runtime_index_path(root)) as conn:
                conn.execute(
                    "UPDATE works SET active_lanes_json = ? WHERE work_id = ?",
                    ("{malformed", "starter-work"),
                )
                conn.execute(
                    "UPDATE runs SET record_json = ? WHERE record_id = ?",
                    ("{malformed", "default:20260703-article-review"),
                )

            payload = RuntimeIndex(root).get_index()

            self.assertEqual(payload["works"][0]["active_lanes"], [])
            self.assertEqual(payload["recent_runs"][0]["record"], {})

    def test_delete_index_does_not_change_work_status_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)
            RuntimeIndex(root).refresh()
            before = json.dumps(RuntimeIndex(root).get_index()["works"][0]["work_state"], sort_keys=True)

            runtime_index_path(root).unlink()
            missing = RuntimeIndex(root).get_index()
            RuntimeIndex(root).refresh()
            after = json.dumps(RuntimeIndex(root).get_index()["works"][0]["work_state"], sort_keys=True)

            self.assertEqual(missing["status"], "missing")
            self.assertEqual(before, after)
