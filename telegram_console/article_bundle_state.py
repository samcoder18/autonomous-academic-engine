from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import json
import tempfile

from .workspace import WorkConfig, WorkspaceConfigError


ARTICLE_BUNDLE_STATE_VERSION = "v1"
ARTICLE_READINESS_STATUSES = ("submission-ready", "strong-draft", "strong-draft-with-blockers")


@dataclass(frozen=True)
class ArticleBundleState:
    work_id: str
    article_slug: str
    current_phase: str
    current_status: str
    readiness_status: str | None
    active_phase: str | None
    profile_id: str | None
    evidence_state: str
    checklist_state: str
    finalizer_gate_state: str
    last_action: str | None
    last_run_status: str | None
    latest_run_manifest: str | None
    latest_output_file: str | None
    latest_runtime_record_ids: tuple[str, ...]
    bundle_files: dict[str, dict[str, Any]]
    execution_contract: dict[str, Any] | None
    inputs: dict[str, Any]
    updated_at: str
    version: str = ARTICLE_BUNDLE_STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "work_id": self.work_id,
            "article_slug": self.article_slug,
            "current_phase": self.current_phase,
            "current_status": self.current_status,
            "readiness_status": self.readiness_status,
            "active_phase": self.active_phase,
            "profile_id": self.profile_id,
            "evidence_state": self.evidence_state,
            "checklist_state": self.checklist_state,
            "finalizer_gate_state": self.finalizer_gate_state,
            "last_action": self.last_action,
            "last_run_status": self.last_run_status,
            "latest_run_manifest": self.latest_run_manifest,
            "latest_output_file": self.latest_output_file,
            "latest_runtime_record_ids": list(self.latest_runtime_record_ids),
            "bundle_files": self.bundle_files,
            "execution_contract": self.execution_contract,
            "inputs": self.inputs,
            "updated_at": self.updated_at,
        }


def article_bundle_manifest_path(work: WorkConfig, article_slug: str) -> Path:
    if not work.article:
        raise WorkspaceConfigError(f"Work `{work.slug}` не поддерживает article lane.")
    clean_slug = article_slug.strip()
    if not clean_slug:
        raise WorkspaceConfigError("Slug article bundle не может быть пустым.")
    return work.article.paths.root_dir / "runs" / f"{clean_slug}.bundle.json"


def build_article_bundle_state(
    *,
    work_id: str,
    article_slug: str,
    bundle: dict[str, Path],
    profile_id: str | None = None,
    last_action: str | None = None,
    last_run_status: str | None = None,
    latest_run_manifest: str | None = None,
    latest_output_file: str | None = None,
    latest_runtime_record_ids: Iterable[str] = (),
    execution_contract: dict[str, Any] | None = None,
    topic: str | None = None,
    input_brief: str | None = None,
    target_path: str | None = None,
    previous_state: ArticleBundleState | None = None,
) -> ArticleBundleState:
    bundle_files = snapshot_bundle_files(bundle)
    current_phase = infer_article_phase(bundle_files)
    readiness_status = infer_readiness_status(bundle_files, previous_state.readiness_status if previous_state else None)
    active_phase = _active_phase_for_action(last_action)
    current_status = _current_status(current_phase, readiness_status, last_run_status)
    runtime_ids = tuple(str(item).strip() for item in latest_runtime_record_ids if str(item).strip())
    if not runtime_ids and previous_state is not None:
        runtime_ids = previous_state.latest_runtime_record_ids
    if latest_run_manifest is None and previous_state is not None:
        latest_run_manifest = previous_state.latest_run_manifest
    if latest_output_file is None and previous_state is not None:
        latest_output_file = previous_state.latest_output_file
    if profile_id is None and previous_state is not None:
        profile_id = previous_state.profile_id
    if execution_contract is None and previous_state is not None:
        execution_contract = previous_state.execution_contract
    return ArticleBundleState(
        work_id=work_id,
        article_slug=article_slug,
        current_phase=current_phase,
        current_status=current_status,
        readiness_status=readiness_status,
        active_phase=active_phase,
        profile_id=profile_id,
        evidence_state=_evidence_state(bundle_files),
        checklist_state=_checklist_state(bundle_files),
        finalizer_gate_state=_finalizer_gate_state(bundle_files),
        last_action=last_action or (previous_state.last_action if previous_state else None),
        last_run_status=last_run_status or (previous_state.last_run_status if previous_state else None),
        latest_run_manifest=latest_run_manifest,
        latest_output_file=latest_output_file,
        latest_runtime_record_ids=runtime_ids,
        bundle_files=bundle_files,
        execution_contract=execution_contract,
        inputs={
            "topic": topic,
            "input_brief": input_brief,
            "target_path": target_path,
        },
        updated_at=datetime.now(timezone.utc).isoformat(),
    )


def snapshot_bundle_files(bundle: dict[str, Path]) -> dict[str, dict[str, Any]]:
    return {
        name: {
            "path": str(path),
            "exists": path.exists(),
        }
        for name, path in bundle.items()
    }


