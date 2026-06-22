from __future__ import annotations

import sys
from pathlib import Path

from .orchestrator_article import OrchestratorArticleMixin
from .orchestrator_exports import OrchestratorExportMixin
from .orchestrator_launch import OrchestratorLaunchMixin
from .orchestrator_runtime import OrchestratorRuntimeMixin
from .orchestrator_status import OrchestratorStatusMixin
from .orchestrator_support import RunBusyError, RunRecord, WorkflowError, action_title, lane_title, slugify
from .orchestrator_thesis import OrchestratorThesisMixin
from .orchestrator_workspace import OrchestratorWorkspaceMixin
from .state import RuntimeStore


class WorkflowOrchestrator(
    OrchestratorWorkspaceMixin,
    OrchestratorLaunchMixin,
    OrchestratorStatusMixin,
    OrchestratorArticleMixin,
    OrchestratorThesisMixin,
    OrchestratorRuntimeMixin,
    OrchestratorExportMixin,
):
    """Thin shell that composes the orchestration mixins."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        codex_bin: str | None = None,
        codex_model: str | None = None,
        python_executable: str | None = None,
        store: RuntimeStore | None = None,
        project_id: str | None = None,
        project_title: str | None = None,
    ):
        self.root_dir = Path(root_dir).resolve()
        self.package_root = Path(__file__).resolve().parents[1]
        self.store = store or RuntimeStore(self.root_dir)
        self.codex_bin = codex_bin
        self.codex_model = codex_model
        self.python_executable = python_executable or sys.executable
        self.project_id = (project_id or "default").strip() or "default"
        self.project_title = (project_title or self.root_dir.name or self.project_id).strip()
        self._workspace = None


__all__ = [
    "RunRecord",
    "RunBusyError",
    "WorkflowError",
    "WorkflowOrchestrator",
    "action_title",
    "lane_title",
    "slugify",
]
