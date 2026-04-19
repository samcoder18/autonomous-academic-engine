from __future__ import annotations

from pathlib import Path
from typing import Iterable
import re

from .guarded_prose import extract_guarded_prose_matches, load_guarded_prose_rules
from .workspace import WorkConfig


LEGACY_LEDGER_FIELDS = {
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
}

EXPANDED_LEDGER_FIELDS = {
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
}

VERIFICATION_LOG_FIELDS = {
    "claim_id",
    "primary_identifier",
    "official_primary_link",
    "knowledge_date",
    "verification_result",
    "verification_status",
    "false_attribution_check",
    "notes",
}

CLAIM_FIELD_ALIASES = {
    "claim id": "claim_id",
    "claim text": "claim_text",
    "basis type": "basis_type",
    "basis_type": "basis_type",
    "claim type": "basis_type",
    "claim_type": "basis_type",
    "primary identifier": "primary_identifier",
    "primary_identifier": "primary_identifier",
    "official primary link": "official_primary_link",
    "official_primary_link": "official_primary_link",
    "jurisdiction": "jurisdiction",
    "statement precision": "statement_precision",
    "statement_precision": "statement_precision",
    "knowledge date": "knowledge_date",
    "knowledge_date": "knowledge_date",
    "verification result": "verification_result",
    "verification_result": "verification_result",
    "verification status": "verification_status",
    "verification_status": "verification_status",
    "status": "verification_status",
    "support scope": "support_scope",
    "support_scope": "support_scope",
    "draft use": "draft_use",
    "draft_use": "draft_use",
    "false attribution check": "false_attribution_check",
    "false_attribution_check": "false_attribution_check",
    "source ids": "source_ids",
    "source id": "source_id",
    "source class": "source_class",
    "source role": "source_role",
    "period": "period",
    "territory": "territory",
    "method": "method",
    "provider": "provider",
    "sample": "sample",
    "dataset": "dataset",
    "generic prose pattern": "generic_prose_pattern",
    "empty emphasis": "empty_emphasis",
    "term": "term",
    "preferred usage": "preferred_usage",
    "paragraph": "paragraph",
    "notes": "notes",
}

DYNAMIC_BASIS_TYPES = {"primary-normative", "official-guidance", "court-decision", "empirical"}
SECONDARY_BASIS_TYPES = {"secondary-doctrine", "news", "commentary"}
PASS_VALUES = {"passed", "pass", "clear", "ok", "none", "no", "false", "n/a"}
STATS_METADATA_FIELDS = {"period", "territory", "method", "provider", "sample", "dataset"}
PROSE_RULE_FLAG_MAP = {
    "guarded-prose-generic-prose-pattern": "generic_prose_pattern",
    "guarded-prose-empty-emphasis": "empty_emphasis",
}


def build_quality_advisories(work: WorkConfig) -> dict[str, object]:
    return {
        "kind": "quality-advisories",
        "version": "v1",
        "advisory_only": True,
        "readiness_claim": "none",
        "does_not_replace": [
            "source-verification",
            "citation-checking",
            "contract-gates",
            "finalization-engine",
        ],
        "thesis": _build_thesis_quality_advisory(work),
        "article": _build_article_quality_advisory(work),
    }


