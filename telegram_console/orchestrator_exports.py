"""DOCX export helpers for WorkflowOrchestrator (mixin)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from .orchestrator_support import WorkflowError


class OrchestratorExportMixin:
    """Invokes `scripts/export_*.sh` for thesis and article DOCX."""

    root_dir: Path

    def export_docx(self, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
        work = self._work(work_id)
        if subject == "thesis":
            if not work.thesis:
                raise WorkflowError(f"Work `{work.slug}` не поддерживает thesis lane.")
            cmd = ["bash", "scripts/export_docx.sh", "--work", work.slug]
            expected = work.thesis.export_docx_path
        elif subject.startswith("article:"):
            slug = subject.split(":", 1)[1]
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
            "path": str(output_path.resolve()),
            "stdout": completed.stdout.strip(),
        }
