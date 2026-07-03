# Runtime State Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a rebuildable SQLite runtime-state index under `output/runtime/` and add named regression fixtures for critical blocking scenarios.

**Architecture:** Add a focused `academic_engine.runtime_index` module that reads existing file-first engine sources and writes a cache-only SQLite projection. Expose it through `EngineService` and thin CLI adapters; keep `WorkflowEngine`, `WorkflowOrchestrator`, `work_state`, one-shot gates, and export explanation as the decision authorities.

**Tech Stack:** Python 3.11 standard library (`sqlite3`, `json`, `tempfile`, `pathlib`), existing `RuntimeStore`, `runtime_status.load_runtime_record`, `WorkflowOrchestrator`, `EngineService`, `unittest`, and existing fake workspace helpers.

---

## File Structure

- Create: `academic_engine/runtime_index.py`
  - Owns schema creation, refresh, query payloads, blocker/artifact flattening, and JSON serialization for SQLite rows.
- Modify: `academic_engine/engine_service.py`
  - Adds `refresh_runtime_index()` and `get_runtime_index()` service methods.
- Modify: `academic_engine/work_cli.py`
  - Adds `runtime-index refresh` and `runtime-index status` commands that call `EngineService`.
- Create: `tests/test_runtime_index.py`
  - Covers schema, missing database behavior, refresh counts, work/run/blocker/artifact extraction, and cache-only behavior.
- Modify: `tests/test_engine_service.py`
  - Covers the new service methods and default runtime-index path.
- Modify: `tests/test_work_cli_runtime.py`
  - Covers the new CLI commands and human/JSON output.
- Create: `tests/test_runtime_regression_fixtures.py`
  - Adds named scenario fixtures requested by the user.
- Modify: `output/README.md`
  - Documents `output/runtime/runtime-index.sqlite` as a local rebuildable cache.

---

### Task 1: Runtime Index Skeleton and Missing-Index Query

**Files:**
- Create: `academic_engine/runtime_index.py`
- Create: `tests/test_runtime_index.py`

- [ ] **Step 1: Write the failing tests**

Add `tests/test_runtime_index.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from academic_engine.runtime_index import RuntimeIndex, runtime_index_path


class RuntimeIndexPathTests(unittest.TestCase):
    def test_default_index_path_lives_under_output_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)

            self.assertEqual(runtime_index_path(root), root.resolve() / "output" / "runtime" / "runtime-index.sqlite")


class RuntimeIndexMissingDatabaseTests(unittest.TestCase):
    def test_get_index_reports_missing_without_claiming_fresh_data(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            index = RuntimeIndex(root)

            payload = index.get_index()

            self.assertEqual(payload["kind"], "runtime-index")
            self.assertEqual(payload["version"], "v1")
            self.assertEqual(payload["status"], "missing")
            self.assertEqual(payload["refreshed_at"], None)
            self.assertEqual(payload["works"], [])
            self.assertEqual(payload["recent_runs"], [])
            self.assertEqual(payload["blockers"], [])
            self.assertEqual(payload["artifacts"], [])
            self.assertFalse(runtime_index_path(root).exists())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_runtime_index.py::RuntimeIndexPathTests::test_default_index_path_lives_under_output_runtime tests/test_runtime_index.py::RuntimeIndexMissingDatabaseTests::test_get_index_reports_missing_without_claiming_fresh_data -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'academic_engine.runtime_index'`.

- [ ] **Step 3: Implement minimal skeleton**

Create `academic_engine/runtime_index.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_runtime_index.py::RuntimeIndexPathTests::test_default_index_path_lives_under_output_runtime tests/test_runtime_index.py::RuntimeIndexMissingDatabaseTests::test_get_index_reports_missing_without_claiming_fresh_data -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/runtime_index.py tests/test_runtime_index.py
git commit -m "feat: add runtime index skeleton"
```

---

### Task 2: SQLite Schema and Refresh Metadata

**Files:**
- Modify: `academic_engine/runtime_index.py`
- Modify: `tests/test_runtime_index.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_runtime_index.py`:

