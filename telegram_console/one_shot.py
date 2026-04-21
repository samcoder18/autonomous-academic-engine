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

from .dissertation_artifacts import build_bundle as build_dissertation_bundle
from .dissertation_artifacts import write_bundle as write_dissertation_bundle
from .docx_conformance import ConformanceProfile, check_docx
from .gost_linter import lint_bibliography
from .originality.checker import OriginalityChecker, passage_blockers
from .originality.corpus import OriginalityCorpus
from .repair_kernel import Blocker
from .thesis_runtime_signals import extract_thesis_runtime_signals
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
            f"# One-shot thesis report — {self.status}",
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
    dissertation_metadata_path: Path | None = None
    dissertation_artifacts_destination: Path | None = None
    dissertation_root: Path | None = None
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

    profile = resolve_profile(config.work_type)
    is_dissertation = bool(profile and profile.artifact_family == "dissertation")

    if is_dissertation:
        if config.dissertation_metadata_path and config.dissertation_artifacts_destination:
            gates.append(
                _gate_dissertation_artifacts(
                    config.dissertation_metadata_path,
                    config.dissertation_artifacts_destination,
                )
            )
            artifacts["dissertation_artifacts"] = str(config.dissertation_artifacts_destination)
        if config.dissertation_root:
            gates.extend(_dissertation_contour_gates(config.dissertation_root, profile))
            artifacts["dissertation_root"] = str(config.dissertation_root)
    elif config.metadata_path and config.frontmatter_destination:
        gates.append(_gate_frontmatter(config.metadata_path, config.frontmatter_destination))
        artifacts["frontmatter"] = str(config.frontmatter_destination)

    gates.append(_gate_bibliography(config.manuscript_md))
    artifacts["manuscript"] = str(config.manuscript_md)
    thesis_quality_gate = _gate_thesis_quality_contract(config.manuscript_md)
    if thesis_quality_gate is not None:
        gates.append(thesis_quality_gate)

    if profile is not None:
        gates.append(_gate_work_type_structure(config.manuscript_md, profile))
        artifacts["work_type"] = profile.identifier
        if profile.min_chars or profile.max_chars:
            gates.append(_gate_length_conformance(config.manuscript_md, profile))
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


