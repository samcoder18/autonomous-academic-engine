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
        self.assertIn(str(self.root / "works" / "smart-contracts" / "articles" / "drafts"), payload["created_dirs"])
        self.assertIn(str(self.root / "works" / "smart-contracts" / "articles" / "reviews"), payload["created_dirs"])
        self.assertIn(str(self.root / "works" / "smart-contracts" / "articles" / "final"), payload["created_dirs"])

        work_toml = tomllib.loads(Path(payload["work_toml"]).read_text(encoding="utf-8"))
        self.assertEqual(work_toml["slug"], "smart-contracts")
        self.assertEqual(work_toml["title"], "Статья по смарт-контрактам")
        self.assertEqual(work_toml["topic"], "Смарт-контракты")
        self.assertEqual(work_toml["artifact_type"], "article")

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
        self.assertEqual(instances[0].root_dir, Path("/tmp/example-root").resolve())
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
