from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .orchestrator import WorkflowOrchestrator
from .runtime_status import RuntimeRecord, load_runtime_record
from .state import RuntimeStore
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

WORK_INSERT_SQL = """
INSERT INTO works (
  work_id, title, artifact_type, active_lanes_json, status, known_blocker_count,
  suggested_next_action_json, work_state_json, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

RUN_INSERT_SQL = """
INSERT INTO runs (
  record_id, workflow_id, work_id, lane, action, status, stage, readiness_status,
  promotion_status, started_at, finished_at, summary, runtime_dir, status_path, source, record_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

BLOCKER_INSERT_SQL = """
INSERT INTO blockers (
  blocker_id, work_id, run_record_id, lane, category, code, message, repairable,
  blocks_statuses_json, source, details_json, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

ARTIFACT_INSERT_SQL = """
INSERT INTO artifacts (
  artifact_id, work_id, run_record_id, lane, artifact_type, path, "exists", source, metadata_json, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        store = RuntimeStore(self.root_dir)
        orchestrator = WorkflowOrchestrator(self.root_dir, store=store)

        work_rows: list[tuple[Any, ...]] = []
        blocker_rows: list[tuple[Any, ...]] = []
        artifact_rows: list[tuple[Any, ...]] = []
        for work_id in sorted(workspace.works):
            work = resolve_work_config(workspace, work_id=work_id)
            work_state = orchestrator.get_work_state(work_id=work.slug)
            work_rows.append(_work_row(work, work_state, refreshed_at))
            blocker_rows.extend(_work_blocker_rows(work.slug, work_state, refreshed_at))
            artifact_rows.extend(_work_artifact_rows(work.slug, work_state, refreshed_at))

        runtime_records: list[RuntimeRecord] = []
        warnings: list[dict[str, Any]] = []
        seen_record_ids: set[str] = set()
        seen_workflow_ids: set[str] = set()
        for run_dir in _runtime_record_dirs(self.root_dir, store):
            try:
                record = load_runtime_record(run_dir, "workflow-run")
            except OSError as exc:
                warnings.append(_runtime_record_warning(self.root_dir, run_dir, error=exc))
                continue
            if record is None:
                warnings.append(_runtime_record_warning(self.root_dir, run_dir))
                continue
            if record.record_id in seen_record_ids:
                continue
            if record.workflow_id and record.workflow_id in seen_workflow_ids:
                continue
            seen_record_ids.add(record.record_id)
            if record.workflow_id:
                seen_workflow_ids.add(record.workflow_id)
            runtime_records.append(record)
        run_rows = [_run_row(record) for record in runtime_records]
        for record in runtime_records:
            blocker_rows.extend(_runtime_blocker_rows(record, refreshed_at))
            artifact_rows.extend(_runtime_artifact_rows(record, refreshed_at))
        blocker_rows = _dedupe_blocker_rows(blocker_rows)
        artifact_rows = _dedupe_artifact_rows(artifact_rows)

        counts: dict[str, int]
        try:
            with closing(sqlite3.connect(self.index_path)) as conn:
                with conn:
                    conn.executescript(SCHEMA_SQL)
                    for table in ("index_metadata", "works", "runs", "blockers", "artifacts"):
                        conn.execute(f"DELETE FROM {table}")
                    conn.executemany(WORK_INSERT_SQL, work_rows)
                    conn.executemany(RUN_INSERT_SQL, run_rows)
                    conn.executemany(BLOCKER_INSERT_SQL, blocker_rows)
                    conn.executemany(ARTIFACT_INSERT_SQL, artifact_rows)
                    counts = {
                        "works_indexed": _table_count(conn, "works"),
                        "runs_indexed": _table_count(conn, "runs"),
                        "blockers_indexed": _table_count(conn, "blockers"),
                        "artifacts_indexed": _table_count(conn, "artifacts"),
                    }
                    _write_metadata(conn, refreshed_at=refreshed_at, counts=counts, warnings=warnings)
        except sqlite3.DatabaseError as exc:
            return _refresh_failed_payload(self.index_path, refreshed_at=refreshed_at, error=exc, warnings=warnings)

        return _refresh_payload(self.index_path, refreshed_at=refreshed_at, counts=counts, warnings=warnings)

    def get_index(self, *, work_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        if not self.index_path.exists():
            return _missing_payload(self.index_path)
        try:
            with closing(sqlite3.connect(self.index_path)) as conn:
                conn.row_factory = sqlite3.Row
                metadata = _metadata(conn)
                return {
                    "kind": "runtime-index",
                    "version": RUNTIME_INDEX_VERSION,
                    "status": "ready",
                    "index_path": str(self.index_path),
                    "schema_version": metadata.get("schema_version"),
                    "refreshed_at": metadata.get("refreshed_at"),
                    "works": _select_works(conn, work_id),
                    "recent_runs": _select_runs(conn, work_id, limit),
                    "blockers": _select_blockers(conn, work_id),
                    "artifacts": _select_artifacts(conn, work_id, limit),
                }
        except sqlite3.DatabaseError as exc:
            return _index_failed_payload(self.index_path, error=exc)


def _runtime_record_dirs(root_dir: Path, store: RuntimeStore) -> list[Path]:
    canonical_runs_dir = root_dir / "output" / "runs"
    canonical_dirs: list[Path] = []
    if canonical_runs_dir.exists():
        canonical_dirs = sorted((path for path in canonical_runs_dir.iterdir() if path.is_dir()), reverse=True)
    return [*canonical_dirs, *store.list_run_dirs()]


def _runtime_record_warning(root_dir: Path, run_dir: Path, *, error: OSError | None = None) -> dict[str, Any]:
    source = "runtime-store"
    try:
        run_dir.resolve().relative_to((root_dir / "output" / "runs").resolve())
        source = "canonical-run"
    except ValueError:
        pass
    warning = {
        "code": "runtime-record-unreadable",
        "source": source,
        "path": str(run_dir),
    }
    if error is not None:
        warning["error"] = str(error)
    return warning


def _work_row(work: Any, work_state: dict[str, Any], refreshed_at: str) -> tuple[Any, ...]:
    return (
        work.slug,
        work.title,
        work.artifact_type,
        _json(list(work.active_lanes)),
        _work_status(work_state),
        int(work_state.get("known_blocker_count") or 0),
        _json(work_state.get("suggested_next_action")),
        _json(work_state),
        refreshed_at,
    )


def _work_status(work_state: dict[str, Any]) -> str:
    runtime = work_state.get("runtime") if isinstance(work_state.get("runtime"), dict) else {}
    active_run = runtime.get("active_run") if isinstance(runtime, dict) else None
    if isinstance(active_run, dict):
        return "running"
    return "blocked" if int(work_state.get("known_blocker_count") or 0) else "idle"


def _run_row(record: RuntimeRecord) -> tuple[Any, ...]:
    return (
        record.record_id,
        record.workflow_id,
        record.work_id,
        record.lane,
        record.action,
        record.status,
        record.stage,
        record.readiness_status,
        record.promotion_status,
        record.started_at,
        record.finished_at,
        record.summary,
        record.runtime_dir,
        record.status_path,
        record.source,
        _json(record.to_dict()),
    )


def _blocker_row(
    *,
    blocker_id: str,
    work_id: str | None,
    run_record_id: str | None,
    lane: str | None,
    blocker: dict[str, Any],
    source: str,
    created_at: str | None,
) -> tuple[Any, ...]:
    blocks_statuses = blocker.get("blocks_statuses")
    if not isinstance(blocks_statuses, list):
        blocks_statuses = []
    return (
        blocker_id,
        work_id,
        run_record_id,
        lane,
        _text(blocker.get("category")),
        _text(blocker.get("code")),
        _text(blocker.get("message")),
        1 if blocker.get("repairable") else 0,
        _json(blocks_statuses),
        source,
        _json(blocker),
        created_at,
    )


def _work_blocker_rows(
    work_id: str,
    work_state: dict[str, Any],
    refreshed_at: str,
) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    known_blockers = work_state.get("known_blockers")
    if not isinstance(known_blockers, list):
        return rows
    for index, blocker in enumerate(known_blockers):
        if not isinstance(blocker, dict):
            continue
        category = _text(blocker.get("category")) or "unknown"
        code = _text(blocker.get("code")) or "unknown"
        rows.append(
            _blocker_row(
                blocker_id=f"work:{work_id}:{index}:{category}:{code}",
                work_id=work_id,
                run_record_id=_text(blocker.get("record_id")),
                lane=_text(blocker.get("lane")),
                blocker=blocker,
                source="work-state",
                created_at=refreshed_at,
            )
        )
    return rows


def _runtime_blocker_rows(record: RuntimeRecord, refreshed_at: str) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for index, blocker in enumerate(record.blockers):
        if not isinstance(blocker, dict):
            continue
        category = _text(blocker.get("category")) or "unknown"
        code = _text(blocker.get("code")) or "unknown"
        rows.append(
            _blocker_row(
                blocker_id=f"run:{record.record_id}:{index}:{category}:{code}",
                work_id=record.work_id,
                run_record_id=record.record_id,
                lane=record.lane,
                blocker=blocker,
                source="runtime-record",
                created_at=record.finished_at or record.started_at or refreshed_at,
            )
        )
    return rows


def _runtime_artifact_rows(record: RuntimeRecord, refreshed_at: str) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for name, payload in sorted(record.attachments.items()):
        if not isinstance(payload, dict):
            continue
        raw_path = _text(payload.get("path"))
        if not raw_path:
            continue
        rows.append(
            (
                f"run:{record.record_id}:{name}",
                record.work_id,
                record.record_id,
                record.lane,
                name,
                raw_path,
                1 if payload.get("exists") else 0,
                "runtime-attachment",
                _json(payload),
                refreshed_at,
            )
        )
    return rows


def _work_artifact_rows(work_id: str, work_state: dict[str, Any], refreshed_at: str) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for item in _artifact_dicts(work_state):
        path = _text(item.get("path"))
        if not path:
            continue
        lane = _text(item.get("lane"))
        artifact_type = _text(item.get("artifact_id")) or _text(item.get("kind")) or "work-artifact"
        rows.append(
            (
                f"work:{work_id}:{lane or 'none'}:{artifact_type}:{path}",
                work_id,
                None,
                lane,
                artifact_type,
                path,
                1 if item.get("exists") else 0,
                "work-state",
                _json(item),
                refreshed_at,
            )
        )
    return rows


def _artifact_dicts(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if "path" in value and "exists" in value:
            items.append(value)
        for child in value.values():
            items.extend(_artifact_dicts(child))
    elif isinstance(value, list):
        for child in value:
            items.extend(_artifact_dicts(child))
    return items


def _dedupe_blocker_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    by_key: dict[tuple[Any, ...], tuple[Any, ...]] = {}
    order: list[tuple[Any, ...]] = []
    for row in rows:
        key = _blocker_semantic_key(row)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = row
            order.append(key)
            continue
        if row[9] == "work-state" and existing[9] != "work-state":
            by_key[key] = row
    return [by_key[key] for key in order]


def _blocker_semantic_key(row: tuple[Any, ...]) -> tuple[Any, ...]:
    details = _load_json(row[10], {})
    return (
        row[1],
        row[3],
        row[4],
        row[5],
        row[6],
        row[8],
        _blocker_identity(details, "target"),
        _blocker_identity(details, "profile_id"),
        _blocker_identity(details, "artifact"),
        _blocker_identity(details, "artifact_id"),
        _blocker_identity(details, "path"),
    )


def _blocker_identity(payload: Any, key: str) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get(key)
    nested = payload.get("details")
    if value is None and isinstance(nested, dict):
        value = nested.get(key)
    if isinstance(value, (dict, list)):
        return _json(value)
    return _text(value)


def _dedupe_artifact_rows(rows: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    by_id: dict[Any, tuple[Any, ...]] = {}
    order: list[Any] = []
    for row in rows:
        artifact_id = row[0]
        if artifact_id in by_id:
            continue
        by_id[artifact_id] = row
        order.append(artifact_id)
    return [by_id[artifact_id] for artifact_id in order]


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    return {str(row["key"]): str(row["value"]) for row in conn.execute("SELECT key, value FROM index_metadata")}


def _write_metadata(
    conn: sqlite3.Connection,
    *,
    refreshed_at: str,
    counts: dict[str, int],
    warnings: list[dict[str, Any]],
) -> None:
    metadata = {
        "schema_version": RUNTIME_INDEX_SCHEMA_VERSION,
        "refreshed_at": refreshed_at,
        "warnings_count": str(len(warnings)),
        **{key: str(value) for key, value in counts.items()},
    }
    conn.executemany(
        "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
        sorted(metadata.items()),
    )


def _empty_counts() -> dict[str, int]:
    return {
        "works_indexed": 0,
        "runs_indexed": 0,
        "blockers_indexed": 0,
        "artifacts_indexed": 0,
    }


def _sqlite_error(exc: sqlite3.DatabaseError) -> dict[str, str]:
    return {
        "code": "runtime-index-sqlite-error",
        "message": str(exc),
    }


def _refresh_payload(
    index_path: Path,
    *,
    refreshed_at: str,
    counts: dict[str, int],
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "kind": "runtime-index-refresh",
        "version": RUNTIME_INDEX_VERSION,
        "status": "refreshed",
        "index_path": str(index_path),
        "schema_version": RUNTIME_INDEX_SCHEMA_VERSION,
        "refreshed_at": refreshed_at,
        "works_indexed": counts["works_indexed"],
        "runs_indexed": counts["runs_indexed"],
        "blockers_indexed": counts["blockers_indexed"],
        "artifacts_indexed": counts["artifacts_indexed"],
        "warnings_count": len(warnings),
        "warnings": warnings,
    }


def _refresh_failed_payload(
    index_path: Path,
    *,
    refreshed_at: str,
    error: sqlite3.DatabaseError,
    warnings: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = _refresh_payload(
        index_path,
        refreshed_at=refreshed_at,
        counts=_empty_counts(),
        warnings=warnings,
    )
    payload["status"] = "failed"
    payload["error"] = _sqlite_error(error)
    return payload


def _index_failed_payload(index_path: Path, *, error: sqlite3.DatabaseError) -> dict[str, Any]:
    return {
        "kind": "runtime-index",
        "version": RUNTIME_INDEX_VERSION,
        "status": "failed",
        "index_path": str(index_path),
        "schema_version": None,
        "refreshed_at": None,
        "works": [],
        "recent_runs": [],
        "blockers": [],
        "artifacts": [],
        "error": _sqlite_error(error),
    }


def _missing_payload(index_path: Path) -> dict[str, Any]:
    return {
        "kind": "runtime-index",
        "version": RUNTIME_INDEX_VERSION,
        "status": "missing",
        "index_path": str(index_path),
        "schema_version": None,
        "refreshed_at": None,
        "works": [],
        "recent_runs": [],
        "blockers": [],
        "artifacts": [],
    }


def _select_works(conn: sqlite3.Connection, work_id: str | None) -> list[dict[str, Any]]:
    if work_id:
        rows = conn.execute(
            """
            SELECT work_id, title, artifact_type, active_lanes_json, status, known_blocker_count,
                   suggested_next_action_json, work_state_json, updated_at
            FROM works
            WHERE work_id = ?
            ORDER BY work_id
            """,
            (work_id,),
        )
    else:
        rows = conn.execute(
            """
            SELECT work_id, title, artifact_type, active_lanes_json, status, known_blocker_count,
                   suggested_next_action_json, work_state_json, updated_at
            FROM works
            ORDER BY work_id
            """
        )
    return [
        {
            "work_id": row["work_id"],
            "title": row["title"],
            "artifact_type": row["artifact_type"],
            "active_lanes": _load_json(row["active_lanes_json"], []),
            "status": row["status"],
            "known_blocker_count": row["known_blocker_count"],
            "suggested_next_action": _load_json(row["suggested_next_action_json"], None),
            "work_state": _load_json(row["work_state_json"], {}),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def _select_runs(conn: sqlite3.Connection, work_id: str | None, limit: int) -> list[dict[str, Any]]:
    safe_limit = max(0, int(limit))
    if work_id:
        rows = conn.execute(
            """
            SELECT record_id, workflow_id, work_id, lane, action, status, stage, readiness_status,
                   promotion_status, started_at, finished_at, summary, runtime_dir, status_path, source, record_json
            FROM runs
            WHERE work_id = ?
            ORDER BY COALESCE(finished_at, started_at, '') DESC, record_id DESC
            LIMIT ?
            """,
            (work_id, safe_limit),
        )
    else:
        rows = conn.execute(
            """
            SELECT record_id, workflow_id, work_id, lane, action, status, stage, readiness_status,
                   promotion_status, started_at, finished_at, summary, runtime_dir, status_path, source, record_json
            FROM runs
            ORDER BY COALESCE(finished_at, started_at, '') DESC, record_id DESC
            LIMIT ?
            """,
            (safe_limit,),
        )
    return [
        {
            "record_id": row["record_id"],
            "workflow_id": row["workflow_id"],
            "work_id": row["work_id"],
            "lane": row["lane"],
            "action": row["action"],
            "status": row["status"],
            "stage": row["stage"],
            "readiness_status": row["readiness_status"],
            "promotion_status": row["promotion_status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "summary": row["summary"],
            "runtime_dir": row["runtime_dir"],
            "status_path": row["status_path"],
            "source": row["source"],
            "record": _load_json(row["record_json"], {}),
        }
        for row in rows
    ]


def _select_blockers(conn: sqlite3.Connection, work_id: str | None) -> list[dict[str, Any]]:
    if work_id:
        rows = conn.execute(
            """
            SELECT blocker_id, work_id, run_record_id, lane, category, code, message, repairable,
                   blocks_statuses_json, source, details_json, created_at
            FROM blockers
            WHERE work_id = ?
            ORDER BY blocker_id
            """,
            (work_id,),
        )
    else:
        rows = conn.execute(
            """
            SELECT blocker_id, work_id, run_record_id, lane, category, code, message, repairable,
                   blocks_statuses_json, source, details_json, created_at
            FROM blockers
            ORDER BY blocker_id
            """
        )
    return [
        {
            "blocker_id": row["blocker_id"],
            "work_id": row["work_id"],
            "run_record_id": row["run_record_id"],
            "lane": row["lane"],
            "category": row["category"],
            "code": row["code"],
            "message": row["message"],
            "repairable": bool(row["repairable"]),
            "blocks_statuses": _load_json(row["blocks_statuses_json"], []),
            "source": row["source"],
            "details": _load_json(row["details_json"], {}),
            "created_at": row["created_at"],
        }
        for row in rows
    ]


def _select_artifacts(conn: sqlite3.Connection, work_id: str | None, limit: int) -> list[dict[str, Any]]:
    safe_limit = max(0, int(limit))
    if work_id:
        rows = conn.execute(
            """
            SELECT artifact_id, work_id, run_record_id, lane, artifact_type, path, "exists",
                   source, metadata_json, updated_at
            FROM artifacts
            WHERE work_id = ?
            ORDER BY updated_at DESC, source, artifact_id
            LIMIT ?
            """,
            (work_id, safe_limit),
        )
    else:
        rows = conn.execute(
            """
            SELECT artifact_id, work_id, run_record_id, lane, artifact_type, path, "exists",
                   source, metadata_json, updated_at
            FROM artifacts
            ORDER BY updated_at DESC, source, artifact_id
            LIMIT ?
            """,
            (safe_limit,),
        )
    return [
        {
            "artifact_id": row["artifact_id"],
            "work_id": row["work_id"],
            "run_record_id": row["run_record_id"],
            "lane": row["lane"],
            "artifact_type": row["artifact_type"],
            "path": row["path"],
            "exists": bool(row["exists"]),
            "source": row["source"],
            "metadata": _load_json(row["metadata_json"], {}),
            "updated_at": row["updated_at"],
        }
        for row in rows
    ]


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _load_json(value: str | None, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