def _gate_dissertation_artifacts(metadata_path: Path, destination: Path) -> GateResult:
    bundle = build_dissertation_bundle(metadata_path)
    if bundle.has_blockers:
        return GateResult(
            name="dissertation-artifacts",
            passed=False,
            summary=f"Dissertation artifacts not renderable: {len(bundle.issues)} metadata blocker(s).",
            blockers=tuple(bundle.blockers()),
        )
    written = write_dissertation_bundle(bundle, destination=destination)
    return GateResult(
        name="dissertation-artifacts",
        passed=True,
        summary=f"Rendered {len(written)} dissertation artifact(s) into {destination}.",
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


def _gate_length_conformance(manuscript_md: Path, profile: WorkTypeProfile) -> GateResult:
    if not manuscript_md.exists():
        return GateResult(
            name="length-conformance",
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
    char_count = len(manuscript_md.read_text(encoding="utf-8"))
    blockers: list[Blocker] = []
    if profile.min_chars is not None and char_count < profile.min_chars:
        blockers.append(
            Blocker(
                category="length-conformance",
                code="below-minimum-length",
                message=(
                    f"Manuscript has {char_count} characters, {profile.title} requires at least {profile.min_chars}."
                ),
                repairable=True,
                blocks_statuses=("submission-ready",),
                details={"actual": char_count, "min_chars": profile.min_chars},
            )
        )
    if profile.max_chars is not None and char_count > profile.max_chars:
        blockers.append(
            Blocker(
                category="length-conformance",
                code="above-maximum-length",
                message=(
                    f"Manuscript has {char_count} characters, {profile.title} allows at most {profile.max_chars}."
                ),
                repairable=True,
                blocks_statuses=("submission-ready",),
                details={"actual": char_count, "max_chars": profile.max_chars},
            )
        )
    if blockers:
        return GateResult(
            name="length-conformance",
            passed=False,
            summary=f"Length check found {len(blockers)} issue(s) against the work-type range.",
            blockers=tuple(blockers),
            details={"actual_chars": char_count, "min_chars": profile.min_chars, "max_chars": profile.max_chars},
        )
    return GateResult(
        name="length-conformance",
        passed=True,
        summary=f"Manuscript length {char_count} chars is inside the configured range.",
        details={"actual_chars": char_count, "min_chars": profile.min_chars, "max_chars": profile.max_chars},
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


def _gate_thesis_quality_contract(manuscript_md: Path) -> GateResult | None:
    thesis_root = _infer_thesis_root(manuscript_md)
    if thesis_root is None:
        return None

    ledgers_dir = thesis_root / "ledgers"
    reviews_dir = thesis_root / "reviews"
    if not ledgers_dir.exists() and not reviews_dir.exists():
        return GateResult(
            name="thesis-quality-contract",
            passed=True,
            summary="Managed thesis quality contract is not applicable outside a thesis bundle.",
        )

    blockers: list[Blocker] = []
    claim_ledgers = sorted(path for path in ledgers_dir.glob("*.md")) if ledgers_dir.exists() else []
    verification_logs = [path for path in claim_ledgers if "verification-log" in path.stem.casefold()]
    claim_ledgers = [path for path in claim_ledgers if path not in verification_logs]
    review_files = (
        sorted(path for path in reviews_dir.glob("*.md") if "one-shot" not in path.stem.casefold())
        if reviews_dir.exists()
        else []
    )

    if not claim_ledgers:
        blockers.append(
            Blocker(
                category="verification",
                code="thesis-ledger-missing",
                message="Managed thesis bundle is missing a claim ledger for the pre-final quality contract.",
                repairable=True,
                blocks_statuses=("submission-ready",),
            )
        )
    if not verification_logs:
        blockers.append(
            Blocker(
                category="verification",
                code="thesis-verification-log-missing",
                message="Managed thesis bundle is missing a verification log for strong claims.",
                repairable=True,
                blocks_statuses=("submission-ready",),
            )
        )
    if not review_files:
        blockers.append(
            Blocker(
                category="review",
                code="thesis-review-artifact-missing",
                message="Managed thesis bundle is missing a review artifact for the pre-final quality contract.",
                repairable=True,
                blocks_statuses=("submission-ready",),
            )
        )

    required_markers = (
        "claim_id",
        "basis_type",
        "primary_identifier",
        "official_primary_link",
        "pinpoint_locator",
        "support_excerpt",
        "verification_status",
        "draft_use",
    )
    for ledger_path in claim_ledgers:
        text = ledger_path.read_text(encoding="utf-8")
        missing_markers = [marker for marker in required_markers if marker not in text]
        if missing_markers:
            blockers.append(
                Blocker(
                    category="verification",
                    code="thesis-claim-passport-incomplete",
                    message=(
                        f"Ledger `{ledger_path.name}` is missing strict claim-passport markers: "
                        + ", ".join(missing_markers)
                    ),
                    repairable=True,
                    blocks_statuses=("submission-ready",),
                )
            )
        for raw_line in text.splitlines():
            line = raw_line.casefold()
            if ("needs-recheck" in line or "unsafe-for-draft" in line or "unsafe for draft" in line) and "safe" in line:
                blockers.append(
                    Blocker(
                        category="verification",
                        code="thesis-unsafe-draft-use",
                        message=(
                            f"Ledger `{ledger_path.name}` marks a needs-recheck or unsafe claim as safe for drafting."
                        ),
                        repairable=True,
                        blocks_statuses=("submission-ready",),
                    )
                )
                break

    if review_files:
        artifact_texts = {path.stem: path.read_text(encoding="utf-8") for path in review_files}
        review_signals = extract_thesis_runtime_signals(artifact_texts)
        blockers.extend(review_signals.blockers)

    if blockers:
        return GateResult(
            name="thesis-quality-contract",
            passed=False,
            summary=f"Thesis quality contract found {len(blockers)} issue(s) across managed ledgers/reviews.",
            blockers=tuple(blockers),
            details={
                "claim_ledgers": [str(path) for path in claim_ledgers],
                "verification_logs": [str(path) for path in verification_logs],
                "review_files": [str(path) for path in review_files],
            },
        )

    return GateResult(
        name="thesis-quality-contract",
        passed=True,
        summary="Managed thesis bundle exposes strict claim-passport and review artifacts.",
        details={
            "claim_ledgers": [str(path) for path in claim_ledgers],
            "verification_logs": [str(path) for path in verification_logs],
            "review_files": [str(path) for path in review_files],
        },
    )


def _infer_thesis_root(manuscript_md: Path) -> Path | None:
    if manuscript_md.name != "full-draft.md":
        return None
    if manuscript_md.parent.name == "manuscript":
        return manuscript_md.parent.parent
    if manuscript_md.parent.name == "thesis":
        return manuscript_md.parent
    return None


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


def _dissertation_contour_gates(root_dir: Path, profile: WorkTypeProfile) -> list[GateResult]:
    paths = {
        "historiography-coverage": root_dir / "maps" / "historiography-map.md",
        "novelty-contract": root_dir / "maps" / "novelty-contribution-map.md",
        "claim-map-coverage": root_dir / "maps" / "dissertation-claim-map.md",
        "counterargument-coverage": root_dir / "reviews" / "counterargument-review.md",
        "dissertation-review-coverage": root_dir / "reviews" / "dissertation-review.md",
        "publication-evidence": root_dir / "publications" / "publication-evidence.md",
        "publication-claim-coverage": root_dir / "publications" / "publication-claim-matrix.md",
        "leading-organization-packet": root_dir / "defense" / "leading-organization.md",
        "opponents-packet": root_dir / "defense" / "opponents.md",
    }
    gates = [
        _gate_markdown_contract(
            "historiography-coverage",
            paths["historiography-coverage"],
            required_markers=("поле", "школ", "неразреш"),
            min_chars=120,
        ),
        _gate_markdown_contract(
            "novelty-contract",
            paths["novelty-contract"],
            required_markers=("новиз", "вклад", "огранич"),
            min_chars=120,
        ),
        _gate_markdown_contract(
            "claim-map-coverage",
            paths["claim-map-coverage"],
            required_markers=("claim", "counterargument", "limits"),
            min_chars=140,
        ),
        _gate_markdown_contract(
            "dissertation-review-coverage",
            paths["dissertation-review-coverage"],
            required_markers=("новизн", "вклад", "методолог", "огранич"),
            min_chars=120,
        ),
    ]
    if profile.requires_counterargument_pass:
        gates.append(
            _gate_markdown_contract(
                "counterargument-coverage",
                paths["counterargument-coverage"],
                required_markers=("позици", "ответ"),
                min_chars=100,
            )
        )
    if profile.requires_publication_evidence:
        gates.append(
            _gate_markdown_contract(
                "publication-evidence",
                paths["publication-evidence"],
                required_markers=("статус", "выходные данные", "связ"),
                min_chars=100,
            )
        )
    if "publication-claim-matrix" in profile.required_artifact_groups:
        gates.append(
            _gate_markdown_contract(
                "publication-claim-coverage",
                paths["publication-claim-coverage"],
                required_markers=("тезис", "глава", "публикац", "покрыт"),
                min_chars=120,
            )
        )
    if "defense-packet" in profile.required_artifact_groups:
        gates.extend(
            (
                _gate_markdown_contract(
                    "leading-organization-packet",
                    paths["leading-organization-packet"],
                    required_markers=("ведущ", "компетенц", "связ"),
                    min_chars=90,
                ),
                _gate_markdown_contract(
                    "opponents-packet",
                    paths["opponents-packet"],
                    required_markers=("оппонент", "специализац", "связ"),
                    min_chars=90,
                ),
            )
        )
    return gates


def _gate_markdown_contract(
    gate_name: str,
    path: Path,
    *,
    required_markers: tuple[str, ...],
    min_chars: int,
) -> GateResult:
    if not path.exists():
        return GateResult(
            name=gate_name,
            passed=False,
            summary=f"Required dissertation artifact missing at {path}.",
            blockers=(
                Blocker(
                    category=gate_name,
                    code="artifact-missing",
                    message=f"Required dissertation artifact missing: {path}",
                    repairable=True,
                    blocks_statuses=("submission-ready",),
                ),
            ),
        )
    text = path.read_text(encoding="utf-8").strip()
    blockers: list[Blocker] = []
    if len(text) < min_chars:
        blockers.append(
            Blocker(
                category=gate_name,
                code="artifact-too-thin",
                message=f"Artifact `{path.name}` is too short for a reliable dissertation contract.",
                repairable=True,
                blocks_statuses=("submission-ready",),
                details={"actual_chars": len(text), "minimum_chars": min_chars},
            )
        )
    normalized = text.casefold()
    for marker in required_markers:
        if marker.casefold() not in normalized:
            blockers.append(
                Blocker(
                    category=gate_name,
                    code="required-marker-missing",
                    message=f"Artifact `{path.name}` is missing the expected marker `{marker}`.",
                    repairable=True,
                    blocks_statuses=("submission-ready",),
                    details={"marker": marker},
                )
            )
    if blockers:
        return GateResult(
            name=gate_name,
            passed=False,
            summary=f"Dissertation artifact `{path.name}` has {len(blockers)} contract issue(s).",
            blockers=tuple(blockers),
        )
    return GateResult(
        name=gate_name,
        passed=True,
        summary=f"Dissertation artifact `{path.name}` matches the minimum contract.",
    )


def write_report(report: OneShotReport, *, markdown_path: Path, json_path: Path) -> None:
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(report.to_markdown(), encoding="utf-8")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
