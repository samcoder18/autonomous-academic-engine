# Engine Service Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an internal `EngineService` facade over existing engine operations and route `work init` plus `work-status` through it without changing CLI behavior.

**Architecture:** `EngineService` composes existing core modules and returns JSON-ready dictionaries. `work_bootstrap` remains the work-creation source of truth, `WorkflowOrchestrator` remains the workflow/status/export source of truth, and `autonomous_runner.stop_autonomous_run` remains the safe autonomous stop source of truth. CLI code parses arguments and formats payloads only.

**Tech Stack:** Python 3 standard library, `unittest`, existing `academic_engine` modules, existing CLI tests.

---

## File Structure

- Create: `academic_engine/engine_service.py`
  - Request dataclasses and `EngineService`.
  - No printing and no process exits.
  - Uses dependency injection via `orchestrator_factory` for service tests.
- Create: `tests/test_engine_service.py`
  - Service-level tests for create/status/start/export/stop behavior.
  - Uses fake orchestrators where delegation is the behavior under test.
- Modify: `academic_engine/work_cli.py`
  - Replace direct `bootstrap_work` and direct `WorkflowOrchestrator.get_work_state` calls with `EngineService`.
  - Preserve current `work init` and `work-status` output.
- Existing tests:
  - `tests/test_work_bootstrap.py`
  - `tests/test_work_cli_runtime.py`
  - `tests/test_work_cli_autonomous.py`
  - `tests/test_work_state.py`

---

### Task 1: Add RED Service Tests For Work Creation And Status

**Files:**
- Create: `tests/test_engine_service.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_engine_service.py` with this content:

```python
from __future__ import annotations

import tempfile
import tomllib
import unittest
from pathlib import Path

from academic_engine.engine_service import CreateWorkRequest, EngineService


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


def _prepare_workspace(tmp: Path) -> Path:
    (tmp / "workspace.toml").write_text(MINIMAL_WORKSPACE_TOML, encoding="utf-8")
    starter_dir = tmp / "works" / "starter-work"
    starter_dir.mkdir(parents=True, exist_ok=True)
    (starter_dir / "placeholder.txt").write_text("placeholder", encoding="utf-8")
    return tmp


class EngineServiceCreateWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = _prepare_workspace(Path(self._tempdir.name))

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_create_work_returns_cli_compatible_payload(self) -> None:
        payload = EngineService(self.root).create_work(
            CreateWorkRequest(
                slug="smart-contracts",
                title="Статья по смарт-контрактам",
                topic="Смарт-контракты",
                artifact_type="article",
            )
        )

        self.assertEqual(payload["kind"], "work-init")
        self.assertEqual(payload["version"], "v1")
        self.assertEqual(payload["slug"], "smart-contracts")
        self.assertEqual(payload["work_dir"], str(self.root / "works" / "smart-contracts"))
        self.assertEqual(payload["work_toml"], str(self.root / "works" / "smart-contracts" / "work.toml"))
        self.assertEqual(payload["work_canon"], str(self.root / "works" / "smart-contracts" / "work-canon.md"))
        self.assertEqual(payload["workspace_toml"], str(self.root / "workspace.toml"))
        self.assertFalse(payload["set_default"])
        self.assertEqual(payload["default_work"], "starter-work")
        self.assertIn(str(self.root / "works" / "smart-contracts" / "articles" / "briefs"), payload["created_dirs"])

        parsed = tomllib.loads((self.root / "workspace.toml").read_text(encoding="utf-8"))
        self.assertIn("smart-contracts", parsed["works"])

    def test_create_work_defaults_empty_topic_to_title(self) -> None:
        payload = EngineService(self.root).create_work(
            CreateWorkRequest(
                slug="topic-default",
                title="Fallback title",
                topic="",
                artifact_type="article",
            )
        )

        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["topic"], "Fallback title")


class EngineServiceStatusTests(unittest.TestCase):
    def test_get_work_status_delegates_to_orchestrator(self) -> None:
        instances: list[FakeOrchestrator] = []

        def factory(root_dir: Path) -> FakeOrchestrator:
            fake = FakeOrchestrator(root_dir)
            instances.append(fake)
            return fake

        service = EngineService("/tmp/example-root", orchestrator_factory=factory)
        payload = service.get_work_status(work_id="demo-work")

        self.assertEqual(payload["kind"], "work-state")
        self.assertEqual(payload["work_id"], "demo-work")
        self.assertEqual(instances[0].status_work_ids, ["demo-work"])


class FakeOrchestrator:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.status_work_ids: list[str | None] = []

    def get_work_state(self, *, work_id: str | None = None) -> dict[str, object]:
        self.status_work_ids.append(work_id)
        return {
            "kind": "work-state",
            "work_id": work_id or "default-work",
            "work_title": "Demo work",
        }


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_engine_service -q
```