def infer_article_phase(bundle_files: dict[str, dict[str, Any]]) -> str:
    if _exists(bundle_files, "final_markdown") and _exists(bundle_files, "checklist"):
        return "finalized"
    if _exists(bundle_files, "review"):
        return "reviewed"
    if _exists(bundle_files, "draft"):
        return "drafted"
    if _exists(bundle_files, "claim_map"):
        return "claim-mapped"
    if _exists(bundle_files, "evidence_pack"):
        return "evidence-collected"
    if _exists(bundle_files, "brief"):
        return "briefed"
    return "not-started"


def infer_readiness_status(bundle_files: dict[str, dict[str, Any]], previous_status: str | None = None) -> str | None:
    if previous_status in ARTICLE_READINESS_STATUSES:
        return previous_status
    if _exists(bundle_files, "final_markdown") and _exists(bundle_files, "checklist"):
        return "strong-draft"
    if _exists(bundle_files, "final_markdown") and not _exists(bundle_files, "checklist"):
        return "strong-draft-with-blockers"
    return None


def load_article_bundle_state(path: Path) -> ArticleBundleState | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return article_bundle_state_from_payload(payload)


def article_bundle_state_from_payload(payload: dict[str, Any]) -> ArticleBundleState | None:
    work_id = _optional_text(payload.get("work_id"))
    article_slug = _optional_text(payload.get("article_slug"))
    current_phase = _optional_text(payload.get("current_phase"))
    current_status = _optional_text(payload.get("current_status"))
    updated_at = _optional_text(payload.get("updated_at"))
    if not work_id or not article_slug or not current_phase or not current_status or not updated_at:
        return None
    bundle_files = payload.get("bundle_files")
    if not isinstance(bundle_files, dict):
        bundle_files = {}
    runtime_ids = payload.get("latest_runtime_record_ids")
    if not isinstance(runtime_ids, list):
        runtime_ids = []
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    execution_contract = payload.get("execution_contract")
    if not isinstance(execution_contract, dict):
        execution_contract = None
    return ArticleBundleState(
        version=_optional_text(payload.get("version")) or ARTICLE_BUNDLE_STATE_VERSION,
        work_id=work_id,
        article_slug=article_slug,
        current_phase=current_phase,
        current_status=current_status,
        readiness_status=_optional_text(payload.get("readiness_status")),
        active_phase=_optional_text(payload.get("active_phase")),
        profile_id=_optional_text(payload.get("profile_id")),
        evidence_state=_optional_text(payload.get("evidence_state")) or "missing",
        checklist_state=_optional_text(payload.get("checklist_state")) or "not-started",
        finalizer_gate_state=_optional_text(payload.get("finalizer_gate_state")) or "not-ready",
        last_action=_optional_text(payload.get("last_action")),
        last_run_status=_optional_text(payload.get("last_run_status")),
        latest_run_manifest=_optional_text(payload.get("latest_run_manifest")),
        latest_output_file=_optional_text(payload.get("latest_output_file")),
        latest_runtime_record_ids=tuple(str(item).strip() for item in runtime_ids if str(item).strip()),
        bundle_files={str(key): value for key, value in bundle_files.items() if isinstance(value, dict)},
        execution_contract=execution_contract,
        inputs=inputs,
        updated_at=updated_at,
    )


def write_article_bundle_state(path: Path, state: ArticleBundleState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
    ) as handle:
        json.dump(state.to_dict(), handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def _evidence_state(bundle_files: dict[str, dict[str, Any]]) -> str:
    if _exists(bundle_files, "evidence_pack") and _exists(bundle_files, "claim_map"):
        return "mapped"
    if _exists(bundle_files, "evidence_pack"):
        return "collected"
    return "missing"


def _checklist_state(bundle_files: dict[str, dict[str, Any]]) -> str:
    if _exists(bundle_files, "checklist"):
        return "present"
    if _exists(bundle_files, "final_markdown"):
        return "missing"
    return "not-started"


def _finalizer_gate_state(bundle_files: dict[str, dict[str, Any]]) -> str:
    if _exists(bundle_files, "final_markdown") and _exists(bundle_files, "checklist") and _exists(bundle_files, "docx"):
        return "exported"
    if _exists(bundle_files, "final_markdown") and _exists(bundle_files, "checklist"):
        return "ready-for-export"
    if _exists(bundle_files, "final_markdown"):
        return "blocked-checklist"
    return "not-ready"


def _current_status(current_phase: str, readiness_status: str | None, last_run_status: str | None) -> str:
    if readiness_status:
        return readiness_status
    if current_phase == "not-started" and not last_run_status:
        return "not-started"
    return "in-progress"


def _active_phase_for_action(action: str | None) -> str | None:
    mapping = {
        "article": "drafted",
        "review": "reviewed",
        "repair": "repairing",
    }
    return mapping.get((action or "").strip().lower()) or None


def _exists(bundle_files: dict[str, dict[str, Any]], name: str) -> bool:
    payload = bundle_files.get(name)
    return bool(isinstance(payload, dict) and payload.get("exists"))


def _optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned:
            return cleaned
    return None
