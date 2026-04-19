"""One-shot VKR thesis finalization pipeline.

Runs the full chain of machine-driven gates on an already drafted thesis
manuscript and produces a structured report with an honest status:

- ``submission-ready`` — all gates passed;
- ``strong-draft-with-blockers`` — one or more gates produced blockers;
- ``blocked-runtime`` — pipeline itself failed (I/O, pandoc missing, etc.).

This is **not** a replacement for the Codex-driven workflow. It assumes
that drafting, source verification at the agent level, and bibliography
maintenance have already happened. The goal is to prevent the
orchestrator from claiming submission-ready when deterministic checks
still disagree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .docx_conformance import ConformanceProfile, check_docx
from .gost_linter import lint_bibliography
from .originality.checker import OriginalityChecker, passage_blockers
from .originality.corpus import OriginalityCorpus
from .repair_kernel import Blocker
from .vkr_artifacts import build_bundle, write_bundle
from .work_type import WorkTypeProfile, resolve_profile, validate_structure


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    summary: str
    blockers: tuple[Blocker, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.name,
            "passed": self.passed,
            "summary": self.summary,
            "blockers": [{"category": b.category, "code": b.code, "message": b.message} for b in self.blockers],
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class OneShotReport:
    status: str
    started_at: datetime
    finished_at: datetime
    gates: tuple[GateResult, ...]
    artifacts: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def all_blockers(self) -> list[Blocker]:
        blockers: list[Blocker] = []
        for gate in self.gates:
            blockers.extend(gate.blockers)
        return blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "gates": [gate.to_dict() for gate in self.gates],
            "artifacts": dict(self.artifacts),
            "notes": list(self.notes),
        }

    def to_markdown(self) -> str:
        lines = [
            f"# One-shot VKR report — {self.status}",
            "",
            f"- Started: {self.started_at.isoformat()}",
            f"- Finished: {self.finished_at.isoformat()}",
            "",
            "## Gates",
            "",
        ]
        for gate in self.gates:
            marker = "PASS" if gate.passed else "FAIL"
            lines.append(f"### {marker}: {gate.name}")
            lines.append("")
            lines.append(gate.summary)
            lines.append("")
            if gate.blockers:
                lines.append("Blockers:")
                for b in gate.blockers:
                    lines.append(f"- `{b.category}/{b.code}` — {b.message}")
                lines.append("")
        if self.artifacts:
            lines.append("## Artifacts")
            lines.append("")
            for key, value in self.artifacts.items():
                lines.append(f"- {key}: `{value}`")
            lines.append("")
        if self.notes:
            lines.append("## Notes")
            lines.append("")
            for note in self.notes:
                lines.append(f"- {note}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------


@dataclass
class OneShotConfig:
    manuscript_md: Path
    docx_path: Path | None
    metadata_path: Path | None
    frontmatter_destination: Path | None
    corpus_path: Path | None = None
    originality_threshold: float = 0.35
    conformance_profile: ConformanceProfile = field(default_factory=ConformanceProfile)
    require_docx: bool = True
    work_type: str | None = None


def run_one_shot(config: OneShotConfig) -> OneShotReport:
    started = datetime.now(UTC)
    gates: list[GateResult] = []
    notes: list[str] = []
    artifacts: dict[str, str] = {}

    if config.metadata_path and config.frontmatter_destination:
        gates.append(_gate_frontmatter(config.metadata_path, config.frontmatter_destination))
        artifacts["frontmatter"] = str(config.frontmatter_destination)

    gates.append(_gate_bibliography(config.manuscript_md))
    artifacts["manuscript"] = str(config.manuscript_md)

    profile = resolve_profile(config.work_type)
    if profile is not None:
        gates.append(_gate_work_type_structure(config.manuscript_md, profile))
        artifacts["work_type"] = profile.identifier
        if profile.maximum_originality_similarity:
            if config.originality_threshold > profile.maximum_originality_similarity:
                notes.append(
                    f"originality threshold tightened from {config.originality_threshold} "
                    f"to {profile.maximum_originality_similarity} per work-type profile"
                )
                config.originality_threshold = profile.maximum_originality_similarity
    elif config.work_type:
        notes.append(f"unknown work_type '{config.work_type}' — structural gate skipped")

    if config.docx_path is not None:
        gates.append(
            _gate_docx_conformance(
                config.docx_path,
                profile=config.conformance_profile,
                require=config.require_docx,
            )
        )
        artifacts["docx"] = str(config.docx_path)

    if config.corpus_path is not None:
        gates.append(
            _gate_originality(
                config.manuscript_md,
                corpus_path=config.corpus_path,
                threshold=config.originality_threshold,
            )
        )
    else:
        notes.append("originality gate skipped: corpus_path not configured")

    finished = datetime.now(UTC)
    status = "submission-ready" if all(g.passed for g in gates) else "strong-draft-with-blockers"
    return OneShotReport(
        status=status,
        started_at=started,
        finished_at=finished,
        gates=tuple(gates),
        artifacts=artifacts,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Gate implementations.


def _gate_frontmatter(metadata_path: Path, destination: Path) -> GateResult:
    bundle = build_bundle(metadata_path)
    if bundle.has_blockers:
        return GateResult(
            name="vkr-frontmatter",
            passed=False,
            summary=f"Frontmatter not renderable: {len(bundle.issues)} metadata blocker(s).",
            blockers=tuple(bundle.blockers()),
        )
    written = write_bundle(bundle, destination=destination)
    return GateResult(
        name="vkr-frontmatter",
        passed=True,
        summary=f"Rendered {len(written)} frontmatter artifact(s) into {destination}.",
        details={"files": [str(path) for path in written]},
    )


def _gate_work_type_structure(manuscript_md: Path, profile: WorkTypeProfile) -> GateResult:
    if not manuscript_md.exists():
        return GateResult(
            name=f"work-type[{profile.identifier}]",
            passed=False,
            summary=f"Manuscript missing at {manuscript_md}.",
            blockers=(
                Blocker(
                    category="runtime",
                    code="manuscript-missing",
                    message=f"Manuscript missing: {manuscript_md}",
                    repairable=False,
                ),
            ),
        )
    text = manuscript_md.read_text(encoding="utf-8")
    issues = validate_structure(text, profile)
    if issues:
        return GateResult(
            name=f"work-type[{profile.identifier}]",
            passed=False,
            summary=(f"{profile.title}: {len(issues)} structural blocker(s) against profile minimums."),
            blockers=tuple(issue.to_blocker() for issue in issues),
        )
    return GateResult(
        name=f"work-type[{profile.identifier}]",
        passed=True,
        summary=(
            f"{profile.title}: all required sections present, "
            f"bibliography has at least {profile.minimum_entries} entries."
        ),
    )


def _gate_bibliography(manuscript_md: Path) -> GateResult:
    if not manuscript_md.exists():
        return GateResult(
            name="gost-bibliography",
            passed=False,
            summary=f"Manuscript not found at {manuscript_md}.",
            blockers=(
                Blocker(
                    category="runtime",
                    code="manuscript-missing",
                    message=f"Manuscript missing: {manuscript_md}",
                    repairable=False,
                ),
            ),
        )
    text = manuscript_md.read_text(encoding="utf-8")
    report = lint_bibliography(text)
    if report.has_blockers:
        return GateResult(
            name="gost-bibliography",
            passed=False,
            summary=(f"GOST linter found {len(report.issues)} issue(s) across {len(report.entries)} entries."),
            blockers=tuple(report.blockers()),
        )
    return GateResult(
        name="gost-bibliography",
        passed=True,
        summary=f"GOST linter: {len(report.entries)} entries, no structural issues.",
    )


def _gate_docx_conformance(
    docx_path: Path,
    *,
    profile: ConformanceProfile,
    require: bool,
) -> GateResult:
    if not docx_path.exists():
        if require:
            return GateResult(
                name="docx-conformance",
                passed=False,
                summary=f"DOCX missing at {docx_path}; run export before one-shot.",
                blockers=(
                    Blocker(
                        category="runtime",
                        code="docx-missing",
                        message=f"DOCX not found: {docx_path}",
                        repairable=False,
                    ),
                ),
            )
        return GateResult(
            name="docx-conformance",
            passed=True,
            summary="DOCX gate skipped (not required).",
        )
    report = check_docx(docx_path, profile)
    if report.has_blockers:
        return GateResult(
            name="docx-conformance",
            passed=False,
            summary=f"DOCX conformance: {len(report.issues)} issue(s).",
            blockers=tuple(report.blockers()),
        )
    return GateResult(
        name="docx-conformance",
        passed=True,
        summary="DOCX matches the configured conformance profile.",
    )


def _gate_originality(
    manuscript_md: Path,
    *,
    corpus_path: Path,
    threshold: float,
) -> GateResult:
    try:
        corpus = OriginalityCorpus.load(corpus_path)
    except (OSError, ValueError) as exc:
        return GateResult(
            name="originality",
            passed=False,
            summary=f"Failed to load originality corpus: {exc}",
            blockers=(
                Blocker(
                    category="runtime",
                    code="originality-corpus-unreadable",
                    message=str(exc),
                    repairable=False,
                ),
            ),
        )
    text = manuscript_md.read_text(encoding="utf-8")
    checker = OriginalityChecker(corpus, threshold=threshold)
    report = checker.check_passage(passage_id="manuscript", text=text)
    if report.is_blocking:
        return GateResult(
            name="originality",
            passed=False,
            summary=(
                f"Originality checker: similarity {report.similarity:.2f} reaches or exceeds threshold {threshold:.2f}."
            ),
            blockers=tuple(passage_blockers([report])),
        )
    return GateResult(
        name="originality",
        passed=True,
        summary=(f"Originality checker: all passages below similarity threshold {threshold}."),
    )


def write_report(report: OneShotReport, *, markdown_path: Path, json_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