Expected: FAIL with `ModuleNotFoundError: No module named 'academic_engine.engine_service'`.

- [ ] **Step 3: Commit RED tests**

Run:

```bash
git add tests/test_engine_service.py
git commit -m "test: cover engine service work operations"
```

---

### Task 2: Implement `EngineService` For Create Work And Status

**Files:**
- Create: `academic_engine/engine_service.py`
- Test: `tests/test_engine_service.py`

- [ ] **Step 1: Add minimal implementation**

Create `academic_engine/engine_service.py` with this content:

```python
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .orchestrator import WorkflowOrchestrator
from .work_bootstrap import WorkBootstrapRequest, bootstrap_work


@dataclass(frozen=True)
class CreateWorkRequest:
    slug: str
    title: str
    artifact_type: str
    topic: str | None = None
    language: str = "ru"
    lanes: tuple[str, ...] | None = None
    thesis_profile: str | None = None
    article_profile: str | None = None
    set_default: bool = False


class EngineService:
    """Stable internal service facade over the academic engine core."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        orchestrator_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()
        self._orchestrator_factory = orchestrator_factory or WorkflowOrchestrator

    def create_work(self, request: CreateWorkRequest) -> dict[str, Any]:
        topic = request.topic.strip() if request.topic and request.topic.strip() else request.title
        result = bootstrap_work(
            self.root_dir,
            WorkBootstrapRequest(
                slug=request.slug,
                title=request.title,
                topic=topic,
                artifact_type=request.artifact_type,
                language=request.language,
                lanes=request.lanes,
                thesis_profile=request.thesis_profile,
                article_profile=request.article_profile,
                set_default=request.set_default,
            ),
        )
        return {
            "kind": "work-init",
            "version": "v1",
            "slug": result.slug,
            "work_dir": str(result.work_dir),
            "work_toml": str(result.work_toml),
            "work_canon": str(result.work_canon),
            "workspace_toml": str(result.workspace_toml),
            "set_default": result.set_default,
            "default_work": result.default_work_after,
            "created_dirs": [str(directory) for directory in result.created_dirs],
        }

    def get_work_status(self, work_id: str | None = None) -> dict[str, Any]:
        return self._orchestrator().get_work_state(work_id=work_id)

    def _orchestrator(self) -> Any:
        return self._orchestrator_factory(self.root_dir)
```

- [ ] **Step 2: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_engine_service -q
```

Expected: PASS.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git add academic_engine/engine_service.py tests/test_engine_service.py
git commit -m "feat: add engine service facade"
```

---

### Task 3: Add Service Tests For Workflow Start, Export, And Stop

**Files:**
- Modify: `tests/test_engine_service.py`

- [ ] **Step 1: Extend failing tests**

In `tests/test_engine_service.py`, replace `EngineServiceStatusTests` and `FakeOrchestrator` with this expanded block:

