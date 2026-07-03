from __future__ import annotations

from pathlib import Path
from typing import Any

RUNTIME_INDEX_VERSION = "v1"
RUNTIME_INDEX_SCHEMA_VERSION = "1"
RUNTIME_INDEX_FILENAME = "runtime-index.sqlite"


def runtime_index_path(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / "output" / "runtime" / RUNTIME_INDEX_FILENAME


class RuntimeIndex:
    def __init__(self, root_dir: str | Path, *, index_path: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.index_path = Path(index_path).expanduser().resolve() if index_path else runtime_index_path(self.root_dir)

    def get_index(self, *, work_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        if not self.index_path.exists():
            return {
                "kind": "runtime-index",
                "version": RUNTIME_INDEX_VERSION,
                "status": "missing",
                "index_path": str(self.index_path),
                "schema_version": None,
                "refreshed_at": None,
                "works": [],
                "recent_runs": [],
                "blockers": [],
                "artifacts": [],
            }
        return {
            "kind": "runtime-index",
            "version": RUNTIME_INDEX_VERSION,
            "status": "ready",
            "index_path": str(self.index_path),
            "schema_version": RUNTIME_INDEX_SCHEMA_VERSION,
            "refreshed_at": None,
            "works": [],
            "recent_runs": [],
            "blockers": [],
            "artifacts": [],
        }