def _build_thesis_quality_advisory(work: WorkConfig) -> dict[str, object]:
    if not work.thesis:
        return _missing_lane_payload()

    sources: list[str] = []
    ledger_records: list[dict[str, str]] = []
    verification_log_records: list[dict[str, str]] = []
    glossary_records: list[dict[str, str]] = []
    micro_review_records: list[dict[str, str]] = []
    prose_texts: list[tuple[str, str]] = []
    has_expanded_ledger = False
    has_legacy_ledger = False
    has_log_file = False

    if work.thesis.ledgers_dir.exists():
        for path in sorted(work.thesis.ledgers_dir.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            if "verification-log" in path.stem.casefold():
                has_log_file = True
                _append_source(sources, "verification-log")
                rows = _extract_markdown_table_rows(text, VERIFICATION_LOG_FIELDS)
                for row in rows:
                    verification_log_records.append(_normalize_claim_record(row, artifact_path=path, record_format="expanded"))
                continue
            _append_source(sources, "ledger")
            expanded_rows = _extract_markdown_table_rows(text, EXPANDED_LEDGER_FIELDS)
            if expanded_rows:
                has_expanded_ledger = True
                for row in expanded_rows:
                    ledger_records.append(_normalize_claim_record(row, artifact_path=path, record_format="expanded"))
                continue
            legacy_rows = _extract_markdown_table_rows(text, LEGACY_LEDGER_FIELDS)
            if legacy_rows:
                has_legacy_ledger = True
                for row in legacy_rows:
                    ledger_records.append(_normalize_claim_record(row, artifact_path=path, record_format="legacy"))

    if work.thesis.reviews_dir.exists():
        for path in sorted(work.thesis.reviews_dir.glob("*-glossary.md")):
            _append_source(sources, "work-glossary")
            text = path.read_text(encoding="utf-8")
            prose_texts.append(("work-glossary", text))
            glossary_records.extend(_parse_markdown_records(text, artifact_path=path))
        for path in sorted(work.thesis.reviews_dir.glob("*-micro-review.md")):
            _append_source(sources, "paragraph-micro-review")
            text = path.read_text(encoding="utf-8")
            prose_texts.append(("paragraph-micro-review", text))
            micro_review_records.extend(_parse_markdown_records(text, artifact_path=path))

    coverage = "missing"
    if has_expanded_ledger and verification_log_records:
        coverage = "full"
    elif ledger_records or has_log_file:
        coverage = "limited"

    verification_advisory = _build_verification_advisory(
        records=[*ledger_records, *verification_log_records],
        base_status=coverage,
    )
    source_mix_advisory = _build_source_mix_advisory(records=ledger_records, base_status=coverage)
    prose_base_status = _optional_coverage(glossary_records or micro_review_records or prose_texts)
    prose_advisory = _build_prose_advisory(
        lane="thesis",
        prose_texts=prose_texts,
        records=[*glossary_records, *micro_review_records],
        base_status=prose_base_status,
    )

    return {
        "coverage": coverage,
        "sources": sources,
        "verification_advisory": verification_advisory,
        "source_mix_advisory": source_mix_advisory,
        "prose_advisory": prose_advisory,
    }


def _build_article_quality_advisory(work: WorkConfig) -> dict[str, object]:
    if not work.article:
        return _missing_lane_payload()

    sources: list[str] = []
    claim_records: list[dict[str, str]] = []
    evidence_seen = False
    claim_map_seen = False

    if work.article.evidence_dir.exists():
        for path in sorted(work.article.evidence_dir.glob("*.md")):
            evidence_seen = True
            _append_source(sources, "evidence-pack")
            text = path.read_text(encoding="utf-8")
            records = _parse_markdown_records(text, artifact_path=path)
            for record in records:
                if _has_claim_passport_shape(record):
                    claim_records.append(record)

    if work.article.claim_maps_dir.exists():
        for path in sorted(work.article.claim_maps_dir.glob("*.md")):
            claim_map_seen = True
            _append_source(sources, "claim-map")
            text = path.read_text(encoding="utf-8")
            records = _parse_markdown_records(text, artifact_path=path)
            for record in records:
                if _has_claim_passport_shape(record):
                    claim_records.append(record)

    coverage = "missing"
    record_sources = {record.get("artifact_path") for record in claim_records if record.get("artifact_path")}
    if evidence_seen and claim_map_seen and len(record_sources) >= 2:
        coverage = "full"
    elif evidence_seen or claim_map_seen:
        coverage = "limited"

    return {
        "coverage": coverage,
        "sources": sources,
        "verification_advisory": _build_verification_advisory(records=claim_records, base_status=coverage),
        "source_mix_advisory": _build_source_mix_advisory(records=claim_records, base_status=coverage),
        "prose_advisory": _build_advisory_payload("missing", []),
    }


def _build_verification_advisory(
    *,
    records: Iterable[dict[str, str]],
    base_status: str,
) -> dict[str, object]:
    issues: list[dict[str, str]] = []
    for record in records:
        record_format = record.get("record_format", "")
        if record_format == "legacy":
            continue
        result = _normalized_text(record.get("verification_result"))
        status = _normalized_text(record.get("verification_status"))
        basis_type = _normalized_text(record.get("basis_type"))

        if "not found in primary" in result:
            issues.append(_issue("not_found_in_primary", "Claim was not found in the cited primary material.", record))
        if "partial support" in result or record.get("support_scope", "").strip().casefold() == "partial":
            issues.append(_issue("partial_support", "Claim has only partial support in the cited material.", record))
        if "conflicting primary" in result:
            issues.append(_issue("conflicting_primary", "Primary materials appear to conflict on this claim.", record))
        if basis_type and basis_type != "analytical" and not record.get("official_primary_link"):
            issues.append(_issue("missing_official_primary_link", "Strong claim is missing an official primary link.", record))
        if "needs-recheck" in status or "needs recheck" in status or "stale" in result:
            issues.append(_issue("stale_knowledge_date", "Claim is explicitly marked for re-check or stale verification.", record))
        if basis_type in DYNAMIC_BASIS_TYPES and not record.get("knowledge_date"):
            issues.append(_issue("stale_knowledge_date", "Dynamic material is missing a knowledge_date.", record))
        false_attribution_check = _normalized_text(record.get("false_attribution_check"))
        if false_attribution_check and false_attribution_check not in PASS_VALUES:
            issues.append(_issue("false_attribution_risk", "False attribution check needs review.", record))

    return _build_advisory_payload(base_status, issues)


def _build_source_mix_advisory(
    *,
    records: Iterable[dict[str, str]],
    base_status: str,
) -> dict[str, object]:
    claim_records = [record for record in records if record.get("record_format") != "legacy" and record.get("basis_type")]
    issues: list[dict[str, str]] = []
    basis_counts: dict[str, int] = {}
    for record in claim_records:
        basis_type = _normalized_text(record.get("basis_type"))
        if basis_type:
            basis_counts[basis_type] = basis_counts.get(basis_type, 0) + 1
        if basis_type == "empirical" and not _record_has_stats_metadata(record):
            issues.append(_issue("stats_missing_metadata", "Empirical support is missing basic statistics metadata.", record))
        jurisdiction = _normalized_text(record.get("jurisdiction"))
        if _is_foreign_jurisdiction(jurisdiction) and basis_type in SECONDARY_BASIS_TYPES and not record.get("official_primary_link"):
            issues.append(_issue("foreign_law_secondary_only", "Foreign-law claim relies on secondary material only.", record))

    total_claims = sum(basis_counts.values())
    if total_claims >= 2 and len(basis_counts) == 1:
        overloaded_type = next(iter(basis_counts))
        issues.append(
            {
                "flag": "single_source_type_overload",
                "message": "Claim set relies on a single source type.",
                "basis_type": overloaded_type,
            }
        )

    return _build_advisory_payload(base_status, issues)


def _build_prose_advisory(
    *,
    lane: str,
    prose_texts: list[tuple[str, str]],
    records: Iterable[dict[str, str]],
    base_status: str,
) -> dict[str, object]:
    issues: list[dict[str, str]] = []
    for record in records:
        if _looks_truthy(record.get("generic_prose_pattern")):
            issues.append(_issue("generic_prose_pattern", "Paragraph review flags a generic prose pattern.", record))
        if _looks_truthy(record.get("empty_emphasis")):
            issues.append(_issue("empty_emphasis", "Paragraph review flags empty emphasis.", record))

    rules = load_guarded_prose_rules(lane, mode="advisory")
    for source_name, text in prose_texts:
        for match in extract_guarded_prose_matches(
            source_name,
            text,
            normalize_line=_normalize_inline_value,
            rules=rules,
        ):
            flag = PROSE_RULE_FLAG_MAP.get(match["code"])
            if not flag:
                continue
            issues.append(
                {
                    "flag": flag,
                    "message": match["message"],
                    "source": source_name,
                    "field": match["field"],
                    "value": match["value"],
                }
            )

    return _build_advisory_payload(base_status, issues)


def _build_advisory_payload(base_status: str, issues: list[dict[str, str]]) -> dict[str, object]:
    deduped = _dedupe_issues(issues)
    if base_status == "missing":
        status = "missing"
    elif deduped:
        status = "needs-attention"
    elif base_status == "limited":
        status = "limited"
    else:
        status = "clear"
    return {
        "status": status,
        "issue_count": len(deduped),
        "flags": [item["flag"] for item in deduped],
        "issues": deduped,
    }


def _missing_lane_payload() -> dict[str, object]:
    return {
        "coverage": "missing",
        "sources": [],
        "verification_advisory": _build_advisory_payload("missing", []),
        "source_mix_advisory": _build_advisory_payload("missing", []),
        "prose_advisory": _build_advisory_payload("missing", []),
    }


def _optional_coverage(has_anything: object) -> str:
    return "full" if has_anything else "missing"


def _append_source(target: list[str], source_name: str) -> None:
    if source_name not in target:
        target.append(source_name)


def _extract_markdown_table_rows(text: str, required_fields: set[str]) -> list[dict[str, str]]:
    lines = text.splitlines()
    index = 0
    rows: list[dict[str, str]] = []
    while index < len(lines) - 1:
        header_line = lines[index].strip()
        separator_line = lines[index + 1].strip()
        if not header_line.startswith("|") or not _is_separator_row(separator_line):
            index += 1
            continue
        headers = _split_table_row(header_line)
        if not required_fields.issubset(set(headers)):
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
            rows.append({header: _normalize_inline_value(value) for header, value in zip(headers, values)})
            index += 1
    return rows


def _parse_markdown_records(text: str, *, artifact_path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for raw_line in text.splitlines():
        heading = re.match(r"^\s*###\s+(.+?)\s*$", raw_line)
        if heading:
            if _record_has_content(current):
                current["artifact_path"] = str(artifact_path)
                current.setdefault("record_format", "expanded")
                records.append(current)
            current = {"heading": heading.group(1).strip()}
            continue
        match = re.match(r"^\s*(?:[-*]\s+)?([^:\n]+):\s*(.*?)\s*$", raw_line)
        if not match:
            continue
        key = _normalize_key(match.group(1))
        field = CLAIM_FIELD_ALIASES.get(key)
        if not field:
            continue
        current[field] = _normalize_inline_value(match.group(2))
    if _record_has_content(current):
        current["artifact_path"] = str(artifact_path)
        current.setdefault("record_format", "expanded")
        records.append(current)
    return records


def _normalize_claim_record(
    record: dict[str, str],
    *,
    artifact_path: Path,
    record_format: str,
) -> dict[str, str]:
    normalized = dict(record)
    normalized["artifact_path"] = str(artifact_path)
    normalized["record_format"] = record_format
    if not normalized.get("basis_type") and normalized.get("claim_type"):
        normalized["basis_type"] = normalized["claim_type"]
    if not normalized.get("primary_identifier") and normalized.get("primary_source_reference"):
        normalized["primary_identifier"] = normalized["primary_source_reference"]
    if not normalized.get("knowledge_date") and normalized.get("primary_verification_date"):
        normalized["knowledge_date"] = normalized["primary_verification_date"]
    return normalized


def _has_claim_passport_shape(record: dict[str, str]) -> bool:
    if not record.get("claim_id") or not record.get("basis_type"):
        return False
    return any(record.get(field) for field in ("verification_result", "official_primary_link", "jurisdiction", "knowledge_date"))


def _record_has_stats_metadata(record: dict[str, str]) -> bool:
    return all(record.get(field) for field in ("period", "territory", "method", "provider"))


def _record_has_content(record: dict[str, str]) -> bool:
    return any(key not in {"heading"} and value for key, value in record.items())


def _issue(flag: str, message: str, record: dict[str, str]) -> dict[str, str]:
    issue = {
        "flag": flag,
        "message": message,
    }
    for key in ("claim_id", "basis_type", "artifact_path", "jurisdiction", "verification_status"):
        value = record.get(key)
        if value:
            issue[key] = value
    return issue


def _dedupe_issues(issues: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    deduped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in issues:
        key = (
            str(item.get("flag") or ""),
            str(item.get("claim_id") or ""),
            str(item.get("artifact_path") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(item))
    return deduped


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


def _normalize_key(value: str) -> str:
    cleaned = re.sub(r"[`*]+", "", value).strip().casefold()
    cleaned = cleaned.replace("_", " ")
    return re.sub(r"\s+", " ", cleaned)


def _normalize_inline_value(value: str) -> str:
    cleaned = re.sub(r"[`*]+", "", value).strip()
    return re.sub(r"\s+", " ", cleaned)


def _normalized_text(value: str | None) -> str:
    return _normalize_inline_value(value or "").casefold()


def _looks_truthy(value: str | None) -> bool:
    normalized = _normalized_text(value)
    return normalized.startswith(("yes", "да", "true")) or normalized in {"generic", "present"}


def _is_foreign_jurisdiction(value: str) -> bool:
    normalized = value.casefold()
    return bool(normalized) and normalized not in {"ru", "russia", "россия"} and (
        "foreign" in normalized or "eu" in normalized or "us" in normalized or "uk" in normalized or normalized not in {"ru", "russia", "россия"}
    )
