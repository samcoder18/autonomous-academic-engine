from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from academic_engine.originality import (
    OriginalityChecker,
    OriginalityCorpus,
    fingerprint,
    passage_blockers,
    shingles,
)

_BIOMETRICS_TEXT = (
    "Биометрическая идентификация в цифровом банкинге опирается на Федеральный закон 572-ФЗ "
    "и положения Банка России о единой биометрической системе. "
    "Оператор ЕБС отвечает за корректность обработки персональных данных."
)

_OVERLAPPING_TEXT = (
    "Биометрическая идентификация в цифровом банкинге опирается на Федеральный закон 572-ФЗ "
    "и положения Банка России о единой биометрической системе. "
    "Это означает, что требуется повышенная защита."
)

_ORIGINAL_TEXT = (
    "Совершенно иной подход предлагает автор: рассматривать биометрические системы через призму "
    "инфраструктурной модели с акцентом на аудит и операционную устойчивость. Сравнение с банковским "
    "аутсорсингом показывает принципиальные различия в распределении ответственности."
)


class FingerprintTests(unittest.TestCase):
    def test_shingles_respects_window(self) -> None:
        self.assertEqual(
            shingles("один два три четыре пять", window=3),
            ["один два три", "два три четыре", "три четыре пять"],
        )

    def test_empty_when_text_too_short(self) -> None:
        self.assertEqual(shingles("слишком мало", window=8), [])
        fp = fingerprint("слишком мало", window=8)
        self.assertEqual(fp.signature, ())
        self.assertEqual(fp.token_count, 2)

    def test_identical_fingerprints_have_full_jaccard(self) -> None:
        a = fingerprint(_BIOMETRICS_TEXT)
        b = fingerprint(_BIOMETRICS_TEXT)
        self.assertEqual(a.signature, b.signature)
        self.assertAlmostEqual(a.jaccard(b), 1.0)

    def test_similar_text_has_high_jaccard(self) -> None:
        a = fingerprint(_BIOMETRICS_TEXT)
        b = fingerprint(_OVERLAPPING_TEXT)
        self.assertGreater(a.jaccard(b), 0.3)

    def test_distinct_text_has_low_jaccard(self) -> None:
        a = fingerprint(_BIOMETRICS_TEXT)
        b = fingerprint(_ORIGINAL_TEXT)
        self.assertLess(a.jaccard(b), 0.2)


class CorpusTests(unittest.TestCase):
    def test_compare_sorts_by_similarity(self) -> None:
        corpus = OriginalityCorpus()
        corpus.add_document(identifier="a", title="Existing ВКР", text=_BIOMETRICS_TEXT)
        corpus.add_document(identifier="b", title="Unrelated monograph", text=_ORIGINAL_TEXT)
        fp = fingerprint(_OVERLAPPING_TEXT)
        ranked = corpus.compare(fp)
        self.assertEqual(ranked[0][0].identifier, "a")
        self.assertGreater(ranked[0][1], ranked[1][1])

    def test_save_load_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "corpus.json"
            corpus = OriginalityCorpus()
            corpus.add_document(identifier="a", title="X", text=_BIOMETRICS_TEXT)
            corpus.save(path)
            restored = OriginalityCorpus.load(path)
            self.assertEqual(len(restored), 1)
            docs = list(restored.documents())
            self.assertEqual(docs[0].identifier, "a")


class CheckerTests(unittest.TestCase):
    def _corpus(self) -> OriginalityCorpus:
        corpus = OriginalityCorpus()
        corpus.add_document(identifier="existing-vkr", title="Existing ВКР", text=_BIOMETRICS_TEXT)
        return corpus

    def test_blocks_on_high_similarity(self) -> None:
        checker = OriginalityChecker(self._corpus(), threshold=0.15)
        report = checker.check_passage(passage_id="chapter-1", text=_OVERLAPPING_TEXT)
        self.assertTrue(report.is_blocking)
        blockers = passage_blockers([report])
        self.assertEqual(len(blockers), 1)
        self.assertEqual(blockers[0].category, "originality")
        self.assertIn("chapter-1", blockers[0].code)

    def test_passes_on_original_text(self) -> None:
        checker = OriginalityChecker(self._corpus(), threshold=0.35)
        report = checker.check_passage(passage_id="chapter-2", text=_ORIGINAL_TEXT)
        self.assertFalse(report.is_blocking)
        self.assertEqual(passage_blockers([report]), [])

    def test_threshold_validation(self) -> None:
        with self.assertRaises(ValueError):
            OriginalityChecker(self._corpus(), threshold=0)
        with self.assertRaises(ValueError):
            OriginalityChecker(self._corpus(), threshold=1.2)


if __name__ == "__main__":
    unittest.main()