```python
import sqlite3


MINIMAL_WORKSPACE_TOML = """\
version = 1
default_work = "starter-work"
supported_lanes = ["thesis", "article"]

[default_profiles]
thesis = "thesis-v1"
article = "ru-law-article-v1"

[outputs]
runs_dir = "output/runs"
docx_dir = "output/docx"

[works]
starter-work = "works/starter-work"
"""


def prepare_minimal_workspace(root: Path) -> None:
    (root / "workspace.toml").write_text(MINIMAL_WORKSPACE_TOML, encoding="utf-8")
    work_dir = root / "works" / "starter-work"
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "work.toml").write_text(
        'slug = "starter-work"\n'
        'title = "Starter work"\n'
        'artifact_type = "article"\n'
        'active_lanes = ["article"]\n'
        'language = "ru"\n'
        'topic = "Demo topic"\n'
        '\n[article]\n'
        'profile = "ru-law-article-v1"\n'
        '[article.paths]\n'
        'briefs = "articles/briefs"\n'
        'evidence = "articles/evidence"\n'
        'drafts = "articles/drafts"\n'
        'reviews = "articles/reviews"\n'
        'final = "articles/final"\n'
        'checklists = "articles/checklists"\n'
        'output_runs_dir = "output/runs/starter-work/article"\n',
        encoding="utf-8",
    )
    (work_dir / "work-canon.md").write_text("# Starter work\n", encoding="utf-8")


class RuntimeIndexRefreshMetadataTests(unittest.TestCase):
    def test_refresh_creates_sqlite_schema_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)

            payload = RuntimeIndex(root).refresh()

            self.assertEqual(payload["kind"], "runtime-index-refresh")
            self.assertEqual(payload["version"], "v1")
            self.assertEqual(payload["status"], "refreshed")
            self.assertEqual(payload["works_indexed"], 1)
            self.assertTrue(runtime_index_path(root).exists())
            with sqlite3.connect(runtime_index_path(root)) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertGreaterEqual(tables, {"index_metadata", "works", "runs", "blockers", "artifacts"})
                refreshed_at = conn.execute(
                    "SELECT value FROM index_metadata WHERE key = 'refreshed_at'"
                ).fetchone()
                self.assertIsNotNone(refreshed_at)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_runtime_index.py::RuntimeIndexRefreshMetadataTests::test_refresh_creates_sqlite_schema_and_metadata -q
```

Expected: FAIL with `AttributeError: 'RuntimeIndex' object has no attribute 'refresh'`.

- [ ] **Step 3: Implement schema creation and metadata refresh**

Modify `academic_engine/runtime_index.py`:

```python
import json
import sqlite3

from .utils import utc_now
from .workspace import load_workspace_config, resolve_work_config


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
  exists INTEGER NOT NULL,
  source TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  updated_at TEXT
);
"""


    def refresh(self) -> dict[str, Any]:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        refreshed_at = utc_now()
        workspace = load_workspace_config(self.root_dir)
        works_indexed = 0
        with sqlite3.connect(self.index_path) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("DELETE FROM index_metadata")
            conn.execute("DELETE FROM works")
            conn.execute("DELETE FROM runs")
            conn.execute("DELETE FROM blockers")
            conn.execute("DELETE FROM artifacts")
            for work_id in sorted(workspace.works):
                work = resolve_work_config(workspace, work_id=work_id)
                conn.execute(
                    """
                    INSERT INTO works (
                      work_id, title, artifact_type, active_lanes_json, status,
                      known_blocker_count, suggested_next_action_json, work_state_json, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
python3 -m pytest tests/test_runtime_index.py::RuntimeIndexRefreshMetadataTests::test_refresh_creates_sqlite_schema_and_metadata -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/runtime_index.py tests/test_runtime_index.py
git commit -m "feat: create runtime index schema"
```

---

### Task 3: Index Work State, Runs, Blockers, and Artifacts

**Files:**
- Modify: `academic_engine/runtime_index.py`
- Modify: `tests/test_runtime_index.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_runtime_index.py`:

