from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .workspace import WorkConfig


LEGACY_REQUIRED_FIELDS = (
    "claim_id",
    "section_target",
    "claim_text",
    "claim_type",
    "verification_status",
    "source_package_item_ids",
    "primary_source_reference",
    "primary_verification_date",
    "support_scope",
    "draft_use",
    "notes",
)

EXPANDED_REQUIRED_FIELDS = (
    "claim_id",
    "section_target",
    "claim_text",
    "basis_type",
    "verification_status",
    "source_package_item_ids",
    "primary_identifier",
    "official_primary_link",
    "jurisdiction",
    "statement_precision",
    "knowledge_date",
    "verification_result",
    "support_scope",
    "draft_use",
    "false_attribution_check",
    "notes",
)


def audit_thesis_ledgers(work: WorkConfig) -> dict[str, object]:
    if not work.thesis:
        return _empty_advisory(available=False)

    ledger_files = sorted(work.thesis.ledgers_dir.glob("*.md")) if work.thesis.ledgers_dir.exists() else []
    claims: list[dict[str, str]] = []
    issues: list[dict[str, object]] = []

    for ledger_path in ledger_files:
        rows = _extract_ledger_rows(ledger_path.read_text(encoding="utf-8"))
        if not rows:
            continue
        for row in rows:
            claims.append(row)

    if not ledger_files:
        return _empty_advisory(available=False)

    verified_count = 0
    needs_recheck_count = 0
    unsafe_for_draft_count = 0
    analytical_count = 0
    safe_count = 0
    narrow_count = 0
    hold_count = 0
    missing_primary_date_count = 0

    for row in claims:
        verification_status = row.get("verification_status", "")
        draft_use = row.get("draft_use", "")
        claim_id = row.get("claim_id") or None
        section_target = row.get("section_target") or None

        if verification_status == "verified":
            verified_count += 1
        elif verification_status == "needs-recheck":
            needs_recheck_count += 1
        elif verification_status == "unsafe-for-draft":
            unsafe_for_draft_count += 1
        elif verification_status == "analytical-conclusion":
            analytical_count += 1

        if draft_use == "safe":
            safe_count += 1
        elif draft_use == "narrow":
            narrow_count += 1
        elif draft_use == "hold":
            hold_count += 1

        if verification_status == "verified" and not row.get("primary_verification_date"):
            missing_primary_date_count += 1

        if verification_status == "needs-recheck":
            issues.append(
                _issue(
                    "needs-recheck-claims",
                    "warn",
                    "Ledger still contains claims marked needs-recheck.",
                    claim_id=claim_id,
                    section_target=section_target,
                )
            )
        if verification_status == "unsafe-for-draft":
            issues.append(
                _issue(
                    "unsafe-for-draft-claims",
                    "warn",
                    "Ledger contains claims marked unsafe-for-draft.",
                    claim_id=claim_id,
                    section_target=section_target,
                )
            )
        if verification_status == "verified" and not row.get("primary_verification_date"):
            issues.append(
                _issue(
                    "verified-missing-primary-date",
                    "warn",
                    "Verified claim is missing primary verification date.",
                    claim_id=claim_id,
                    section_target=section_target,
                )
            )

    advisory_status = "clear"
    if not claims:
        advisory_status = "empty"
    elif unsafe_for_draft_count:
        advisory_status = "blocked-for-draft"
    elif needs_recheck_count or missing_primary_date_count:
        advisory_status = "needs-attention"

    return {
        "kind": "thesis-ledger-advisory",
        "version": "v1",
        "available": True,
        "advisory_status": advisory_status,
        "readiness_claim": "none",
        "does_not_replace": [
            "source-verification",
            "citation-checking",
            "argument-review",
            "submission-ready-verdict",
        ],
        "ledger_count": len(ledger_files),
        "claim_count": len(claims),
        "verified_count": verified_count,
        "needs_recheck_count": needs_recheck_count,
        "unsafe_for_draft_count": unsafe_for_draft_count,
        "analytical_count": analytical_count,
        "safe_count": safe_count,
        "narrow_count": narrow_count,
        "hold_count": hold_count,
        "missing_primary_date_count": missing_primary_date_count,
        "issues": _dedupe_issues(issues),
    }


def _extract_ledger_rows(text: str) -> list[dict[str, str]]:
    lines = text.splitlines()
    index = 0
    rows: list[dict[str, str]] = []
    legacy_required_set = set(LEGACY_REQUIRED_FIELDS)
    expanded_required_set = set(EXPANDED_REQUIRED_FIELDS)
    while index < len(lines) - 1:
        header_line = lines[index].strip()
        separator_line = lines[index + 1].strip()
        if not header_line.startswith("|") or not _is_separator_row(separator_line):
            index += 1
            continue
        headers = _split_table_row(header_line)
        header_set = set(headers)
        if not legacy_required_set.issubset(header_set) and not expanded_required_set.issubset(header_set):
            index += 1
            continue
        index += 2
        while index < len(lines):
            row_line = lines[index].strip()
            if not row_line.startswith("|"):
                break
            values = _split_table_row(row_line)
            if len(values) != len(headers):
                index += 1
                continue
            row = {header: _normalize_cell(value) for header, value in zip(headers, values)}
            rows.append(_normalize_ledger_row(row))
            index += 1
        continue
    return rows


def _split_table_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [item.strip() for item in raw.split("|")]


def _is_separator_row(line: str) -> bool:
    cells = _split_table_row(line)
    if not cells:
        return False
    return all(cell and set(cell) <= {"-", ":", " "} for cell in cells)


def _normalize_cell(value: str) -> str:
    return value.strip().strip("`")


def _normalize_ledger_row(row: dict[str, str]) -> dict[str, str]:
    normalized = dict(row)
    if not normalized.get("claim_type") and normalized.get("basis_type"):
        normalized["claim_type"] = normalized["basis_type"]
    if not normalized.get("primary_source_reference"):
        normalized["primary_source_reference"] = (
            normalized.get("primary_identifier")
            or normalized.get("official_primary_link")
            or ""
        )
    if not normalized.get("primary_verification_date") and normalized.get("knowledge_date"):
        normalized["primary_verification_date"] = normalized["knowledge_date"]
    return normalized


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    claim_id: str | None,
    section_target: str | None,
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "claim_id": claim_id,
        "section_target": section_target,
    }


def _dedupe_issues(items: Iterable[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[str, str, str]] = set()
    result: list[dict[str, object]] = []
    for item in items:
        key = (
            str(item.get("code") or ""),
            str(item.get("claim_id") or ""),
            str(item.get("section_target") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _empty_advisory(*, available: bool) -> dict[str, object]:
    return {
        "kind": "thesis-ledger-advisory",
        "version": "v1",
        "available": available,
        "advisory_status": "missing" if not available else "empty",
        "readiness_claim": "none",
        "does_not_replace": [
            "source-verification",
            "citation-checking",
            "argument-review",
            "submission-ready-verdict",
        ],
        "ledger_count": 0,
        "claim_count": 0,
        "verified_count": 0,
        "needs_recheck_count": 0,
        "unsafe_for_draft_count": 0,
        "analytical_count": 0,
        "safe_count": 0,
        "narrow_count": 0,
        "hold_count": 0,
        "missing_primary_date_count": 0,
        "issues": [],
    }
