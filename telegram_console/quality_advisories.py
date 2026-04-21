from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from .article_runtime_signals import extract_article_artifact_signals
from .guarded_prose import extract_guarded_prose_matches, load_guarded_prose_rules
from .workspace import WorkConfig, article_bundle_paths, discover_article_slugs

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
    "pinpoint_locator",
    "support_excerpt",
    "caveat_note",
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
    "pinpoint_locator",
    "support_excerpt",
    "caveat_note",
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
    "pinpoint locator": "pinpoint_locator",
    "pinpoint_locator": "pinpoint_locator",
    "support locator": "pinpoint_locator",
    "support_locator": "pinpoint_locator",
    "support excerpt": "support_excerpt",
    "support_excerpt": "support_excerpt",
    "holding summary": "support_excerpt",
    "holding_summary": "support_excerpt",
    "caveat note": "caveat_note",
    "caveat_note": "caveat_note",
    "limit note": "caveat_note",
    "limit_note": "caveat_note",
    "source ids": "source_ids",
    "source id": "source_id",
    "supported claim ids": "supported_claim_ids",
    "supported claim id": "supported_claim_ids",
    "source class": "source_class",
    "source role": "source_role",
    "url or repository": "source_url",
    "source url": "source_url",
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
QUALITY_ADVISORY_DOES_NOT_REPLACE = [
    "source-verification",
    "citation-checking",
    "contract-gates",
    "finalization-engine",
]
NON_FOREIGN_JURISDICTIONS = {
    "",
    "ru",
    "russia",
    "россия",
    "national",
    "comparative",
    "domestic",
    "local",
    "general",
    "mixed",
    "n/a",
    "na",
    "none",
    "unknown",
    "unspecified",
}
FOREIGN_JURISDICTION_CODES = {
    "eu",
    "eea",
    "us",
    "usa",
    "uk",
    "gb",
    "kz",
    "by",
    "ua",
    "de",
    "fr",
    "it",
    "es",
    "pl",
    "cn",
    "jp",
    "in",
    "br",
    "ca",
    "au",
    "tr",
}
NON_ANALYTICAL_BASIS_TYPES = DYNAMIC_BASIS_TYPES | SECONDARY_BASIS_TYPES
ARTICLE_PROSE_BLOCKER_FLAGS = {
    "citation-safety-gap": "citation_safety_gap",
    "close-paraphrase-risk": "close_paraphrase_risk",
    "footnote-consistency-gap": "footnote_consistency_gap",
    "citation-model-inconsistent": "citation_model_inconsistent",
    "bibliographic-wording-unsafe": "bibliographic_wording_unsafe",
    "counterargument-gap": "counterargument_gap",
    "missing-caveats": "missing_caveats",
    "overclaims-not-narrowed": "overclaims_not_narrowed",
    "inferential-gaps": "inferential_gaps",
}


