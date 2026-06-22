#!/usr/bin/env python3
# ruff: noqa: E402, I001

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from academic_engine.bot import main


if __name__ == "__main__":
    raise SystemExit(main())
