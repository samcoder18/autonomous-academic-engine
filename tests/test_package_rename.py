from __future__ import annotations

import importlib
import unittest
from pathlib import Path


class PackageRenameTests(unittest.TestCase):
    def test_academic_engine_is_primary_package(self) -> None:
        root = Path(__file__).resolve().parents[1]

        self.assertTrue((root / "academic_engine").is_dir())
        old_package_name = "telegram" + "_console"
        self.assertFalse((root / old_package_name).exists())

        module = importlib.import_module("academic_engine.work_cli")
        self.assertTrue(callable(module.main))