def build_quality_advisories(work: WorkConfig) -> dict[str, object]:
    return {
        "kind": "quality-advisories",
        "version": "v1",
        "advisory_only": True,
        "readiness_claim": "none",
        "does_not_replace": list(QUALITY_ADVISORY_DOES_NOT_REPLACE),
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
                    verification_log_records.append(
                        _normalize_claim_record(row, artifact_path=path, record_format="expanded")
                    )
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

    coverage = _thesis_quality_coverage(
        ledger_records=ledger_records,
        verification_log_records=verification_log_records,
        has_expanded_ledger=has_expanded_ledger,
        has_legacy_ledger=has_legacy_ledger,
        has_log_file=has_log_file,
    )

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
    bundle_coverages: list[str] = []
    prose_coverages: list[str] = []
    prose_issues: list[dict[str, str]] = []
    for slug in discover_article_slugs(work):
        bundle = article_bundle_paths(work, slug)
        bundle_payload = _article_bundle_quality_payload(bundle, slug=slug)
        if bundle_payload is None:
            continue
        for source_name in bundle_payload["sources"]:
            _append_source(sources, source_name)
        bundle_coverages.append(bundle_payload["coverage"])
        claim_records.extend(bundle_payload["claim_records"])
        prose_payload = _article_bundle_review_payload(bundle)
        if prose_payload is not None:
            prose_coverages.append(str(prose_payload["coverage"]))
            prose_issues.extend(prose_payload["issues"])  # type: ignore[arg-type]
            for source_name in prose_payload["sources"]:  # type: ignore[index]
                _append_source(sources, str(source_name))

    coverage = _aggregate_bundle_coverages(bundle_coverages)
    prose_coverage = _aggregate_bundle_coverages(prose_coverages)

    return {
        "coverage": coverage,
        "sources": sources,
        "verification_advisory": _build_verification_advisory(records=claim_records, base_status=coverage),
        "source_mix_advisory": _build_source_mix_advisory(records=claim_records, base_status=coverage),
        "prose_advisory": _build_advisory_payload(prose_coverage, prose_issues),
    }


def _thesis_quality_coverage(
    *,
    ledger_records: list[dict[str, str]],
    verification_log_records: list[dict[str, str]],
    has_expanded_ledger: bool,
    has_legacy_ledger: bool,
    has_log_file: bool,
) -> str:
    if not ledger_records and not has_log_file:
        return "missing"
    if not has_expanded_ledger:
        return "limited" if ledger_records or has_log_file else "missing"
    if has_legacy_ledger or not has_log_file or not verification_log_records:
        return "limited"

    ledger_claim_ids = _structured_claim_ids(
        record for record in ledger_records if record.get("record_format") == "expanded"
    )
    verification_claim_ids = _structured_claim_ids(verification_log_records)
    if not ledger_claim_ids or ledger_claim_ids != verification_claim_ids:
        return "limited"
    return "full"


def _article_bundle_quality_payload(bundle: dict[str, Path], *, slug: str) -> dict[str, object] | None:
    evidence_path = bundle["evidence_pack"]
    claim_map_path = bundle["claim_map"]
    evidence_present = evidence_path.exists()
    claim_map_present = claim_map_path.exists()
    if not evidence_present and not claim_map_present:
        return None

    sources: list[str] = []
    evidence_claim_records: list[dict[str, str]] = []
    claim_map_records: list[dict[str, str]] = []

    if evidence_present:
        _append_source(sources, "evidence-pack")
        evidence_records = _parse_artifact_records(evidence_path)
        source_register_records = [record for record in evidence_records if _is_source_register_record(record)]
        evidence_claim_records = [record for record in evidence_records if _has_claim_passport_shape(record)]
        evidence_claim_records = _merge_source_register_metadata(
            claim_records=evidence_claim_records,
            source_records=source_register_records,
        )

    if claim_map_present:
        _append_source(sources, "claim-map")
        claim_map_records = [
            record for record in _parse_artifact_records(claim_map_path) if _has_claim_passport_shape(record)
        ]

    coverage = "limited"
    if evidence_present and claim_map_present and evidence_claim_records and claim_map_records:
        coverage = "full"

    return {
        "slug": slug,
        "sources": sources,
        "coverage": coverage,
        "claim_records": _merge_claim_records([*evidence_claim_records, *claim_map_records]),
    }


def _article_bundle_review_payload(bundle: dict[str, Path]) -> dict[str, object] | None:
    artifact_texts: dict[str, str] = {}
    sources: list[str] = []
    for source_name, key in (("review", "review"), ("checklist", "checklist")):
        path = bundle[key]
        if not path.exists():
            continue
        artifact_texts[source_name] = path.read_text(encoding="utf-8")
        sources.append(source_name)
    if not artifact_texts:
        return None

    signals = extract_article_artifact_signals(artifact_texts)
    issues: list[dict[str, str]] = []
    for blocker in signals.blockers:
        flag = _article_blocker_flag(blocker.code)
        if not flag:
            continue
        issue = {
            "flag": flag,
            "message": blocker.message,
        }
        for key in ("source", "field", "value"):
            value = blocker.details.get(key)
            if isinstance(value, str) and value:
                issue[key] = value
        issues.append(issue)

    return {
        "coverage": "full" if len(artifact_texts) == 2 else "limited",
        "sources": tuple(sources),
        "issues": issues,
    }


def _aggregate_bundle_coverages(bundle_coverages: Iterable[str]) -> str:
    coverages = [coverage for coverage in bundle_coverages if coverage]
    if not coverages:
        return "missing"
    if any(coverage == "limited" for coverage in coverages):
        return "limited"
    if all(coverage == "full" for coverage in coverages):
        return "full"
    return "limited"


def _parse_artifact_records(path: Path) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for record in _parse_markdown_records(path.read_text(encoding="utf-8"), artifact_path=path):
        records.append(
            _normalize_claim_record(
                record,
                artifact_path=path,
                record_format=record.get("record_format") or "expanded",
            )
        )
    return records


def _structured_claim_ids(records: Iterable[dict[str, str]]) -> set[str]:
    return {
        claim_id for claim_id in (_normalize_inline_value(record.get("claim_id", "")) for record in records) if claim_id
    }


def _merge_claim_records(records: Iterable[dict[str, str]]) -> list[dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    anonymous: list[dict[str, str]] = []
    for record in records:
        claim_id = _normalize_inline_value(record.get("claim_id", ""))
        if not claim_id:
            anonymous.append(dict(record))
            continue
        current = merged.get(claim_id)
        if current is None:
            merged[claim_id] = dict(record)
            continue
        for key, value in record.items():
            if value and not current.get(key):
                current[key] = value
    return [*merged.values(), *anonymous]


def _merge_source_register_metadata(
    *,
    claim_records: Iterable[dict[str, str]],
    source_records: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    sources_by_id: dict[str, dict[str, str]] = {}
    sources_by_claim: dict[str, list[dict[str, str]]] = {}
    for source_record in source_records:
        source_id = _normalized_text(source_record.get("source_id"))
        if source_id:
            sources_by_id[source_id] = source_record
        for claim_id in _split_multi_value(source_record.get("supported_claim_ids")):
            sources_by_claim.setdefault(claim_id, []).append(source_record)

    merged_records: list[dict[str, str]] = []
    for claim_record in claim_records:
        merged = dict(claim_record)
        related_sources: list[dict[str, str]] = []
        for source_id in _split_multi_value(claim_record.get("source_ids")):
            source = sources_by_id.get(_normalized_text(source_id))
            if source and source not in related_sources:
                related_sources.append(source)
        claim_id = _normalize_inline_value(claim_record.get("claim_id", ""))
        for source in sources_by_claim.get(claim_id, []):
            if source not in related_sources:
                related_sources.append(source)
        for field in STATS_METADATA_FIELDS:
            if merged.get(field):
                continue
            for source in related_sources:
                value = source.get(field)
                if value:
                    merged[field] = value
                    break
        merged_records.append(merged)
    return merged_records


def _is_source_register_record(record: dict[str, str]) -> bool:
    return any(
        record.get(field)
        for field in (
            "source_id",
            "supported_claim_ids",
            "source_class",
            "source_role",
            "source_url",
        )
    )


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
            issues.append(
                _issue("missing_official_primary_link", "Strong claim is missing an official primary link.", record)
            )
        if _requires_strict_claim_passport(basis_type):
            if not record.get("pinpoint_locator"):
                issues.append(
                    _issue(
                        "missing_pinpoint_locator",
                        "Non-analytical claim is missing a pinpoint locator for the supporting material.",
                        record,
                    )
                )
            if not record.get("support_excerpt"):
                issues.append(
                    _issue(
                        "missing_support_excerpt",
                        "Non-analytical claim is missing a short support excerpt or holding summary.",
                        record,
                    )
                )
            if _claim_needs_caveat(record) and not record.get("caveat_note"):
                issues.append(
                    _issue(
                        "partial_support_without_caveat",
                        "Qualified or partial claim is missing an explicit caveat or limit note.",
                        record,
                    )
                )
        if "needs-recheck" in status or "needs recheck" in status or "stale" in result:
            issues.append(
                _issue("stale_knowledge_date", "Claim is explicitly marked for re-check or stale verification.", record)
            )
        if basis_type in DYNAMIC_BASIS_TYPES and not record.get("knowledge_date"):
            issues.append(_issue("stale_knowledge_date", "Dynamic material is missing a knowledge_date.", record))
        if _claim_is_unsafe_for_safe_drafting(record):
            issues.append(
                _issue(
                    "unsafe_draft_use",
                    "Claim is marked safe for drafting despite partial, stale, or unsafe verification.",
                    record,
                )
            )
        false_attribution_check = _normalized_text(record.get("false_attribution_check"))
        if false_attribution_check and false_attribution_check not in PASS_VALUES:
            issues.append(_issue("false_attribution_risk", "False attribution check needs review.", record))

    return _build_advisory_payload(base_status, issues)


def _build_source_mix_advisory(
    *,
    records: Iterable[dict[str, str]],
    base_status: str,
) -> dict[str, object]:
    claim_records = [
        record for record in records if record.get("record_format") != "legacy" and record.get("basis_type")
    ]
    issues: list[dict[str, str]] = []
    basis_counts: dict[str, int] = {}
    for record in claim_records:
        basis_type = _normalized_text(record.get("basis_type"))
        if basis_type:
            basis_counts[basis_type] = basis_counts.get(basis_type, 0) + 1
        if basis_type == "empirical" and not _record_has_stats_metadata(record):
            issues.append(
                _issue("stats_missing_metadata", "Empirical support is missing basic statistics metadata.", record)
            )
        jurisdiction = _normalized_text(record.get("jurisdiction"))
        if (
            _is_foreign_jurisdiction(jurisdiction)
            and basis_type in SECONDARY_BASIS_TYPES
            and not record.get("official_primary_link")
        ):
            issues.append(
                _issue("foreign_law_secondary_only", "Foreign-law claim relies on secondary material only.", record)
            )

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
    flags: list[str] = []
    for item in deduped:
        flag = str(item.get("flag") or "")
        if flag and flag not in flags:
            flags.append(flag)
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
        "flags": flags,
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
    return any(
        record.get(field)
        for field in ("verification_result", "official_primary_link", "jurisdiction", "knowledge_date")
    )


def _record_has_stats_metadata(record: dict[str, str]) -> bool:
    return all(record.get(field) for field in ("period", "territory", "method", "provider"))


def _requires_strict_claim_passport(basis_type: str | None) -> bool:
    normalized = _normalized_text(basis_type)
    return bool(normalized and (normalized in NON_ANALYTICAL_BASIS_TYPES or normalized == "primary-normative"))


def _claim_needs_caveat(record: dict[str, str]) -> bool:
    statement_precision = _normalized_text(record.get("statement_precision"))
    support_scope = _normalized_text(record.get("support_scope"))
    verification_result = _normalized_text(record.get("verification_result"))
    return (
        statement_precision in {"qualified", "context-only"}
        or support_scope in {"partial", "context-only"}
        or "partial support" in verification_result
    )


def _claim_is_unsafe_for_safe_drafting(record: dict[str, str]) -> bool:
    draft_use = _normalized_text(record.get("draft_use"))
    if draft_use != "safe":
        return False
    verification_status = _normalized_text(record.get("verification_status"))
    support_scope = _normalized_text(record.get("support_scope"))
    verification_result = _normalized_text(record.get("verification_result"))
    if verification_status in {"needs-recheck", "unsafe-for-draft"}:
        return True
    if support_scope in {"partial", "context-only"}:
        return True
    if "partial support" in verification_result or "not found in primary" in verification_result:
        return True
    return False


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
        claim_marker = str(item.get("claim_id") or "")
        artifact_marker = str(item.get("artifact_path") or item.get("source") or item.get("field") or "")
        key = (
            str(item.get("flag") or ""),
            claim_marker or artifact_marker,
            str(item.get("message") or ""),
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


def _split_multi_value(value: str | None) -> list[str]:
    cleaned = _normalize_inline_value(value or "")
    if not cleaned:
        return []
    return [item.strip() for item in re.split(r"[;,]", cleaned) if item.strip()]


def _normalized_text(value: str | None) -> str:
    return _normalize_inline_value(value or "").casefold()


def _article_blocker_flag(code: str | None) -> str | None:
    normalized = _normalized_text(code)
    for suffix, flag in ARTICLE_PROSE_BLOCKER_FLAGS.items():
        if normalized.endswith(suffix):
            return flag
    return None


def _looks_truthy(value: str | None) -> bool:
    normalized = _normalized_text(value)
    return normalized.startswith(("yes", "да", "true")) or normalized in {"generic", "present"}


def _is_foreign_jurisdiction(value: str) -> bool:
    normalized = _normalized_text(value)
    if normalized in NON_FOREIGN_JURISDICTIONS:
        return False
    tokens = {token for token in re.split(r"[^a-zа-я0-9]+", normalized) if token}
    if "foreign" in tokens or "foreign" in normalized or "зарубеж" in normalized:
        return True
    if tokens & {"eu", "eea", "european", "европейский", "евросоюз"}:
        return True
    if tokens & FOREIGN_JURISDICTION_CODES:
        return True
    return normalized in FOREIGN_JURISDICTION_CODES