```python
import json

from academic_engine.runtime_status import build_runtime_status, write_status


def write_runtime_fixture(root: Path) -> Path:
    run_dir = root / "output" / "runtime" / "runs" / "article-review-runtime"
    artifact_path = root / "works" / "starter-work" / "articles" / "drafts" / "demo.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text("# Draft\n", encoding="utf-8")
    write_status(
        run_dir / "status.json",
        build_runtime_status(
            record_id="default:20260703-article-review",
            entity_kind="workflow-run",
            status="succeeded",
            stage="completed",
            project_id="default",
            project_title=root.name,
            project_root=str(root.resolve()),
            work_id="starter-work",
            work_title="Starter work",
            lane="article",
            action="review",
            started_at="2026-07-03T10:00:00+00:00",
            finished_at="2026-07-03T10:05:00+00:00",
            summary="Article review found a blocker.",
            blockers=[
                {
                    "category": "primary-support",
                    "code": "missing-evidence",
                    "message": "Evidence pack is missing.",
                    "repairable": True,
                    "blocks_statuses": ["submission-ready"],
                }
            ],
            attachments={
                "draft": {"path": str(artifact_path), "exists": True},
                "missing-evidence": {
                    "path": str(root / "works" / "starter-work" / "articles" / "evidence" / "demo.md"),
                    "exists": False,
                },
            },
        ),
    )
    return run_dir


class RuntimeIndexRefreshContentTests(unittest.TestCase):
    def test_refresh_indexes_work_state_runs_blockers_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)

            refresh = RuntimeIndex(root).refresh()
            payload = RuntimeIndex(root).get_index()

            self.assertEqual(refresh["works_indexed"], 1)
            self.assertEqual(refresh["runs_indexed"], 1)
            self.assertGreaterEqual(refresh["blockers_indexed"], 1)
            self.assertGreaterEqual(refresh["artifacts_indexed"], 2)
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["works"][0]["work_id"], "starter-work")
            self.assertEqual(payload["works"][0]["known_blocker_count"], 1)
            self.assertEqual(payload["recent_runs"][0]["record_id"], "default:20260703-article-review")
            self.assertEqual(payload["recent_runs"][0]["status"], "succeeded")
            self.assertEqual(payload["blockers"][0]["code"], "missing-evidence")
            artifact_paths = {item["path"] for item in payload["artifacts"]}
            self.assertTrue(any(path.endswith("articles/drafts/demo.md") for path in artifact_paths))
            self.assertTrue(any(path.endswith("articles/evidence/demo.md") for path in artifact_paths))

    def test_delete_index_does_not_change_work_status_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            prepare_minimal_workspace(root)
            write_runtime_fixture(root)
            RuntimeIndex(root).refresh()
            before = json.dumps(RuntimeIndex(root).get_index()["works"][0]["work_state"], sort_keys=True)

            runtime_index_path(root).unlink()
            missing = RuntimeIndex(root).get_index()
            RuntimeIndex(root).refresh()
            after = json.dumps(RuntimeIndex(root).get_index()["works"][0]["work_state"], sort_keys=True)

            self.assertEqual(missing["status"], "missing")
            self.assertEqual(before, after)
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_runtime_index.py::RuntimeIndexRefreshContentTests -q
```

Expected: FAIL because refresh does not yet inspect work state, runtime records, blockers, or artifacts.

- [ ] **Step 3: Implement indexing extraction**

Modify `academic_engine/runtime_index.py`:

