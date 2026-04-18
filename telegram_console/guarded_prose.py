from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence
import re
import tomllib

from .repair_kernel import Blocker


RULES_FILE = Path(__file__).with_name("guarded_prose_rules.toml")


@dataclass(frozen=True)
class GuardedProseRule:
    category: str
    code: str
    message: str
    field: str = "guarded-prose"
    required_markers: tuple[str, ...] = ()
    keyword_markers: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()
    forbidden_markers: tuple[str, ...] = ()


@lru_cache(maxsize=4)
def load_guarded_prose_rules(lane: str) -> tuple[GuardedProseRule, ...]:
    payload = _read_rules_payload()
    items = payload.get("rules")
    if not isinstance(items, list):
        return ()
    selected: list[GuardedProseRule] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_lane = str(item.get("lane") or "").strip().lower()
        if item_lane != lane.strip().lower():
            continue
        selected.append(_rule_from_payload(item))
    return tuple(selected)


def extract_guarded_prose_blockers(
    source_name: str,
    text: str,
    *,
    normalize_line: Callable[[str], str],
    rules: Sequence[GuardedProseRule],
    build_blocker: Callable[..., Blocker],
) -> list[Blocker]:
    blockers: list[Blocker] = []
    for raw_line in text.splitlines():
        line = normalize_line(re.sub(r"^\s*(?:[-*]\s+)?", "", raw_line))
        if not line or ":" in line:
            continue
        normalized = line.casefold().strip(" .")
        if not normalized:
            continue
        for rule in rules:
            if not _matches_rule(rule, normalized):
                continue
            blockers.append(
                build_blocker(
                    source_name,
                    rule.field,
                    line,
                    category=rule.category,
                    code=rule.code,
                    message=rule.message,
                )
            )
            break
    return blockers


def _matches_rule(rule: GuardedProseRule, normalized: str) -> bool:
    if rule.forbidden_markers and any(marker in normalized for marker in rule.forbidden_markers):
        return False
    if rule.required_markers and not any(marker in normalized for marker in rule.required_markers):
        return False
    if rule.keyword_markers and not any(marker in normalized for marker in rule.keyword_markers):
        return False
    if rule.regex_patterns and not any(re.search(pattern, normalized) for pattern in rule.regex_patterns):
        return False
    return bool(rule.required_markers or rule.keyword_markers or rule.regex_patterns)


def _read_rules_payload() -> dict[str, Any]:
    with RULES_FILE.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload if isinstance(payload, dict) else {}


def _rule_from_payload(payload: dict[str, Any]) -> GuardedProseRule:
    return GuardedProseRule(
        category=str(payload.get("category") or "").strip(),
        code=str(payload.get("code") or "").strip(),
        message=str(payload.get("message") or "").strip(),
        field=str(payload.get("field") or "guarded-prose").strip() or "guarded-prose",
        required_markers=_tuple_of_text(payload.get("required_markers")),
        keyword_markers=_tuple_of_text(payload.get("keyword_markers")),
        regex_patterns=_tuple_of_text(payload.get("regex_patterns")),
        forbidden_markers=_tuple_of_text(payload.get("forbidden_markers")),
    )


def _tuple_of_text(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items: list[str] = []
    for raw in value:
        text = str(raw).strip()
        if text:
            items.append(text)
    return tuple(items)