```python
class EngineServiceDelegationTests(unittest.TestCase):
    def test_get_work_status_delegates_to_orchestrator(self) -> None:
        instances: list[FakeOrchestrator] = []

        def factory(root_dir: Path) -> FakeOrchestrator:
            fake = FakeOrchestrator(root_dir)
            instances.append(fake)
            return fake

        service = EngineService("/tmp/example-root", orchestrator_factory=factory)
        payload = service.get_work_status(work_id="demo-work")

        self.assertEqual(payload["kind"], "work-state")
        self.assertEqual(payload["work_id"], "demo-work")
        self.assertEqual(instances[0].status_work_ids, ["demo-work"])

    def test_start_workflow_delegates_to_orchestrator(self) -> None:
        instances: list[FakeOrchestrator] = []

        def factory(root_dir: Path) -> FakeOrchestrator:
            fake = FakeOrchestrator(root_dir)
            instances.append(fake)
            return fake

        from academic_engine.engine_service import StartWorkflowRequest

        payload = EngineService("/tmp/example-root", orchestrator_factory=factory).start_workflow(
            StartWorkflowRequest(
                lane="article",
                action="review",
                target_or_topic="works/demo-work/articles/drafts/demo.md",
                notes="check attribution",
                search_override=False,
                model_override="test-model",
                profile_override="ru-law-article-v1",
                work_id="demo-work",
            )
        )

        self.assertEqual(payload["status"], "queued")
        self.assertEqual(payload["workflow_id"], "wf-demo")
        self.assertEqual(
            instances[0].start_calls,
            [
                {
                    "lane": "article",
                    "action": "review",
                    "target_or_topic": "works/demo-work/articles/drafts/demo.md",
                    "notes": "check attribution",
                    "search_override": False,
                    "model_override": "test-model",
                    "profile_override": "ru-law-article-v1",
                    "work_id": "demo-work",
                }
            ],
        )

    def test_export_docx_delegates_to_orchestrator(self) -> None:
        instances: list[FakeOrchestrator] = []

        def factory(root_dir: Path) -> FakeOrchestrator:
            fake = FakeOrchestrator(root_dir)
            instances.append(fake)
            return fake

        from academic_engine.engine_service import ExportRequest

        payload = EngineService("/tmp/example-root", orchestrator_factory=factory).export_docx(
            ExportRequest(subject="thesis", work_id="demo-work")
        )

        self.assertEqual(payload["subject"], "thesis")
        self.assertEqual(payload["work_id"], "demo-work")
        self.assertEqual(instances[0].export_calls, [{"subject": "thesis", "work_id": "demo-work"}])


class EngineServiceStopJobTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = _prepare_workspace(Path(self._tempdir.name))
        EngineService(self.root).create_work(
            CreateWorkRequest(
                slug="stop-demo",
                title="Stop demo",
                artifact_type="article",
            )
        )

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_stop_job_resolves_work_and_writes_autonomous_stop_state(self) -> None:
        from academic_engine.engine_service import StopJobRequest

        payload = EngineService(self.root).stop_job(
            StopJobRequest(work_id="stop-demo", reason="operator-stop")
        )

        self.assertEqual(payload["kind"], "autonomous-run-state")
        self.assertEqual(payload["status"], "stopped")
        self.assertEqual(payload["work_id"], "stop-demo")
        self.assertEqual(payload["stop_reason"], "operator-stop")


class FakeOrchestrator:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir
        self.status_work_ids: list[str | None] = []
        self.start_calls: list[dict[str, object]] = []
        self.export_calls: list[dict[str, object]] = []

    def get_work_state(self, *, work_id: str | None = None) -> dict[str, object]:
        self.status_work_ids.append(work_id)
        return {
            "kind": "work-state",
            "work_id": work_id or "default-work",
            "work_title": "Demo work",
        }

    def start_run(
        self,
        lane: str,
        action: str,
        target_or_topic: str,
        *,
        notes: str | None = None,
        search_override: bool | None = None,
        model_override: str | None = None,
        profile_override: str | None = None,
        work_id: str | None = None,
    ) -> dict[str, object]:
        self.start_calls.append(
            {
                "lane": lane,
                "action": action,
                "target_or_topic": target_or_topic,
                "notes": notes,
                "search_override": search_override,
                "model_override": model_override,
                "profile_override": profile_override,
                "work_id": work_id,
            }
        )
        return {
            "run_id": "demo-run",
            "workflow_id": "wf-demo",
            "status": "queued",
            "work_id": work_id,
            "lane": lane,
            "action": action,
        }

    def export_docx(self, subject: str, *, work_id: str | None = None) -> dict[str, object]:
        self.export_calls.append({"subject": subject, "work_id": work_id})
        return {
            "subject": subject,
            "work_id": work_id,
            "path": "/tmp/example.docx",
            "stdout": "Exported /tmp/example.docx",
        }
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python3 -m unittest tests.test_engine_service -q
```