```python
from .orchestrator import WorkflowOrchestrator
from .runtime_status import RuntimeRecord, load_runtime_record
from .state import RuntimeStore


    def refresh(self) -> dict[str, Any]:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        refreshed_at = utc_now()
        workspace = load_workspace_config(self.root_dir)
        orchestrator = WorkflowOrchestrator(self.root_dir)
        store = RuntimeStore(self.root_dir)
        work_rows: list[tuple[Any, ...]] = []
        run_rows: list[tuple[Any, ...]] = []
        blocker_rows: list[tuple[Any, ...]] = []
        artifact_rows: list[tuple[Any, ...]] = []

        for work_id in sorted(workspace.works):
            work = resolve_work_config(workspace, work_id=work_id)
            work_state = orchestrator.get_work_state(work_id=work.slug)
            work_rows.append(_work_row(work, work_state, refreshed_at))
            blocker_rows.extend(_work_blocker_rows(work.slug, work_state, refreshed_at))
            artifact_rows.extend(_work_artifact_rows(work.slug, work_state, refreshed_at))

        seen_records: set[str] = set()
        for run_dir in store.list_run_dirs():
            record = load_runtime_record(run_dir, "workflow-run")
            if record is None or not record.work_id:
                continue
            if record.record_id in seen_records:
                continue
            seen_records.add(record.record_id)
            run_rows.append(_run_row(record))
            blocker_rows.extend(_runtime_blocker_rows(record, refreshed_at))
            artifact_rows.extend(_runtime_artifact_rows(record, refreshed_at))

        with sqlite3.connect(self.index_path) as conn:
            conn.executescript(SCHEMA_SQL)
            conn.execute("DELETE FROM index_metadata")
            conn.execute("DELETE FROM works")
            conn.execute("DELETE FROM runs")
            conn.execute("DELETE FROM blockers")
            conn.execute("DELETE FROM artifacts")
            conn.executemany(WORK_INSERT_SQL, work_rows)
            conn.executemany(RUN_INSERT_SQL, run_rows)
            conn.executemany(BLOCKER_INSERT_SQL, blocker_rows)
            conn.executemany(ARTIFACT_INSERT_SQL, artifact_rows)
            _write_metadata(
                conn,
                refreshed_at=refreshed_at,
                works_indexed=len(work_rows),
                runs_indexed=len(run_rows),
                blockers_indexed=len(blocker_rows),
                artifacts_indexed=len(artifact_rows),
            )
        return _refresh_payload(
            self.index_path,
            refreshed_at=refreshed_at,
            works_indexed=len(work_rows),
            runs_indexed=len(run_rows),
            blockers_indexed=len(blocker_rows),
            artifacts_indexed=len(artifact_rows),
        )

    def get_index(self, *, work_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        if not self.index_path.exists():
            return _missing_payload(self.index_path)
        with sqlite3.connect(self.index_path) as conn:
            conn.row_factory = sqlite3.Row
            metadata = _metadata(conn)
            works = _select_works(conn, work_id=work_id)
            runs = _select_runs(conn, work_id=work_id, limit=limit)
            blockers = _select_blockers(conn, work_id=work_id)
            artifacts = _select_artifacts(conn, work_id=work_id, limit=limit)
        return {
            "kind": "runtime-index",
            "version": RUNTIME_INDEX_VERSION,
            "status": "ready",
            "index_path": str(self.index_path),
            "schema_version": metadata.get("schema_version"),
            "refreshed_at": metadata.get("refreshed_at"),
            "works": works,
            "recent_runs": runs,
            "blockers": blockers,
            "artifacts": artifacts,
        }
```

Also add helper functions and SQL constants:

```python
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
  artifact_id, work_id, run_record_id, lane, artifact_type, path, exists, source, metadata_json, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""
```

Implement the helper functions used by the methods with deterministic ids:

```python
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
    active_run = ((work_state.get("runtime") or {}).get("active_run") if isinstance(work_state.get("runtime"), dict) else None)
    if isinstance(active_run, dict):
        return "running"
    return "blocked" if int(work_state.get("known_blocker_count") or 0) else "ready"


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
```

Use `details_json` to preserve all blocker fields:

```python
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
```

Use attachments and visible work-state artifact entries:

```python
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
```

For work-state artifacts, recursively collect dictionaries with a string `path` and boolean-like `exists`:

```python
def _work_artifact_rows(work_id: str, work_state: dict[str, Any], refreshed_at: str) -> list[tuple[Any, ...]]:
    rows: list[tuple[Any, ...]] = []
    for index, item in enumerate(_artifact_dicts(work_state)):
        path = _text(item.get("path"))
        if not path:
            continue
        lane = _text(item.get("lane"))
        artifact_type = _text(item.get("artifact_id")) or _text(item.get("kind")) or "work-artifact"
        rows.append(
            (
                f"work:{work_id}:{index}:{path}",
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
python3 -m pytest tests/test_runtime_index.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/runtime_index.py tests/test_runtime_index.py
git commit -m "feat: index runtime state projections"
```

