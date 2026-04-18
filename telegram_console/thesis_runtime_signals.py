from __future__ import annotations

from dataclasses import dataclass
import re

from .repair_kernel import Blocker


THESIS_STATUS_HINTS = {
    "updated",
    "reviewed",
    "ready-with-caveats",
    "blocked-primary-support",
    "blocked-runtime",
}

FIELD_ALIASES = {
    "status": {
        "status",
        "terminal status",
        "result",
    },
    "unsupported-claims": {
        "есть ли утверждения без опоры",
    },
    "contested-conclusions": {
        "есть ли спорные выводы",
        "что нужно переписать",
    },
    "dynamic-recheck": {
        "все ли динамичные нормы и решения перепроверены на дату написания",
    },
    "primary-support": {
        "есть ли у ключевых утверждений первичная, а не только вторичная опора",
        "что нужно дополнить источниками",
    },
    "citation-consistency": {
        "единообразно ли оформлены ссылки",
    },
}

FIELD_ALIAS_INDEX = {
    alias: canonical
    for canonical, aliases in FIELD_ALIASES.items()
    for alias in aliases
}

GUARDED_PROSE_RULES = (
    {
        "category": "primary-support",
        "code": "guarded-prose-primary-support",
        "message": "Review prose says the section still needs stronger primary support.",
        "patterns": (
            r"\bнужн\w*\s+первич\w*\s+опор",
            r"\bнет\s+первич\w*\s+опор",
            r"\bнедостат\w*.*первич\w*\s+опор",
            r"\bmissing\s+primary\s+support\b",
        ),
    },
    {
        "category": "dynamic-material",
        "code": "guarded-prose-dynamic-material",
        "message": "Review prose says dynamic legal material still needs a fresh re-check.",
        "patterns": (
            r"\bнужн\w*.*перепровер\w*.*динамич",
            r"\bперепровер\w*.*на дату написания",
            r"\bneeds?\s+.*re-?check.*dynamic\b",
        ),
    },
    {
        "category": "review",
        "code": "guarded-prose-review",
        "message": "Review prose says the section still contains contested or weak conclusions.",
        "patterns": (
            r"\bспорн\w*\s+вывод",
            r"\bслаб\w*\s+вывод",
            r"\bcontested\s+conclusion\b",
        ),
    },
)


@dataclass(frozen=True)
class ThesisRuntimeSignals:
    status_hint: str | None
    blockers: tuple[Blocker, ...]


def extract_thesis_runtime_signals(artifact_texts: dict[str, str]) -> ThesisRuntimeSignals:
    status_hint = _extract_status_hint(artifact_texts.get("output", ""))
    blockers: list[Blocker] = []
    for source_name, text in artifact_texts.items():
        blockers.extend(_extract_source_blockers(source_name, text))

    if status_hint == "blocked-primary-support" and not any(item.category == "primary-support" for item in blockers):
        blockers.append(
            _build_blocker(
                "output",
                "status",
                status_hint,
                category="primary-support",
                code="status-blocked-primary-support",
                message="Output explicitly reports blocked primary support.",
            )
        )
    if status_hint == "blocked-runtime" and not any(item.category == "runtime" for item in blockers):
        blockers.append(
            _build_blocker(
                "output",
                "status",
                status_hint,
                category="runtime",
                code="status-blocked-runtime",
                message="Output explicitly reports a runtime blocker.",
                repairable=False,
            )
        )

    return ThesisRuntimeSignals(status_hint=status_hint, blockers=_dedupe_blockers(blockers))


def _extract_status_hint(text: str) -> str | None:
    for field_key, field_value in _iter_fields(text):
        if field_key != "status":
            continue
        status = _parse_status(field_value)
        if status:
            return status
    for line in text.splitlines():
        status = _parse_status(line)
        if status:
            return status
    return None


def _extract_source_blockers(source_name: str, text: str) -> list[Blocker]:
    blockers: list[Blocker] = []
    for field_key, field_value in _iter_fields(text):
        clean_value = _normalize_value(field_value)
        if not clean_value or field_key == "status":
            continue
        blocker = _blocker_from_field(source_name, field_key, clean_value)
        if blocker is not None:
            blockers.append(blocker)
    blockers.extend(_extract_guarded_prose_blockers(source_name, text))
    return blockers


