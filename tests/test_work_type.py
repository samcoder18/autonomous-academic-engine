"""Tests for telegram_console.work_type."""

from __future__ import annotations

import unittest
from textwrap import dedent

from telegram_console.work_type import (
    available_profiles,
    resolve_profile,
    validate_structure,
    validate_to_blockers,
)


class ProfileRegistryTests(unittest.TestCase):
    def test_available_profiles_covers_all_key_types(self) -> None:
        identifiers = {profile.identifier for profile in available_profiles()}
        for expected in (
            "article",
            "vkr-bachelor",
            "master-thesis",
            "dissertation-candidate",
            "dissertation-doctor",
        ):
            self.assertIn(expected, identifiers)

    def test_resolve_profile_accepts_legacy_alias(self) -> None:
        profile = resolve_profile("vkr")
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.identifier, "vkr-bachelor")

    def test_resolve_profile_returns_none_for_unknown(self) -> None:
        self.assertIsNone(resolve_profile("unknown-type"))

    def test_resolve_profile_none_for_empty_string(self) -> None:
        self.assertIsNone(resolve_profile(""))

    def test_dissertation_candidate_profile_exposes_contour_requirements(self) -> None:
        profile = resolve_profile("dissertation-candidate")
        self.assertIsNotNone(profile)
        assert profile is not None
        self.assertEqual(profile.artifact_family, "dissertation")
        self.assertTrue(profile.requires_author_abstract)
        self.assertTrue(profile.requires_publication_evidence)
        self.assertTrue(profile.requires_counterargument_pass)
        self.assertIn("historiography-map", profile.required_artifact_groups)
        self.assertIn("publication-claim-matrix", profile.required_artifact_groups)
        self.assertIn("dissertation-review", profile.required_review_artifacts)


class ValidateStructureTests(unittest.TestCase):
    def test_vkr_bachelor_requires_all_sections(self) -> None:
        profile = resolve_profile("vkr-bachelor")
        assert profile is not None
        markdown = dedent(
            """\
            # Title

            ## Введение

            ## Глава 1

            ## Заключение

            ## Список использованных источников
            """
        )
        issues = validate_structure(markdown, profile)
        codes = {issue.code for issue in issues}
        self.assertIn("required-section-missing", codes)

    def test_valid_vkr_bachelor_has_only_entry_count_blocker(self) -> None:
        profile = resolve_profile("vkr-bachelor")
        assert profile is not None
        markdown = dedent(
            """\
            # Title

            ## Введение

            ## Глава 1

            ## Глава 2

            ## Глава 3

            ## Заключение

            ## Список использованных источников

            1. Запись / Иванов И. И. — Москва, 2024.
            """
        )
        issues = validate_structure(markdown, profile)
        codes = [issue.code for issue in issues]
        self.assertIn("bibliography-insufficient-entries", codes)
        self.assertNotIn("required-section-missing", codes)

    def test_enough_entries_removes_blocker(self) -> None:
        profile = resolve_profile("article")
        assert profile is not None
        entries = "\n".join(f"{i}. Работа / Автор И. — Город, 2024." for i in range(1, 15))
        markdown = (
            "# Title\n\n"
            "## Введение\n\n"
            "## Основная часть\n\n"
            "## Заключение\n\n"
            "## Список использованных источников\n\n"
            f"{entries}\n"
        )
        issues = validate_structure(markdown, profile)
        self.assertEqual(issues, [])

    def test_validate_to_blockers_returns_blocker_objects(self) -> None:
        profile = resolve_profile("vkr-bachelor")
        assert profile is not None
        blockers = validate_to_blockers("# Empty\n", profile)
        self.assertTrue(blockers)
        self.assertTrue(all(b.category == "work-type-structure" for b in blockers))

    def test_doctor_profile_requires_four_chapters(self) -> None:
        profile = resolve_profile("dissertation-doctor")
        assert profile is not None
        markdown = dedent(
            """\
            # Title

            ## Введение

            ## Глава 1

            ## Глава 2

            ## Глава 3

            ## Заключение

            ## Список использованных источников

            1. Источник / Автор И. И. — Москва, 2024.
            """
        )
        issues = validate_structure(markdown, profile)
        codes = {issue.code for issue in issues}
        self.assertIn("chapter-count-insufficient", codes)


if __name__ == "__main__":
    unittest.main()