---

### Task 4: EngineService Runtime Index Facade

**Files:**
- Modify: `academic_engine/engine_service.py`
- Modify: `tests/test_engine_service.py`

- [ ] **Step 1: Write the failing tests**

Append to `EngineServiceDelegationTests` in `tests/test_engine_service.py`:

```python
    def test_runtime_index_methods_use_default_runtime_index(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            _prepare_workspace(root)
            EngineService(root).create_work(
                CreateWorkRequest(slug="index-demo", title="Index demo", artifact_type="article")
            )

            refresh = EngineService(root).refresh_runtime_index()
            payload = EngineService(root).get_runtime_index(work_id="index-demo", limit=5)

            self.assertEqual(refresh["kind"], "runtime-index-refresh")
            self.assertEqual(refresh["status"], "refreshed")
            self.assertEqual(payload["kind"], "runtime-index")
            self.assertEqual(payload["status"], "ready")
            self.assertEqual([item["work_id"] for item in payload["works"]], ["index-demo"])
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python3 -m pytest tests/test_engine_service.py::EngineServiceDelegationTests::test_runtime_index_methods_use_default_runtime_index -q
```

Expected: FAIL with `AttributeError: 'EngineService' object has no attribute 'refresh_runtime_index'`.

- [ ] **Step 3: Implement service methods**

Modify `academic_engine/engine_service.py`:

```python
from .runtime_index import RuntimeIndex
```

Add methods on `EngineService`:

```python
    def refresh_runtime_index(self) -> dict[str, Any]:
        return RuntimeIndex(self.root_dir).refresh()

    def get_runtime_index(self, *, work_id: str | None = None, limit: int = 20) -> dict[str, Any]:
        return RuntimeIndex(self.root_dir).get_index(work_id=work_id, limit=limit)
```

- [ ] **Step 4: Run service tests**

Run:

```bash
python3 -m pytest tests/test_engine_service.py::EngineServiceDelegationTests::test_runtime_index_methods_use_default_runtime_index -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/engine_service.py tests/test_engine_service.py
git commit -m "feat: expose runtime index service"
```

---

### Task 5: CLI Commands for Runtime Index

**Files:**
- Modify: `academic_engine/work_cli.py`
- Modify: `tests/test_work_cli_runtime.py`

- [ ] **Step 1: Write the failing CLI tests**

Append to `tests/test_work_cli_runtime.py`:

```python
    def test_runtime_index_refresh_and_status_cli_json(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["runtime-index", "refresh", "--json"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            refresh = json.loads(stdout.getvalue())
            self.assertEqual(refresh["kind"], "runtime-index-refresh")
            self.assertEqual(refresh["status"], "refreshed")

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(
                    ["runtime-index", "status", "--work", TEST_WORK_ID, "--limit", "3", "--json"],
                    root_dir=root,
                )

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            payload = json.loads(stdout.getvalue())
            self.assertEqual(payload["kind"], "runtime-index")
            self.assertEqual(payload["status"], "ready")
            self.assertEqual(payload["works"][0]["work_id"], TEST_WORK_ID)

    def test_runtime_index_status_cli_human_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            work_cli_module.main(["runtime-index", "refresh"], root_dir=root)

            stdout = StringIO()
            stderr = StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = work_cli_module.main(["runtime-index", "status"], root_dir=root)

            self.assertEqual(code, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Runtime index:", stdout.getvalue())
            self.assertIn("Works:", stdout.getvalue())
            self.assertIn("Recent runs:", stdout.getvalue())
            self.assertNotIn("{", stdout.getvalue())
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
python3 -m pytest tests/test_work_cli_runtime.py::WorkCliRuntimeTests::test_runtime_index_refresh_and_status_cli_json tests/test_work_cli_runtime.py::WorkCliRuntimeTests::test_runtime_index_status_cli_human_summary -q
```

