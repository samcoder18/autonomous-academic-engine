"""DOCX export helpers for WorkflowOrchestrator (mixin)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from .one_shot import ONE_SHOT_REPORT_VERSION
from .orchestrator_support import WorkflowError


def require_submission_ready_workflow(root_dir: Path, work_id: str, lane: str) -> None:
    candidates: list[dict[str, Any]] = []
    workflow_root = root_dir / "output" / "runs"
    if workflow_root.exists():
        for path in workflow_root.glob("*/workflow.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if payload.get("version") != "workflow-run/v1":
                continue
            if payload.get("work_id") != work_id or payload.get("lane") != lane:
                continue
            candidates.append(payload)
    candidates.sort(key=lambda item: str(item.get("finished_at") or item.get("started_at") or ""), reverse=True)
    latest = candidates[0] if candidates else None
    if not latest or latest.get("execution_status") != "succeeded":
        raise WorkflowError(f"DOCX export blocked: no successful workflow v1 for `{work_id}`/{lane}.")
    if latest.get("readiness_status") != "submission-ready":
        raise WorkflowError(
            f"DOCX export blocked: latest workflow readiness is `{latest.get('readiness_status') or 'not-evaluated'}`."
        )
    gates = latest.get("gates")
    if not isinstance(gates, list) or any(
        isinstance(gate, dict) and gate.get("blocking") and gate.get("status") != "pass" for gate in gates
    ):
        raise WorkflowError("DOCX export blocked: latest workflow contains failed mandatory gates.")
    promotion = latest.get("promotion")
    if isinstance(promotion, dict) and promotion.get("status") in {"blocked", "conflict"}:
        raise WorkflowError("DOCX export blocked: latest workflow promotion did not complete safely.")


def require_machine_gates_passed(reviews_dir: Path) -> None:
    reports: list[tuple[str, dict[str, Any]]] = []
    if reviews_dir.exists():
        for path in reviews_dir.glob("*one-shot-report.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            reports.append((str(payload.get("finished_at") or path.stat().st_mtime), payload))
    reports.sort(key=lambda item: item[0], reverse=True)
    latest = reports[0][1] if reports else None
    if not latest or latest.get("version") != ONE_SHOT_REPORT_VERSION or latest.get("status") != "machine-gates-passed":
        raise WorkflowError("DOCX export blocked: thesis one-shot machine gates have not passed.")


class OrchestratorExportMixin:
    """Invokes `scripts/export_*.sh` for thesis and article DOCX."""

    root_dir: Path

    def export_docx(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        if subject == "thesis":
            if not work.thesis:
                raise WorkflowError(f"Work `{work.slug}` не поддерживает thesis lane.")
            self._require_submission_ready_workflow(work.slug, "thesis")
            self._require_machine_gates_passed(work.thesis.reviews_dir)
            cmd = ["bash", "scripts/export_docx.sh", "--work", work.slug]
            expected = work.thesis.export_docx_path
        elif subject.startswith("article:"):
            slug = subject.split(":", 1)[1]
            self._require_submission_ready_workflow(work.slug, "article")
            status = self._article_bundle_status(slug, work.slug)
            final_markdown = status["files"]["final"]["path"]
            if not Path(final_markdown).exists():
                raise WorkflowError(f"У статьи `{slug}` пока нет финального Markdown-файла для экспорта.")
            cmd = [
                "bash",
                "scripts/export_academic_docx.sh",
                self._relative_to_root(Path(final_markdown)),
                "--work",
                work.slug,
            ]
            expected = Path(status["files"]["docx"]["path"])
        else:
            raise WorkflowError(f"Не понимаю, что именно нужно экспортировать: {subject}")

        completed = subprocess.run(
            cmd,
            cwd=self.root_dir,
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise WorkflowError(completed.stderr.strip() or completed.stdout.strip() or "Экспорт не получился.")

        output_path = expected
        for line in (completed.stdout or "").splitlines():
            if line.startswith("Exported "):
                output_path = Path(line[len("Exported ") :].strip())
                break

        return {
            "subject": subject,
            "work_id": work.slug,
            "path": str(output_path.resolve()),
            "stdout": completed.stdout.strip(),
        }

    def _require_submission_ready_workflow(self, work_id: str, lane: str) -> None:
        require_submission_ready_workflow(self.root_dir, work_id, lane)

    def _require_machine_gates_passed(self, reviews_dir: Path) -> None:
        require_machine_gates_passed(reviews_dir)
