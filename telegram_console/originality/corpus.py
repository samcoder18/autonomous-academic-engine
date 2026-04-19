"""Originality corpus — a local index of comparable works."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .fingerprint import MinHashFingerprint, fingerprint


@dataclass(frozen=True)
class CorpusDocument:
    identifier: str
    title: str
    year: int | None
    fingerprint: MinHashFingerprint
    canonical_url: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "title": self.title,
            "year": self.year,
            "canonical_url": self.canonical_url,
            "fingerprint": self.fingerprint.to_dict(),
        }


class OriginalityCorpus:
    """In-memory + JSON-file-backed fingerprint index."""

    def __init__(self) -> None:
        self._documents: dict[str, CorpusDocument] = {}

    def add_document(
        self,
        *,
        identifier: str,
        title: str,
        text: str,
        year: int | None = None,
        canonical_url: str = "",
        window: int = 8,
        permutations: int = 64,
    ) -> CorpusDocument:
        doc = CorpusDocument(
            identifier=identifier,
            title=title,
            year=year,
            fingerprint=fingerprint(text, window=window, permutations=permutations),
            canonical_url=canonical_url,
        )
        self._documents[identifier] = doc
        return doc

    def add_raw(self, doc: CorpusDocument) -> None:
        self._documents[doc.identifier] = doc

    def __len__(self) -> int:
        return len(self._documents)

    def documents(self) -> Iterable[CorpusDocument]:
        return list(self._documents.values())

    def compare(self, passage_fingerprint: MinHashFingerprint) -> list[tuple[CorpusDocument, float]]:
        results: list[tuple[CorpusDocument, float]] = []
        for doc in self._documents.values():
            if doc.fingerprint.permutations != passage_fingerprint.permutations:
                continue
            similarity = doc.fingerprint.jaccard(passage_fingerprint)
            results.append((doc, similarity))
        results.sort(key=lambda item: item[1], reverse=True)
        return results

    # ------------------------------------------------------------------
    # Serialisation.

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"documents": [doc.to_dict() for doc in self._documents.values()]}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> OriginalityCorpus:
        corpus = cls()
        if not path.exists():
            return corpus
        payload = json.loads(path.read_text(encoding="utf-8"))
        for raw in payload.get("documents", []):
            fp_raw = raw["fingerprint"]
            fp = MinHashFingerprint(
                signature=tuple(int(value) for value in fp_raw["signature"]),
                permutations=int(fp_raw["permutations"]),
                window=int(fp_raw["window"]),
                token_count=int(fp_raw["token_count"]),
            )
            corpus.add_raw(
                CorpusDocument(
                    identifier=str(raw["identifier"]),
                    title=str(raw["title"]),
                    year=raw.get("year"),
                    canonical_url=str(raw.get("canonical_url", "")),
                    fingerprint=fp,
                )
            )
        return corpus

    def digest(self) -> str:
        payload = json.dumps(
            sorted(doc.identifier for doc in self._documents.values()),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
