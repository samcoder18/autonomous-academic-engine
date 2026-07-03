from __future__ import annotations

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
