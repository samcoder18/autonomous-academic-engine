from __future__ import annotations

import unittest
from pathlib import Path

from telegram_console.standards import load_standards_registry


class DissertationStandardsRegistryTests(unittest.TestCase):
    def test_dissertation_profiles_have_explicit_workflow_binding(self) -> None:
        root = Path(__file__).resolve().parents[1]
        registry = load_standards_registry(root)
        self.assertIsNone(registry.profiles["rf-dissertation-general"].workflow_lane)
        self.assertEqual(registry.profiles["rf-dissertation-candidate"].workflow_lane, "thesis")
        self.assertEqual(registry.profiles["rf-dissertation-doctor"].workflow_lane, "thesis")
        self.assertTrue(registry.profiles["rf-dissertation-candidate"].normalized_path.exists())


if __name__ == "__main__":
    unittest.main()