Expected: FAIL with argparse `invalid choice: 'runtime-index'`.

- [ ] **Step 3: Implement CLI parser and handlers**

Modify `academic_engine/work_cli.py`.

Add parser after `work-status`:

```python
    runtime_index_parser = subparsers.add_parser("runtime-index")
    runtime_index_subparsers = runtime_index_parser.add_subparsers(dest="runtime_index_command", required=True)

    runtime_index_refresh = runtime_index_subparsers.add_parser("refresh")
    runtime_index_refresh.add_argument("--json", action="store_true", dest="as_json")

    runtime_index_status = runtime_index_subparsers.add_parser("status")
    runtime_index_status.add_argument("--work", dest="work_id")
    runtime_index_status.add_argument("--limit", type=int, default=20)
    runtime_index_status.add_argument("--json", action="store_true", dest="as_json")
```

Add dispatch:

```python
        if args.command == "runtime-index":
            return runtime_index_cli(root_path, args)
```

Add handler:

```python
def runtime_index_cli(root_dir: Path, args: Any) -> int:
    service = EngineService(root_dir)
    if args.runtime_index_command == "refresh":
        payload = service.refresh_runtime_index()
    elif args.runtime_index_command == "status":
        payload = service.get_runtime_index(work_id=args.work_id, limit=args.limit)
    else:
        return 1
    _print_runtime_index_payload(payload, as_json=args.as_json)
    return 0


def _print_runtime_index_payload(payload: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    kind = payload.get("kind")
    if kind == "runtime-index-refresh":
        print(f"Runtime index refreshed: {payload.get('status')}")
        print(f"Works: {payload.get('works_indexed') or 0}")
        print(f"Recent runs: {payload.get('runs_indexed') or 0}")
        print(f"Blockers: {payload.get('blockers_indexed') or 0}")
        print(f"Artifacts: {payload.get('artifacts_indexed') or 0}")
        print(f"Index path: {payload.get('index_path')}")
        return
    works = payload.get("works") if isinstance(payload.get("works"), list) else []
    recent_runs = payload.get("recent_runs") if isinstance(payload.get("recent_runs"), list) else []
    blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
    artifacts = payload.get("artifacts") if isinstance(payload.get("artifacts"), list) else []
    print(f"Runtime index: {payload.get('status')}")
    print(f"Refreshed at: {payload.get('refreshed_at') or 'n/a'}")
    print(f"Works: {len(works)}")
    print(f"Recent runs: {len(recent_runs)}")
    print(f"Blockers: {len(blockers)}")
    print(f"Artifacts: {len(artifacts)}")
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
python3 -m pytest tests/test_work_cli_runtime.py::WorkCliRuntimeTests::test_runtime_index_refresh_and_status_cli_json tests/test_work_cli_runtime.py::WorkCliRuntimeTests::test_runtime_index_status_cli_human_summary -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add academic_engine/work_cli.py tests/test_work_cli_runtime.py
git commit -m "feat: add runtime index cli"
```

---

### Task 6: Named Regression Fixture Scenarios

**Files:**
- Create: `tests/test_runtime_regression_fixtures.py`

- [ ] **Step 1: Add scenario-named tests**

Create `tests/test_runtime_regression_fixtures.py`:

