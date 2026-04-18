from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence
import re

from .repair_kernel import Blocker


@dataclass(frozen=True)
class GuardedProseRule:
    category: str
    code: str
    message: str
    field: str = "guarded-prose"
    required_markers: tuple[str, ...] = ()
    keyword_markers: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()


def extract_guarded_prose_blockers(
    source_name: str,
    text: str,
    *,
    normalize_line: Callable[[str], str],
    rules: Sequence[GuardedProseRule],
    build_blocker: Callable[..., Blocker],
    skip_line: Callable[[str], bool] | None = None,
) -> list[Blocker]:
    blockers: list[Blocker] = []
    for raw_line in text.splitlines():
        line = normalize_line(re.sub(r"^\s*(?:[-*]\s+)?", "", raw_line))
        if not line or ":" in line:
            continue
        normalized = line.casefold().strip(" .")
        if not normalized:
            continue
        if skip_line is not None and skip_line(normalized):
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
    if rule.required_markers and not any(marker in normalized for marker in rule.required_markers):
        return False
    if rule.keyword_markers and not any(marker in normalized for marker in rule.keyword_markers):
        return False
    if rule.regex_patterns and not any(re.search(pattern, normalized) for pattern in rule.regex_patterns):
        return False
    return bool(rule.required_markers or rule.keyword_markers or rule.regex_patterns)
