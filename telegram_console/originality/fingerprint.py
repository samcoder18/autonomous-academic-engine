"""Shingling + MinHash fingerprint.

Pure stdlib; the number of hash permutations is tunable (default 64 —
enough for ВКР-scale pages without exploding memory).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_WORD_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+", re.UNICODE)


def _tokens(text: str) -> list[str]:
    return [match.group(0).casefold() for match in _WORD_RE.finditer(text)]


def shingles(text: str, *, window: int = 8) -> list[str]:
    """Return word-level shingles; empty when there aren't enough tokens."""
    if window <= 0:
        raise ValueError("window must be positive")
    tokens = _tokens(text)
    if len(tokens) < window:
        return []
    return [" ".join(tokens[idx : idx + window]) for idx in range(len(tokens) - window + 1)]


def _hashes(shingle: str, *, permutations: int) -> list[int]:
    base = hashlib.blake2b(shingle.encode("utf-8"), digest_size=16).digest()
    result: list[int] = []
    for index in range(permutations):
        salt = index.to_bytes(4, "big")
        mixed = hashlib.blake2b(base + salt, digest_size=8).digest()
        result.append(int.from_bytes(mixed, "big"))
    return result


@dataclass(frozen=True)
class MinHashFingerprint:
    signature: tuple[int, ...]
    permutations: int
    window: int
    token_count: int

    def jaccard(self, other: MinHashFingerprint) -> float:
        if self.permutations != other.permutations:
            raise ValueError("cannot compare fingerprints with different permutation counts")
        if not self.signature or not other.signature:
            return 0.0
        matches = sum(1 for a, b in zip(self.signature, other.signature, strict=True) if a == b)
        return matches / self.permutations

    def to_dict(self) -> dict[str, object]:
        return {
            "signature": list(self.signature),
            "permutations": self.permutations,
            "window": self.window,
            "token_count": self.token_count,
        }


def fingerprint(text: str, *, window: int = 8, permutations: int = 64) -> MinHashFingerprint:
    """Compute a MinHash fingerprint for ``text``."""
    token_count = len(_tokens(text))
    sigs_table = shingles(text, window=window)
    if not sigs_table:
        return MinHashFingerprint(
            signature=(),
            permutations=permutations,
            window=window,
            token_count=token_count,
        )
    minima = [float("inf")] * permutations
    for shingle in sigs_table:
        hashes = _hashes(shingle, permutations=permutations)
        for idx, value in enumerate(hashes):
            if value < minima[idx]:
                minima[idx] = value
    return MinHashFingerprint(
        signature=tuple(int(value) for value in minima),
        permutations=permutations,
        window=window,
        token_count=token_count,
    )
