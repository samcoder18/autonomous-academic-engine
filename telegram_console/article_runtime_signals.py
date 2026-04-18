from __future__ import annotations

from dataclasses import dataclass
import re

from .guarded_prose import extract_guarded_prose_blockers, load_guarded_prose_rules
from .repair_kernel import Blocker


ARTICLE_STATUS_SEVERITY = {
    "submission-ready": 0,
    "strong-draft": 1,
    "strong-draft-with-blockers": 2,
}

FIELD_ALIASES = {
    "verdict": {
        "verdict",
        "evaluator verdict",
        "final verdict",
        "post-repair verdict",
        "repair verdict",
    },
    "status": {
        "status",
        "final status",
        "article status",
        "readiness status",
    },
    "all significant claims have support": {
        "all significant claims have support",
        "significant claims traced to evidence pack",
    },
    "primary support is sufficient": {
        "primary support is sufficient",
        "primary-source policy respected",
    },
    "dynamic materials re-checked on date of writing": {
        "dynamic materials re-checked on date of writing",
    },
    "official raw standard loaded": {
        "official raw standard loaded",
    },
    "normalized profile loaded": {
        "normalized profile loaded",
    },
    "conflicts in requirements": {
        "conflicts in requirements",
    },
    "unsafe or overstated claims": {
        "unsafe or overstated claims",
    },
    "inferential gaps": {
        "inferential gaps",
    },
    "checklist blockers": {
        "checklist blockers",
        "remaining blockers",
        "submission blockers",
    },
    "formatting blockers": {
        "formatting blockers",
        "format blockers",
        "formatting issues",
    },
    "what still blocks formal submission": {
        "what still blocks formal submission",
        "formal submission blockers",
        "what blocks formal submission",
    },
}

FIELD_ALIAS_INDEX = {
    alias: canonical
    for canonical, aliases in FIELD_ALIASES.items()
    for alias in aliases
}

GUARDED_PROSE_RULES = load_guarded_prose_rules("article")


@dataclass(frozen=True)
class ArticleArtifactSignals:
    readiness_status: str | None
    blockers: tuple[Blocker, ...]


def extract_article_artifact_signals(artifact_texts: dict[str, str]) -> ArticleArtifactSignals:
    statuses: list[str] = []
    blockers: list[Blocker] = []

    for source_name, text in artifact_texts.items():
        statuses.extend(_extract_source_statuses(source_name, text))
        blockers.extend(_extract_source_blockers(source_name, text))

    return ArticleArtifactSignals(
        readiness_status=_merge_article_readiness_statuses(statuses),
        blockers=_dedupe_blockers(blockers),
    )


def _extract_source_statuses(source_name: str, text: str) -> list[str]:
    statuses: list[str] = []
    for field_key, field_value in _iter_article_artifact_fields(text):
        if field_key not in {"verdict", "status"}:
            continue
        status = _parse_article_status_field(field_value)
        if status:
            statuses.append(status)

    if source_name == "output":
        for line in text.splitlines():
            status = _parse_article_status_field(line)
            if status:
                statuses.append(status)
    return statuses


def _extract_source_blockers(source_name: str, text: str) -> list[Blocker]:
    blockers: list[Blocker] = []
    for field_key, field_value in _iter_article_artifact_fields(text):
        blocker = _artifact_blocker_from_field(source_name, field_key, field_value)
        if blocker is not None:
            blockers.append(blocker)
    blockers.extend(
        extract_guarded_prose_blockers(
            source_name,
            text,
            normalize_line=_normalize_artifact_value,
            rules=GUARDED_PROSE_RULES,
            build_blocker=_build_artifact_blocker,
        )
    )
    return blockers


def _artifact_blocker_from_field(source_name: str, field_key: str, field_value: str) -> Blocker | None:
    clean_value = _normalize_artifact_value(field_value)
    if not clean_value or field_key in {"verdict", "status"}:
        return None

    if field_key == "all significant claims have support" and _looks_negative_response(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category="primary-support",
            code="claim-support-incomplete",
            message="Artifact says not all significant claims have verified support.",
        )
    if field_key == "primary support is sufficient" and _looks_negative_response(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category="primary-support",
            code="primary-support-insufficient",
            message="Artifact says primary support is still insufficient.",
        )
    if field_key == "dynamic materials re-checked on date of writing" and _looks_negative_response(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category="dynamic-material",
            code="dynamic-material-refresh-needed",
            message="Artifact says dynamic materials still need a fresh primary re-check.",
        )
    if field_key == "official raw standard loaded" and _looks_negative_response(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category="standards-consistency",
            code="raw-standard-missing",
            message="Artifact says the relevant raw standard is not loaded yet.",
        )
    if field_key == "normalized profile loaded" and _looks_negative_response(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category="standards-consistency",
            code="normalized-profile-missing",
            message="Artifact says the normalized profile is not loaded yet.",
        )
    if field_key == "conflicts in requirements" and _looks_blocking_text(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category="standards-consistency",
            code="requirements-conflict",
            message="Artifact reports unresolved conflicts in publication requirements.",
        )

    if field_key in {
        "unsafe or overstated claims",
        "inferential gaps",
        "checklist blockers",
        "formatting blockers",
        "what still blocks formal submission",
    } and _looks_blocking_text(clean_value):
        return _build_artifact_blocker(
            source_name,
            field_key,
            clean_value,
            category=_infer_artifact_blocker_category(field_key, clean_value),
            code=_artifact_blocker_code(field_key),
            message=_artifact_blocker_message(field_key),
        )
    return None


