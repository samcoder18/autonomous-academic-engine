"""Originality checker.

Given a passage and a corpus, compute:

- overall MinHash similarity with the closest corpus document;
- per-passage report with the top-N matches for transparency.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ..repair_kernel import Blocker
from .corpus import OriginalityCorpus
from .fingerprint import MinHashFingerprint, fingerprint


@dataclass(frozen=True)
class PassageMatch:
    document_identifier: str
    document_title: str
    similarity: float


@dataclass(frozen=True)
class OriginalityReport:
    passage_id: str
    similarity: float
    matches: tuple[PassageMatch, ...]
    threshold: float
    fingerprint: MinHashFingerprint

    @property
    def is_blocking(self) -> bool:
        return self.similarity >= self.threshold

    def to_dict(self) -> dict[str, object]:
        return {
            "passage_id": self.passage_id,
            "similarity": round(self.similarity, 4),
            "threshold": self.threshold,
            "matches": [
                {
                    "document_identifier": match.document_identifier,
                    "document_title": match.document_title,
                    "similarity": round(match.similarity, 4),
                }
                for match in self.matches
            ],
            "fingerprint": self.fingerprint.to_dict(),
        }


class OriginalityChecker:
    """Stateless wrapper over a corpus with a configurable threshold."""

    def __init__(
        self,
        corpus: OriginalityCorpus,
        *,
        threshold: float = 0.15,
        window: int = 8,
        permutations: int = 64,
        top_n: int = 3,
    ) -> None:
        if not (0.0 < threshold <= 1.0):
            raise ValueError("threshold must be in (0, 1]")
        self._corpus = corpus
        self._threshold = threshold
        self._window = window
        self._permutations = permutations
        self._top_n = top_n

    @property
    def threshold(self) -> float:
        return self._threshold

    def check_passage(self, *, passage_id: str, text: str) -> OriginalityReport:
        fp = fingerprint(text, window=self._window, permutations=self._permutations)
        matches = self._corpus.compare(fp)[: self._top_n]
        best_similarity = matches[0][1] if matches else 0.0
        return OriginalityReport(
            passage_id=passage_id,
            similarity=best_similarity,
            matches=tuple(
                PassageMatch(
                    document_identifier=doc.identifier,
                    document_title=doc.title,
                    similarity=similarity,
                )
                for doc, similarity in matches
            ),
            threshold=self._threshold,
            fingerprint=fp,
        )


def passage_blockers(reports: Iterable[OriginalityReport]) -> list[Blocker]:
    """Convert blocking reports into ``originality`` blockers for repair_kernel."""
    out: list[Blocker] = []
    for report in reports:
        if not report.is_blocking:
            continue
        out.append(
            Blocker(
                category="originality",
                code=f"high-similarity-{report.passage_id}",
                message=(
                    f"Passage {report.passage_id!r} similarity {report.similarity:.2f} "
                    f"reaches or exceeds threshold {report.threshold:.2f}. "
                    "Rewrite via deeper analysis, sharper citation, or shorter quotation."
                ),
                repairable=True,
                blocks_statuses=("submission-ready",),
                details=report.to_dict(),
            )
        )
    return out