Expected: FAIL with an import error for `StartWorkflowRequest`, `ExportRequest`, or `StopJobRequest`.

- [ ] **Step 3: Fix implementation if needed**

Update `academic_engine/engine_service.py` by adding imports, request dataclasses, and methods below.

Add these imports:

```python
from .autonomous_runner import stop_autonomous_run
from .workspace import load_workspace_config, resolve_work_config
```

Add these dataclasses after `CreateWorkRequest`:

```python
@dataclass(frozen=True)
class StartWorkflowRequest:
    lane: str
    action: str
    target_or_topic: str
    notes: str | None = None
    search_override: bool | None = None
    model_override: str | None = None
    profile_override: str | None = None
    work_id: str | None = None


@dataclass(frozen=True)
class ExportRequest:
    subject: str
    work_id: str | None = None


@dataclass(frozen=True)
class StopJobRequest:
    work_id: str | None = None
    reason: str = "operator-stop"
```

Add these methods after `get_work_status`:

```python
    def start_workflow(self, request: StartWorkflowRequest) -> dict[str, Any]:
        return self._orchestrator().start_run(
            request.lane,
            request.action,
            request.target_or_topic,
            notes=request.notes,
            search_override=request.search_override,
            model_override=request.model_override,
            profile_override=request.profile_override,
            work_id=request.work_id,
        )

    def export_docx(self, request: ExportRequest) -> dict[str, Any]:
        return self._orchestrator().export_docx(request.subject, work_id=request.work_id)

    def stop_job(self, request: StopJobRequest) -> dict[str, Any]:
        workspace = load_workspace_config(self.root_dir)
        work = resolve_work_config(workspace, work_id=request.work_id)
        return stop_autonomous_run(self.root_dir, work.slug, reason=request.reason)
```

