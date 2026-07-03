from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from academic_engine.runtime_index import RuntimeIndex, runtime_index_path


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
