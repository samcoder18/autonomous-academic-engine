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
        root = self.root.resolve()
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
        self.assertEqual(payload["work_dir"], str(root / "works" / "smart-contracts"))
        self.assertEqual(payload["work_toml"], str(root / "works" / "smart-contracts" / "work.toml"))
        self.assertEqual(payload["work_canon"], str(root / "works" / "smart-contracts" / "work-canon.md"))
        self.assertEqual(payload["workspace_toml"], str(root / "workspace.toml"))
        self.assertFalse(payload["set_default"])
        self.assertEqual(payload["default_work"], "starter-work")
        self.assertIn(str(root / "works" / "smart-contracts" / "articles" / "briefs"), payload["created_dirs"])
        self.assertIn(str(root / "works" / "smart-contracts" / "articles" / "drafts"), payload["created_dirs"])
        self.assertIn(str(root / "works" / "smart-contracts" / "articles" / "reviews"), payload["created_dirs"])
        self.assertIn(str(root / "works" / "smart-contracts" / "articles" / "final"), payload["created_dirs"])

        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["slug"], "smart-contracts")
        self.assertEqual(work_toml["title"], "Статья по смарт-контрактам")
        self.assertEqual(work_toml["topic"], "Смарт-контракты")
        self.assertEqual(work_toml["artifact_type"], "article")

        parsed = tomllib.loads((root / "workspace.toml").read_text(encoding="utf-8"))
        self.assertIn("smart-contracts", parsed["works"])

    def test_create_work_defaults_missing_topic_to_title(self) -> None:
        payload = EngineService(self.root).create_work(
            CreateWorkRequest(
                slug="topic-default",
                title="Fallback title",
                artifact_type="article",
            )
        )

        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["topic"], "Fallback title")

    def test_create_work_trims_whitespace_topic_to_empty(self) -> None:
        payload = EngineService(self.root).create_work(
            CreateWorkRequest(
                slug="topic-whitespace",
                title="Fallback title",
                topic="   ",
                artifact_type="article",
            )
        )

        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["topic"], "")

    def test_create_work_preserves_explicit_empty_topic(self) -> None:
        payload = EngineService(self.root).create_work(
            CreateWorkRequest(
                slug="topic-empty",
                title="Fallback title",
                topic="",
                artifact_type="article",
            )
        )

        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["topic"], "")


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
        self.assertEqual(instances[0].root_dir, Path("/tmp/example-root").resolve())
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

        payload = EngineService(self.root).stop_job(StopJobRequest(work_id="stop-demo", reason="operator-stop"))

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


if __name__ == "__main__":
    unittest.main()