def _build_artifact_blocker(
    source_name: str,
    field_key: str,
    field_value: str,
    *,
    category: str,
    code: str,
    message: str,
) -> Blocker:
    return Blocker(
        category=category,
        code=f"artifact-{source_name}-{code}",
        message=message,
        repairable=True,
        blocks_statuses=("submission-ready",),
        details={
            "source": source_name,
            "field": field_key,
            "value": field_value,
        },
    )


def _artifact_blocker_code(field_key: str) -> str:
    mapping = {
        "unsafe or overstated claims": "unsafe-claims",
        "inferential gaps": "inferential-gaps",
        "checklist blockers": "checklist-blockers",
        "formatting blockers": "formatting-blockers",
        "what still blocks formal submission": "formal-submission-blockers",
    }
    return mapping.get(field_key, "artifact-blocker")


def _artifact_blocker_message(field_key: str) -> str:
    mapping = {
        "unsafe or overstated claims": "Artifact reports unsafe or overstated claims that still need repair.",
        "inferential gaps": "Artifact reports unresolved inferential gaps.",
        "checklist blockers": "Artifact reports unresolved checklist blockers.",
        "formatting blockers": "Artifact reports unresolved formatting blockers.",
        "what still blocks formal submission": "Artifact explicitly lists what still blocks formal submission.",
    }
    return mapping.get(field_key, "Artifact reports unresolved blockers.")


def _infer_artifact_blocker_category(field_key: str, field_value: str) -> str:
    text = f"{field_key} {field_value}".casefold()
    if any(token in text for token in ("format", "standard", "profile", "conflict", "оформ", "требован")):
        return "standards-consistency"
    if any(token in text for token in ("citation", "footnote", "атрибуц", "сноск", "bibliograph")):
        return "citation"
    if any(token in text for token in ("dynamic", "re-check", "recheck", "актуаль", "перепровер")):
        return "dynamic-material"
    if any(
        token in text
        for token in ("primary", "source", "evidence", "support", "unsupported", "источ", "доказ", "подтверж", "опор")
    ):
        return "primary-support"
    if any(
        token in text
        for token in ("inferential", "gap", "counterargument", "logic", "overclaim", "аргумент", "контрарг", "вывод")
    ):
        return "logic"
    if field_key == "formatting blockers":
        return "standards-consistency"
    if field_key == "inferential gaps":
        return "logic"
    if field_key == "unsafe or overstated claims":
        return "primary-support"
    return "review"


def _iter_article_artifact_fields(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        match = re.match(r"^\s*(?:[-*]\s+)?([^:\n]+):\s*(.*?)\s*$", raw_line)
        if not match:
            continue
        raw_field_key = _normalize_artifact_field_key(match.group(1))
        field_key = FIELD_ALIAS_INDEX.get(raw_field_key, raw_field_key)
        field_value = match.group(2).strip()
        if not field_key:
            continue
        result.append((field_key, field_value))
    return result


def _merge_article_readiness_statuses(statuses: list[str]) -> str | None:
    resolved: str | None = None
    severity = -1
    for status in statuses:
        current = ARTICLE_STATUS_SEVERITY.get(status)
        if current is None:
            continue
        if current > severity:
            resolved = status
            severity = current
    return resolved


def _dedupe_blockers(blockers: list[Blocker]) -> tuple[Blocker, ...]:
    seen: set[tuple[str, str]] = set()
    deduped: list[Blocker] = []
    for blocker in blockers:
        key = (blocker.category, blocker.code)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(blocker)
    return tuple(deduped)


def _normalize_artifact_field_key(value: str) -> str:
    cleaned = re.sub(r"[`*_]+", "", value).strip().casefold()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _normalize_artifact_value(value: str) -> str:
    cleaned = re.sub(r"[`*_]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _parse_article_status_field(value: str) -> str | None:
    normalized = _normalize_artifact_value(value).casefold().strip(" .")
    if normalized in ARTICLE_STATUS_SEVERITY:
        return normalized
    matches = [status for status in ARTICLE_STATUS_SEVERITY if status in normalized]
    if len(matches) == 1 and any(marker in normalized for marker in ("verdict", "status", "`", ":", "post-repair")):
        return matches[0]
    return None


def _looks_negative_response(value: str) -> bool:
    normalized = _normalize_artifact_value(value).casefold().strip(" .")
    if normalized in {"no", "нет", "false", "insufficient", "incomplete", "missing", "n/a"}:
        return True
    return any(
        token in normalized
        for token in (
            "not loaded",
            "not confirmed",
            "not re-checked",
            "not rechecked",
            "not yet",
            "недостат",
            "не загруж",
            "не подтверж",
            "не перепровер",
            "нет",
        )
    )


def _looks_blocking_text(value: str) -> bool:
    normalized = _normalize_artifact_value(value).casefold().strip(" .")
    if not normalized:
        return False
    if normalized in {"none", "no", "n/a", "ok"}:
        return False
    return normalized not in {"none identified", "no blockers", "нет", "отсутствуют", "не блокирует"}
