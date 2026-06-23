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
            "work_dir": self._display_path(result.work_dir),
            "work_toml": self._display_path(result.work_toml),
            "work_canon": self._display_path(result.work_canon),
            "workspace_toml": self._display_path(result.workspace_toml),
            "set_default": result.set_default,
            "default_work": result.default_work_after,
            "created_dirs": [self._display_path(directory) for directory in result.created_dirs],
        }

    def get_work_status(self, work_id: str | None = None) -> dict[str, Any]:
        return self._orchestrator().get_work_state(work_id=work_id)

    def _orchestrator(self) -> Any:
        return self._orchestrator_factory(self.root_dir)

    @staticmethod
    def _display_path(path: Path) -> str:
        text = str(path)
        if text.startswith("/private/"):
            return text.removeprefix("/private")
        return text
