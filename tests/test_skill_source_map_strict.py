from __future__ import annotations

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from academic_engine import work_cli
from academic_engine.skill_source_map import (
    audit_skill_source_map,
    load_skill_source_map,
    sync_external_skill_sources,
)


class StrictSkillSourceMapTests(unittest.TestCase):
    def test_audit_reports_every_missing_external_skill(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        expected_skills = set(load_skill_source_map(repo_root))

        with TemporaryDirectory() as tempdir:
            report = audit_skill_source_map(repo_root, external_skills_root=tempdir)

        missing_skills = {issue.skill_name for issue in report.issues if issue.code == "missing-external-skill"}
        self.assertEqual(missing_skills, expected_skills)
        self.assertFalse(report.ok)

    def test_cli_audit_outputs_missing_external_skill(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        stdout = StringIO()

        with TemporaryDirectory() as tempdir, redirect_stdout(stdout):
            code = work_cli.main(
                [
                    "skill-source-map",
                    "audit",
                    "--skills-root",
                    tempdir,
                    "--json",
                ],
                root_dir=repo_root,
            )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertFalse(payload["ok"])
        self.assertIn(
            "missing-external-skill",
            {issue["code"] for issue in payload["issues"]},
        )

    def test_write_sync_bootstraps_empty_root_for_clean_audit(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]

        with TemporaryDirectory() as tempdir:
            sync_report = sync_external_skill_sources(repo_root, tempdir, write=True)
            audit_report = audit_skill_source_map(repo_root, external_skills_root=tempdir)

        self.assertGreater(sync_report.updated_count, 0)
        self.assertEqual(sync_report.missing_external_count, 0)
        self.assertTrue(audit_report.ok, msg=[issue.message for issue in audit_report.issues])


if __name__ == "__main__":
    unittest.main()
