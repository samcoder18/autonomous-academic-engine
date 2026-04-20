"""Workspace/work-state helpers for WorkflowOrchestrator (mixin)."""

from __future__ import annotations

from typing import Any

from .dissertation_contour import inspect_dissertation_contour
from .orchestrator_support import WorkflowError
from .quality_advisories import build_quality_advisories
from .standards import resolve_standard_profile
from .work_state import build_work_state
from .workspace import (
    WorkConfig,
    WorkspaceConfigError,
    load_workspace_config,
    resolve_work_config,
)


class OrchestratorWorkspaceMixin:
    """Workspace resolution plus work/article/thesis overview assembly."""

    root_dir: Any
    _workspace: Any

    def get_artifact_status(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        if subject == "work":
            return self.get_work_state(work_id=work.slug)

        if subject == "thesis":
            sections = [
                self._thesis_section_status(path, work.slug) for path in self.list_thesis_sections(work_id=work.slug)
            ]
            return {
                "kind": "thesis-overview",
                "work_id": work.slug,
                "sections": sections,
                "dissertation": inspect_dissertation_contour(work),
                "summary": self._build_thesis_overview_summary(sections),
            }

        if subject.startswith("thesis:"):
            return self._thesis_section_status(subject.split(":", 1)[1], work.slug)

        if subject == "article":
            bundles = [
                self._article_bundle_status(slug, work.slug) for slug in self.list_article_slugs(work_id=work.slug)
            ]
            return {
                "kind": "article-overview",
                "work_id": work.slug,
                "bundles": bundles,
                "summary": self._build_article_overview_summary(bundles),
            }

        if subject.startswith("article:"):
            return self._article_bundle_status(subject.split(":", 1)[1], work.slug)

        raise WorkflowError(f"Не смогла определить, какой артефакт ты хочешь открыть: {subject}")

    def get_work_state(self, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        thesis_overview: dict[str, Any] | None = None
        thesis_ledger_advisory: dict[str, Any] | None = None
        article_overview: dict[str, Any] | None = None

        if work.supports("thesis") and work.thesis:
            sections = [
                self._thesis_section_status(path, work.slug) for path in self.list_thesis_sections(work_id=work.slug)
            ]
            thesis_overview = {
                "kind": "thesis-overview",
                "work_id": work.slug,
                "sections": sections,
                "dissertation": inspect_dissertation_contour(work),
                "summary": self._build_thesis_overview_summary(sections),
            }
            from .thesis_evidence_ledger import audit_thesis_ledgers

            thesis_ledger_advisory = audit_thesis_ledgers(work)

        if work.supports("article") and work.article:
            bundles = [
                self._article_bundle_status(slug, work.slug) for slug in self.list_article_slugs(work_id=work.slug)
            ]
            article_overview = {
                "kind": "article-overview",
                "work_id": work.slug,
                "bundles": bundles,
                "summary": self._build_article_overview_summary(bundles),
            }

        return build_work_state(
            root_dir=self.root_dir,
            work_id=work.slug,
            work_title=work.title,
            active_lanes=work.active_lanes,
            thesis_overview=thesis_overview,
            thesis_ledger_advisory=thesis_ledger_advisory,
            article_overview=article_overview,
            quality_advisories=build_quality_advisories(work),
            standards_profiles=self._resolve_work_standards_profiles(work),
            runtime_records=self._recent_workflow_runtime_records(work.slug, limit=5),
            active_run=self._active_workflow_run_for_work(work.slug),
        )

    def _resolve_work_standards_profiles(self, work: WorkConfig) -> dict[str, Any]:
        profiles: dict[str, Any] = {}
        for lane in work.active_lanes:
            if lane not in ("thesis", "article"):
                continue
            try:
                profiles[lane] = resolve_standard_profile(
                    self.root_dir,
                    self._workspace_config(),
                    work,
                    lane=lane,
                    requested_profile_id=None,
                )
            except WorkspaceConfigError as exc:
                profiles[lane] = {"lane": lane, "error": str(exc)}
        return profiles

    def _workspace_config(self):
        if self._workspace is not None and self._workspace.root_dir == self.root_dir:
            return self._workspace
        try:
            self._workspace = load_workspace_config(self.root_dir)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
        return self._workspace

    def _work(self, work_id: str | None = None, target: str | None = None) -> WorkConfig:
        workspace = self._workspace_config()
        try:
            return resolve_work_config(workspace, work_id=work_id, target=target)
        except WorkspaceConfigError as exc:
            raise WorkflowError(str(exc)) from exc
