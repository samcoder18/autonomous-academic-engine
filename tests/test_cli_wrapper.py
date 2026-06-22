from __future__ import annotations

import os
import unittest
from pathlib import Path


class WorkCliWrapperTests(unittest.TestCase):
    def test_work_cli_wrapper_is_documentable_entrypoint(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = root / "scripts" / "work_cli.sh"

        self.assertTrue(script.exists())
        self.assertTrue(os.access(script, os.X_OK))

        content = script.read_text(encoding="utf-8")
        self.assertIn(
            'ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
            content,
        )
        self.assertIn('export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"', content)
        self.assertIn('python3 -m academic_engine.work_cli "$@"', content)