def _blocker_from_field(source_name: str, field_key: str, field_value: str) -> Blocker | None:
    if field_key == "unsupported-claims" and _looks_issue_present(field_value):
        return _build_blocker(
            source_name,
            field_key,
            field_value,
            category="primary-support",
            code="unsupported-claims",
            message="Review says the section still contains unsupported claims.",
        )
    if field_key == "contested-conclusions" and _looks_blocking_text(field_value):
        return _build_blocker(
            source_name,
            field_key,
            field_value,
            category="review",
            code="contested-conclusions",
            message="Review says the section still contains disputed or weak conclusions.",
        )
    if field_key == "dynamic-recheck" and _looks_negative_confirmation(field_value):
        return _build_blocker(
            source_name,
            field_key,
            field_value,
            category="dynamic-material",
            code="dynamic-material-not-refreshed",
            message="Review says dynamic legal material still needs a fresh re-check.",
        )
    if field_key == "primary-support" and _looks_primary_support_gap(field_value):
        return _build_blocker(
            source_name,
            field_key,
            field_value,
            category="primary-support",
            code="primary-support-gap",
            message="Review says the section still needs stronger primary support.",
        )
    if field_key == "citation-consistency" and _looks_negative_confirmation(field_value):
        return _build_blocker(
            source_name,
            field_key,
            field_value,
            category="citation",
            code="citation-consistency-gap",
            message="Review says citation formatting is not consistent yet.",
        )
    return None


def _build_blocker(
    source_name: str,
    field_key: str,
    field_value: str,
    *,
    category: str,
    code: str,
    message: str,
    repairable: bool = True,
) -> Blocker:
    return Blocker(
        category=category,
        code=f"thesis-{source_name}-{code}",
        message=message,
        repairable=repairable,
        details={
            "source": source_name,
            "field": field_key,
            "value": field_value,
        },
    )


def _extract_guarded_prose_blockers(source_name: str, text: str) -> list[Blocker]:
    blockers: list[Blocker] = []
    for raw_line in text.splitlines():
        line = _normalize_value(re.sub(r"^\s*(?:[-*]\s+)?", "", raw_line))
        if not line or ":" in line:
            continue
        blocker = _guarded_prose_blocker(source_name, line)
        if blocker is not None:
            blockers.append(blocker)
    return blockers


def _guarded_prose_blocker(source_name: str, line: str) -> Blocker | None:
    normalized = line.casefold().strip(" .")
    if not normalized:
        return None
    for rule in GUARDED_PROSE_RULES:
        if not any(re.search(pattern, normalized) for pattern in rule["patterns"]):
            continue
        return _build_blocker(
            source_name,
            "guarded-prose",
            line,
            category=str(rule["category"]),
            code=str(rule["code"]),
            message=str(rule["message"]),
        )
    return None


def _iter_fields(text: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    for raw_line in text.splitlines():
        match = re.match(r"^\s*(?:[-*]\s+)?([^:\n]+):\s*(.*?)\s*$", raw_line)
        if not match:
            continue
        raw_field = _normalize_key(match.group(1))
        field_key = FIELD_ALIAS_INDEX.get(raw_field, raw_field)
        field_value = match.group(2).strip()
        result.append((field_key, field_value))
    return result


def _parse_status(value: str) -> str | None:
    normalized = _normalize_value(value).casefold().strip(" .")
    if normalized in THESIS_STATUS_HINTS:
        return normalized
    matches = [status for status in THESIS_STATUS_HINTS if status in normalized]
    if len(matches) == 1 and any(marker in normalized for marker in ("status", "result", "`", ":", "blocked", "ready")):
        return matches[0]
    return None


def _normalize_key(value: str) -> str:
    cleaned = re.sub(r"[`*_]+", "", value).strip().casefold()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _normalize_value(value: str) -> str:
    cleaned = re.sub(r"[`*_]+", "", value).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _looks_issue_present(value: str) -> bool:
    normalized = _normalize_value(value).casefold().strip(" .")
    return normalized.startswith(("да", "yes")) or _looks_blocking_text(normalized)


def _looks_negative_confirmation(value: str) -> bool:
    normalized = _normalize_value(value).casefold().strip(" .")
    if normalized in {"нет", "no", "false"}:
        return True
    return any(token in normalized for token in ("не ", "неполн", "недостат"))


def _looks_primary_support_gap(value: str) -> bool:
    normalized = _normalize_value(value).casefold().strip(" .")
    if normalized in {"нет", "no", "false", "none", "n/a", "отсутствуют", "не требуется"}:
        return False
    return _looks_blocking_text(normalized)


def _looks_blocking_text(value: str) -> bool:
    normalized = _normalize_value(value).casefold().strip(" .")
    if not normalized:
        return False
    return normalized not in {"нет", "no", "none", "n/a", "отсутствуют", "не требуется"}


def _dedupe_blockers(blockers: list[Blocker]) -> tuple[Blocker, ...]:
    seen: set[str] = set()
    deduped: list[Blocker] = []
    for blocker in blockers:
        if blocker.category in seen:
            continue
        seen.add(blocker.category)
        deduped.append(blocker)
    return tuple(deduped)
