from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


BLOCKING_SUBMISSION_CATEGORIES = {"dynamic-material", "primary-support", "standards-consistency"}


@dataclass(frozen=True)
class FinalizationCheckResult:
    status: str
    finalization_status: str
    effective_readiness_status: str | None
    allowed_exports: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    required_followups: tuple[str, ...]
    readiness_claim: str = "none"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": "article-finalization-check",
            "status": self.status,
            "finalization_status": self.finalization_status,
            "effective_readiness_status": self.effective_readiness_status,
            "allowed_exports": list(self.allowed_exports),
            "blocked_reasons": list(self.blocked_reasons),
            "required_followups": list(self.required_followups),
            "readiness_claim": self.readiness_claim,
        }


def evaluate_article_finalization(
    *,
    bundle: dict[str, Path],
    readiness_status: str | None,
    blockers: Iterable[dict[str, Any]],
    contract_gates: Iterable[dict[str, Any]],
) -> FinalizationCheckResult:
    blocked: list[str] = []
    followups: list[str] = []

    if not _exists(bundle, "final_markdown"):
        blocked.append("final-markdown-missing")
        followups.append("Create or verify final markdown before finalization.")
    if not _exists(bundle, "checklist"):
        blocked.append("checklist-missing")
        followups.append("Create final checklist before finalization.")
    if not _exists(bundle, "review"):
        blocked.append("review-missing")
        followups.append("Run article review before deterministic finalization.")

    blocker_categories = {
        _optional_text(item.get("category"))
        for item in blockers
        if isinstance(item, dict) and _optional_text(item.get("category"))
    }
    if "primary-support" in blocker_categories:
        blocked.append("primary-support-blockers")
    if "dynamic-material" in blocker_categories:
        blocked.append("dynamic-material-blockers")
    if "standards-consistency" in blocker_categories:
        blocked.append("standards-blockers")

    for gate in contract_gates:
        if not isinstance(gate, dict) or gate.get("status") != "block":
            continue
        if gate.get("blocks_submission_ready") or gate.get("blocks_export"):
            gate_id = _optional_text(gate.get("gate_id")) or "unknown"
            blocked.append(f"gate:{gate_id}")

    effective_status = _effective_readiness_status(readiness_status, blocked, blocker_categories)
    if blocked:
        return FinalizationCheckResult(
            status="block",
            finalization_status="blocked",
            effective_readiness_status=effective_status,
            allowed_exports=(),
            blocked_reasons=tuple(dict.fromkeys(blocked)),
            required_followups=tuple(dict.fromkeys(followups or ["Resolve blocking gates before export."])),
        )

    if _exists(bundle, "docx"):
        finalization_status = "exported"
    else:
        finalization_status = "export-ready"
    return FinalizationCheckResult(
        status="pass",
        finalization_status=finalization_status,
        effective_readiness_status=effective_status,
        allowed_exports=("docx",),
        blocked_reasons=(),
        required_followups=(),
    )


def _effective_readiness_status(
    readiness_status: str | None,
    blocked_reasons: list[str],
    blocker_categories: set[str | None],
) -> str | None:
    if readiness_status == "submission-ready" and (blocked_reasons or blocker_categories & BLOCKING_SUBMISSION_CATEGORIES):
        return "strong-draft-with-blockers"
    return readiness_status


def _exists(bundle: dict[str, Path], name: str) -> bool:
    path = bundle.get(name)
    return isinstance(path, Path) and path.exists()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