```python
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from academic_engine.one_shot import OneShotConfig, run_one_shot
from academic_engine.orchestrator import WorkflowOrchestrator
from academic_engine.orchestrator_exports import ONE_SHOT_REPORT_VERSION
from academic_engine.export_explain import explain_export
from tests.test_academic_engine import (
    TEST_WORK_ID,
    build_fake_repo,
    write_raw_manifest,
)


class RuntimeRegressionFixtureTests(unittest.TestCase):
    def test_article_without_evidence_is_not_submission_ready(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            write_raw_manifest(root, "thesis-v1")
            write_raw_manifest(root, "ru-law-article-v1")
            orchestrator = WorkflowOrchestrator(root)
            run_dir = orchestrator.store.runs_dir / "article-no-evidence"
            run_dir.mkdir(parents=True, exist_ok=True)
            request = {
                "run_id": "article-no-evidence",
                "lane": "article",
                "action": "finalize",
                "work_id": TEST_WORK_ID,
                "work_title": "Demo work",
                "target": "works/demo-work/articles/final/demo.md",
                "started_at": "2026-07-03T10:00:00+00:00",
            }
            result = {
                "status": "success",
                "returncode": 0,
                "finished_at": "2026-07-03T10:05:00+00:00",
                "log_path": str(run_dir / "launcher.log"),
            }
            orchestrator.store.write_json(run_dir / "request.json", request)
            orchestrator.store.write_json(run_dir / "result.json", result)

            record = orchestrator._finalize_runtime_run(run_dir, request, result)
            runtime_record = orchestrator._latest_workflow_runtime_record("article", TEST_WORK_ID)

            self.assertEqual(record.status, "success")
            self.assertIsNotNone(runtime_record)
            assert runtime_record is not None
            self.assertEqual(runtime_record.readiness_status, "strong-draft-with-blockers")
            self.assertTrue(any(item["code"] == "evidence-coverage-gap" for item in runtime_record.blockers))

    def test_vkr_without_originality_corpus_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            manuscript = root / "manuscript.md"
            manuscript.write_text(
                dedent(
                    """\
                    # Глава 1

                    Достаточный текст выпускной квалификационной работы.

                    ## Список использованных источников

                    1. Биометрия в России / Иванов И. И. — Москва: Норма, 2024.
                    """
                ),
                encoding="utf-8",
            )

            report = run_one_shot(
                OneShotConfig(
                    manuscript_md=manuscript,
                    docx_path=None,
                    metadata_path=None,
                    frontmatter_destination=None,
                )
            )

            self.assertEqual(report.status, "blocked")
            originality = next(gate for gate in report.gates if gate.name == "originality")
            self.assertFalse(originality.passed)
            self.assertEqual(originality.blockers[0].code, "originality-corpus-required")

    def test_submission_ready_evaluator_cannot_override_failed_machine_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            workflow_dir = root / "output" / "runs" / "wf-thesis-ready"
            workflow_dir.mkdir(parents=True, exist_ok=True)
            (workflow_dir / "workflow.json").write_text(
                json.dumps(
                    {
                        "version": "workflow-run/v1",
                        "workflow_id": "wf-thesis-ready",
                        "run_id": "wf-thesis-ready",
                        "work_id": TEST_WORK_ID,
                        "lane": "thesis",
                        "action": "finalize",
                        "execution_status": "succeeded",
                        "readiness_status": "submission-ready",
                        "started_at": "2026-07-03T09:00:00+00:00",
                        "finished_at": "2026-07-03T09:30:00+00:00",
                        "gates": [],
                        "promotion": {"status": "promoted"},
                    }
                ),
                encoding="utf-8",
            )
            report_path = root / "works" / TEST_WORK_ID / "thesis" / "reviews" / "2026-07-03-one-shot-report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(
                    {
                        "version": ONE_SHOT_REPORT_VERSION,
                        "status": "blocked",
                        "finished_at": "2026-07-03T09:45:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            payload = explain_export(root, "thesis", work_id=TEST_WORK_ID)

            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["reasons"][0]["code"], "machine-gates-not-passed")

    def test_promotion_conflict_does_not_mutate_canon(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            root = Path(tempdir)
            build_fake_repo(root)
            canon_path = root / "works" / TEST_WORK_ID / "work-canon.md"
            before = canon_path.read_text(encoding="utf-8")
            workflow_dir = root / "output" / "runs" / "wf-promotion-conflict"
            workflow_dir.mkdir(parents=True, exist_ok=True)
            (workflow_dir / "workflow.json").write_text(
                json.dumps(
                    {
                        "version": "workflow-run/v1",
                        "workflow_id": "wf-promotion-conflict",
                        "run_id": "wf-promotion-conflict",
                        "work_id": TEST_WORK_ID,
                        "lane": "article",
                        "action": "finalize",
                        "execution_status": "succeeded",
                        "readiness_status": "submission-ready",
                        "started_at": "2026-07-03T10:00:00+00:00",
                        "finished_at": "2026-07-03T10:30:00+00:00",
                        "gates": [],
                        "promotion": {"status": "conflict"},
                    }
                ),
                encoding="utf-8",
            )

            payload = explain_export(root, "article:demo", work_id=TEST_WORK_ID)

            self.assertEqual(canon_path.read_text(encoding="utf-8"), before)
            self.assertEqual(payload["status"], "blocked")
            self.assertEqual(payload["reasons"][0]["code"], "promotion-not-safe")
```

