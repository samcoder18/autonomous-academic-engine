#!/usr/bin/env python3
"""CLI wrapper for telegram_console.docx_preview."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))


if __name__ == "__main__":
    from telegram_console.docx_preview import main

    raise SystemExit(main())
