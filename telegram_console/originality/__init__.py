"""Novelty / originality checker.

The checker is a **machine gate, not a human-in-the-loop replacement**:
it flags passages whose fingerprint overlaps with an indexed corpus so
the repair loop can ask the agent to rewrite / strengthen the analysis
before ``submission-ready`` is claimed.

Explicitly out of scope (prohibited by ``AGENTS.md``):

- AI-detector bypass;
- mechanical synonym substitution to inflate novelty score;
- feeding external anti-plagiarism APIs a fake draft.
"""

from .checker import OriginalityChecker, OriginalityReport, passage_blockers
from .corpus import CorpusDocument, OriginalityCorpus
from .fingerprint import MinHashFingerprint, fingerprint, shingles

__all__ = [
    "CorpusDocument",
    "MinHashFingerprint",
    "OriginalityChecker",
    "OriginalityCorpus",
    "OriginalityReport",
    "fingerprint",
    "passage_blockers",
    "shingles",
]
