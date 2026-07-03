from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from .utils import utc_now
from .workspace import load_workspace_config, resolve_work_config

RUNTIME_INDEX_VERSION = "v1"
RUNTIME_INDEX_SCHEMA_VERSION = "1"
RUNTIME_INDEX_FILENAME = "runtime-index.sqlite"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS index_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS works (
  work_id TEXT PRIMARY KEY,
  title TEXT,
  artifact_type TEXT,
  active_lanes_json TEXT NOT NULL,
  status TEXT NOT NULL,
  known_blocker_count INTEGER NOT NULL,
  suggested_next_action_json TEXT,
  work_state_json TEXT NOT NULL,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  record_id TEXT PRIMARY KEY,
  workflow_id TEXT,
  work_id TEXT,
  lane TEXT,
  action TEXT,
  status TEXT NOT NULL,
  stage TEXT,
  readiness_status TEXT,
  promotion_status TEXT,
  started_at TEXT,
  finished_at TEXT,
  summary TEXT,
  runtime_dir TEXT,
  status_path TEXT,
  source TEXT,
  record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS blockers (
  blocker_id TEXT PRIMARY KEY,
  work_id TEXT,
  run_record_id TEXT,
  lane TEXT,
  category TEXT,
  code TEXT,
  message TEXT,
  repairable INTEGER NOT NULL DEFAULT 0,
  blocks_statuses_json TEXT NOT NULL,
  source TEXT NOT NULL,
  details_json TEXT NOT NULL,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS artifacts (
  artifact_id TEXT PRIMARY KEY,
  work_id TEXT,
  run_record_id TEXT,
  lane TEXT,
  artifact_type TEXT NOT NULL,
  path TEXT NOT NULL,
  "exists" INTEGER NOT NULL,
  source TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  updated_at TEXT
);
"""


def runtime_index_path(root_dir: str | Path) -> Path:
    return Path(root_dir).expanduser().resolve() / "output" / "runtime" / RUNTIME_INDEX_FILENAME


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class RuntimeIndex:
    def __init__(self, root_dir: str | Path, *, index_path: str | Path | None = None) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self.index_path = Path(index_path).expanduser().resolve() if index_path else runtime_index_path(self.root_dir)

    def refresh(self) -> dict[str, Any]:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        refreshed_at = utc_now()
        workspace = load_workspace_config(self.root_dir)

        works_indexed = 0
        with sqlite3.connect(self.index_path) as conn:
            conn.executescript(SCHEMA_SQL)
            for table in ("index_metadata", "works", "runs", "blockers", "artifacts"):
                conn.execute(f"DELETE FROM {table}")

            for work_id in sorted(workspace.works):
                work = resolve_work_config(workspace, work_id=work_id)
                conn.execute(
                    """
                    INSERT INTO works (
                      work_id,
                      title,
                      artifact_type,
                      active_lanes_json,
                      status,
                      known_blocker_count,
                      suggested_next_action_json,
                      work_state_json,
                      updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        work.slug,
                        work.title,
                        work.artifact_type,
                        _json(list(work.active_lanes)),
                        "indexed",
                        0,
                        _json(None),
                        _json({}),
                        refreshed_at,
                    ),
                )
                works_indexed += 1

            metadata = {
                "schema_version": RUNTIME_INDEX_SCHEMA_VERSION,
                "refreshed_at": refreshed_at,
                "works_indexed": str(works_indexed),
                "runs_indexed": "0",
                "blockers_indexed": "0",
                "artifacts_indexed": "0",
            }
            conn.executemany(
                "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
                sorted(metadata.items()),
            )

        return {
            "kind": "runtime-index-refresh",
            "version": RUNTIME_INDEX_VERSION,
            "status": "refreshed",
            "index_path": str(self.index_path),
            "schema_version": RUNTIME_INDEX_SCHEMA_VERSION,
            "refreshed_at": refreshed_at,
            "works_indexed": works_indexed,
            "runs_indexed": 0,
            "blockers_indexed": 0,
            "artifacts_indexed": 0,
        }

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
