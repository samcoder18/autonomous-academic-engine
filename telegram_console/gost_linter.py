"""GOST R 7.0.100-2018 bibliography linter.

Lightweight, pure-text checker. Operates on the "Список использованных
источников" section of a Markdown manuscript. Goals:

- one entry per line (numbered list);
- required punctuation: `/` before authors, `.` between blocks, `—`
  between title/imprint/series in the canonical layout;
- detect duplicate entries by normalised canonical URL;
- surface malformed entries as ``gost-bibliography`` blockers for the
  repair kernel.

The checker is intentionally **conservative**: it emits blockers only
for structural issues it can reliably identify, not for every deviation.
The goal is to catch regressions, not to replicate a human librarian.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .repair_kernel import Blocker

_BIB_HEADERS = (
    "список использованных источников",
    "список литературы",
    "references",
)

_ITEM_PATTERN = re.compile(r"^\s*(\d+)[.)]\s+(?P<body>.+?)\s*$")
_URL_PATTERN = re.compile(r"(https?://\S+|ftp://\S+)", re.IGNORECASE)

_LEGAL_ACT_MARKERS = (
    "федеральный закон",
    "конституция",
    "кодекс",
    "постановление",
    "распоряжение",
    "приказ",
    "положение",
    "указ",
    "определение конституционного",
    "определение верховного",
    "решение верховного",
    "указание банка россии",
)


def _looks_like_legal_act(text: str) -> bool:
    lowered = text.casefold()
    return any(marker in lowered for marker in _LEGAL_ACT_MARKERS)


@dataclass(frozen=True)
class BibliographyEntry:
    index: int
    raw: str
    line_number: int

    @property
    def normalised(self) -> str:
        return re.sub(r"\s+", " ", self.raw).strip()

    @property
    def canonical_url(self) -> str | None:
        match = _URL_PATTERN.search(self.raw)
        if not match:
            return None
        return match.group(0).rstrip(").,;")


@dataclass(frozen=True)
class BibliographyIssue:
    entry_index: int
    line_number: int
    code: str
    message: str

    def to_blocker(self) -> Blocker:
        return Blocker(
            category="gost-bibliography",
            code=self.code,
            message=self.message,
            repairable=True,
            blocks_statuses=("submission-ready",),
            details={"entry_index": self.entry_index, "line_number": self.line_number},
        )


@dataclass(frozen=True)
class LinterReport:
    entries: tuple[BibliographyEntry, ...]
    issues: tuple[BibliographyIssue, ...]

    @property
    def has_blockers(self) -> bool:
        return bool(self.issues)

    def blockers(self) -> list[Blocker]:
        return [issue.to_blocker() for issue in self.issues]

    def to_dict(self) -> dict[str, object]:
        return {
            "entry_count": len(self.entries),
            "issues": [
                {
                    "entry_index": issue.entry_index,
                    "line_number": issue.line_number,
                    "code": issue.code,
                    "message": issue.message,
                }
                for issue in self.issues
            ],
        }


def extract_bibliography_entries(markdown: str) -> list[BibliographyEntry]:
    """Return every numbered entry under a bibliography-section header."""
    lines = markdown.splitlines()
    in_section = False
    entries: list[BibliographyEntry] = []
    for line_number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith("#"):
            in_section = stripped.lstrip("# ").casefold() in _BIB_HEADERS
            continue
        if not in_section:
            continue
        match = _ITEM_PATTERN.match(raw)
        if match:
            entries.append(
                BibliographyEntry(
                    index=int(match.group(1)),
                    raw=match.group("body"),
                    line_number=line_number,
                )
            )
    return entries


def lint_bibliography(markdown: str) -> LinterReport:
    entries = extract_bibliography_entries(markdown)
    issues: list[BibliographyIssue] = []
    seen_urls: dict[str, int] = {}
    seen_hashes: dict[str, int] = {}

    for entry in entries:
        text = entry.normalised
        if len(text) < 20:
            issues.append(
                BibliographyIssue(
                    entry.index,
                    entry.line_number,
                    code="too-short",
                    message=f"Entry #{entry.index} is shorter than 20 characters; likely truncated.",
                )
            )
        if not text.endswith((".", "…")):
            issues.append(
                BibliographyIssue(
                    entry.index,
                    entry.line_number,
                    code="missing-terminal-period",
                    message=f"Entry #{entry.index} must end with a period per ГОСТ Р 7.0.100-2018.",
                )
            )
        if " / " not in text and not _looks_like_legal_act(text):
            issues.append(
                BibliographyIssue(
                    entry.index,
                    entry.line_number,
                    code="missing-responsibility-slash",
                    message=(
                        f"Entry #{entry.index} should contain ` / ` before responsibility statement "
                        "for scholarly works."
                    ),
                )
            )
        url = entry.canonical_url
        if url:
            previous = seen_urls.get(url)
            if previous is not None:
                issues.append(
                    BibliographyIssue(
                        entry.index,
                        entry.line_number,
                        code="duplicate-canonical-url",
                        message=(f"Entry #{entry.index} duplicates canonical URL of entry #{previous} ({url})."),
                    )
                )
            else:
                seen_urls[url] = entry.index
        digest = hashlib.sha1(text.casefold().encode("utf-8")).hexdigest()
        if digest in seen_hashes:
            issues.append(
                BibliographyIssue(
                    entry.index,
                    entry.line_number,
                    code="duplicate-entry",
                    message=(
                        f"Entry #{entry.index} is a near-duplicate of entry #{seen_hashes[digest]} (identical text)."
                    ),
                )
            )
        else:
            seen_hashes[digest] = entry.index

    return LinterReport(entries=tuple(entries), issues=tuple(issues))


def lint_to_blockers(markdown: str) -> list[Blocker]:
    report = lint_bibliography(markdown)
    return report.blockers()