Remove unused imports after running ruff. The imported helper list should stay limited to names used by this file.

- [ ] **Step 2: Run scenario fixture tests**

Run:

```bash
python3 -m pytest tests/test_runtime_regression_fixtures.py -q
```

Expected: PASS if all four invariants already hold. If any fixture fails, do not weaken the test; inspect the failing engine path and implement the smallest fail-closed fix in the module named by the failure.

- [ ] **Step 3: Commit**

```bash
git add tests/test_runtime_regression_fixtures.py
git commit -m "test: add runtime regression fixtures"
```

---

### Task 7: Document Runtime Index Cache Policy

**Files:**
- Modify: `output/README.md`

- [ ] **Step 1: Write the doc change**

Modify the `output/runtime/` bullet in `output/README.md` to read:

```markdown
- `output/runtime/` — локальное состояние активных запусков и автономного daemon, не коммитится; `runtime-index.sqlite` внутри этой директории является удаляемым cache/index для UI/API и не является источником истины;
```

- [ ] **Step 2: Verify wording**

Run:

```bash
rg -n "runtime-index.sqlite|cache/index|источник" output/README.md
```

Expected: output includes the updated `output/runtime/` policy line.

- [ ] **Step 3: Commit**

```bash
git add output/README.md
git commit -m "docs: document runtime index cache policy"
```

---

### Task 8: Focused and Full Verification

**Files:**
- No planned source edits after this task unless verification finds a defect.

- [ ] **Step 1: Run focused runtime index tests**

```bash
python3 -m pytest tests/test_runtime_index.py tests/test_engine_service.py::EngineServiceDelegationTests::test_runtime_index_methods_use_default_runtime_index tests/test_work_cli_runtime.py::WorkCliRuntimeTests::test_runtime_index_refresh_and_status_cli_json tests/test_work_cli_runtime.py::WorkCliRuntimeTests::test_runtime_index_status_cli_human_summary -q
```

Expected: PASS.

- [ ] **Step 2: Run regression fixture tests**

```bash
python3 -m pytest tests/test_runtime_regression_fixtures.py -q
```

Expected: PASS.

- [ ] **Step 3: Run existing related tests**

```bash
python3 -m pytest tests/test_work_cli_runtime.py tests/test_regression_harness.py tests/test_export_explain.py tests/test_one_shot.py -q
```

Expected: PASS.

- [ ] **Step 4: Run ruff**

```bash
python3 -m ruff check academic_engine tests
```

Expected: PASS with no lint errors.

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest -q
```

Expected: PASS.

- [ ] **Step 6: Final commit if verification required cleanup**

If verification required source cleanup, commit it:

```bash
git add academic_engine tests output/README.md
git commit -m "fix: clean up runtime index verification"
```

If no cleanup was required, do not create an empty commit.

---

## Plan Self-Review

- Spec coverage: runtime index storage, file-first authority, service/CLI facade, cache deletability, blockers/artifacts/timestamps, and four requested regression fixtures are covered by Tasks 1-8.
- Placeholder scan: no prohibited placeholder tokens or unspecified implementation placeholders remain in this plan.
- Type consistency: public payload names match the approved design: `runtime-index-refresh`, `runtime-index`, `works`, `recent_runs`, `blockers`, `artifacts`, `refreshed_at`, and count fields.
- Scope check: no HTTP, frontend, daemon auto-refresh, new dependency, or canonical database is introduced.