- [ ] **Step 4: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest tests.test_engine_service -q
```

Expected: PASS.

- [ ] **Step 5: Commit service operation coverage**

Run:

```bash
git add academic_engine/engine_service.py tests/test_engine_service.py
git commit -m "test: cover engine service workflow operations"
```

---

### Task 4: Route `work init` Through `EngineService`

**Files:**
- Modify: `academic_engine/work_cli.py`
- Test: `tests/test_work_bootstrap.py`

- [ ] **Step 1: Run existing CLI tests before editing**

Run:

```bash
python3 -m unittest tests.test_work_bootstrap -q
```

Expected: PASS before the refactor.

- [ ] **Step 2: Update imports in `academic_engine/work_cli.py`**

Replace:

```python
from .orchestrator import WorkflowOrchestrator
from .work_bootstrap import (
    ALL_ARTIFACT_TYPES,
    WorkBootstrapError,
    WorkBootstrapRequest,
    bootstrap_work,
)
```

With:

```python
from .engine_service import CreateWorkRequest, EngineService
from .work_bootstrap import ALL_ARTIFACT_TYPES, WorkBootstrapError
```

- [ ] **Step 3: Replace `work_init` implementation**

Replace the body of `work_init` with:

```python
def work_init(root_dir: Path, args: Any) -> int:
    lanes: tuple[str, ...] | None = None
    if args.lanes:
        lanes_raw = [lane.strip() for lane in str(args.lanes).split(",") if lane.strip()]
        if not lanes_raw:
            print("--lanes must not be empty when provided", file=sys.stderr)
            return 2
        lanes = tuple(lanes_raw)

    topic = args.topic.strip() if args.topic else args.title
    request = CreateWorkRequest(
        slug=args.slug,
        title=args.title,
        topic=topic,
        artifact_type=args.artifact_type,
        language=args.language,
        lanes=lanes,
        thesis_profile=args.thesis_profile,
        article_profile=args.article_profile,
        set_default=bool(args.set_default),
    )

    try:
        payload = EngineService(root_dir).create_work(request)
    except WorkBootstrapError as exc:
        print(f"work init failed: {exc}", file=sys.stderr)
        return 2

    if getattr(args, "as_json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        work_dir = Path(str(payload["work_dir"]))
        rel = work_dir.relative_to(root_dir) if work_dir.is_absolute() else work_dir
        print(f"Created work `{payload['slug']}` at {rel}")
        print(f"  work.toml: {Path(str(payload['work_toml'])).relative_to(root_dir)}")
        print(f"  work-canon.md: {Path(str(payload['work_canon'])).relative_to(root_dir)}")
        print(f"  registered in: {Path(str(payload['workspace_toml'])).relative_to(root_dir)}")
        if payload["set_default"]:
            print(f"  default_work switched to `{payload['default_work']}`")
        else:
            print(f"  default_work remains `{payload['default_work']}`")
        print("Next step: заполнить work-canon.md и положить источники / бриф в соответствующую lane.")
    return 0
```

- [ ] **Step 4: Run CLI tests**

Run:

```bash
python3 -m unittest tests.test_work_bootstrap -q
```

Expected: PASS.

- [ ] **Step 5: Commit CLI work init routing**

Run:

```bash
git add academic_engine/work_cli.py
git commit -m "refactor: route work init through engine service"
```

---

### Task 5: Route `work-status` Through `EngineService`

**Files:**
- Modify: `academic_engine/work_cli.py`
- Test: `tests/test_work_cli_runtime.py`

- [ ] **Step 1: Change `work_status` implementation**

Replace:

```python
def work_status(root_dir: Path, work_id: str | None, *, as_json: bool = False) -> int:
    state = WorkflowOrchestrator(root_dir).get_work_state(work_id=work_id)
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(format_work_state_summary(state))
    return 0
```

With:

```python
def work_status(root_dir: Path, work_id: str | None, *, as_json: bool = False) -> int:
    state = EngineService(root_dir).get_work_status(work_id=work_id)
    if as_json:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(format_work_state_summary(state))
    return 0
```

- [ ] **Step 2: Run status CLI tests**

Run:

```bash
python3 -m unittest tests.test_work_cli_runtime -q
```

Expected: PASS.

- [ ] **Step 3: Run service tests again**

Run:

```bash
python3 -m unittest tests.test_engine_service -q
```

Expected: PASS.

- [ ] **Step 4: Commit CLI status routing**

Run:

```bash
git add academic_engine/work_cli.py
git commit -m "refactor: route work status through engine service"
```

---

### Task 6: Verify Targeted Regression Pack

**Files:**
- No expected edits.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
python3 -m unittest tests.test_engine_service tests.test_work_bootstrap tests.test_work_cli_runtime tests.test_work_cli_autonomous tests.test_work_state -q
```

Expected: PASS.

- [ ] **Step 2: Run full unit suite**

Run:

```bash
python3 -m unittest discover -s tests -q
```

Expected: PASS.

- [ ] **Step 3: Run formatting/lint checks if tools are available**

Run:

```bash
ruff check academic_engine/ tests/
```

Expected: PASS.

Run:

```bash
ruff format --check academic_engine/ tests/
```

Expected: PASS.

If `ruff` is not installed in this environment, record that in the final verification summary and rely on unit tests.

- [ ] **Step 4: Inspect final diff**

Run:

```bash
git status --short
git diff --stat HEAD
```

Expected: only service-layer implementation changes are present.
