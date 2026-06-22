"""Helpers for dissertation-specific thesis contour paths and status."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .work_type import WorkTypeProfile, resolve_profile
from .workspace import WorkConfig


@dataclass(frozen=True)
class DissertationContourPaths:
    root_dir: Path
    metadata_path: Path
    artifacts_dir: Path
    maps_dir: Path
    historiography_map_path: Path
    novelty_map_path: Path
    claim_map_path: Path
    chapter_contracts_dir: Path
    reviews_dir: Path
    counterargument_review_path: Path
    dissertation_review_path: Path
    publications_dir: Path
    publication_evidence_path: Path
    publication_claim_matrix_path: Path
    defense_dir: Path
    author_abstract_path: Path
    defense_checklist_path: Path
    leading_organization_path: Path
    opponents_path: Path


def is_dissertation_artifact_type(artifact_type: str | None) -> bool:
    profile = resolve_profile(artifact_type)
    return bool(profile and profile.artifact_family == "dissertation")


def dissertation_profile(work: WorkConfig) -> WorkTypeProfile | None:
    return resolve_profile(work.artifact_type)


def dissertation_paths(work: WorkConfig) -> DissertationContourPaths:
    assert work.thesis is not None
    root_dir = work.thesis.paths.root_dir / "dissertation"
    artifacts_dir = root_dir / "artifacts"
    defense_dir = root_dir / "defense"
    return DissertationContourPaths(
        root_dir=root_dir,
        metadata_path=root_dir / "metadata.toml",
        artifacts_dir=artifacts_dir,
        maps_dir=root_dir / "maps",
        historiography_map_path=root_dir / "maps" / "historiography-map.md",
        novelty_map_path=root_dir / "maps" / "novelty-contribution-map.md",
        claim_map_path=root_dir / "maps" / "dissertation-claim-map.md",
        chapter_contracts_dir=root_dir / "chapter-contracts",
        reviews_dir=root_dir / "reviews",
        counterargument_review_path=root_dir / "reviews" / "counterargument-review.md",
        dissertation_review_path=root_dir / "reviews" / "dissertation-review.md",
        publications_dir=root_dir / "publications",
        publication_evidence_path=root_dir / "publications" / "publication-evidence.md",
        publication_claim_matrix_path=root_dir / "publications" / "publication-claim-matrix.md",
        defense_dir=defense_dir,
        author_abstract_path=artifacts_dir / "author-abstract.md",
        defense_checklist_path=artifacts_dir / "defense-checklist.md",
        leading_organization_path=defense_dir / "leading-organization.md",
        opponents_path=defense_dir / "opponents.md",
    )


def chapter_contract_paths(work: WorkConfig, *, minimum_chapters: int | None = None) -> list[Path]:
    paths = dissertation_paths(work)
    profile = dissertation_profile(work)
    count = minimum_chapters or (profile.minimum_chapters if profile is not None else 3) or 3
    return [paths.chapter_contracts_dir / f"{index:02d}-chapter-contract.md" for index in range(1, count + 1)]


def manuscript_character_count(work: WorkConfig) -> int:
    assert work.thesis is not None
    if work.thesis.full_draft_path.exists():
        return len(work.thesis.full_draft_path.read_text(encoding="utf-8"))
    total = 0
    for section in work.thesis.section_order:
        if section.exists():
            total += len(section.read_text(encoding="utf-8"))
    return total


def inspect_dissertation_contour(work: WorkConfig) -> dict[str, Any] | None:
    if not work.thesis or not is_dissertation_artifact_type(work.artifact_type):
        return None

    profile = dissertation_profile(work)
    paths = dissertation_paths(work)
    contracts = chapter_contract_paths(work, minimum_chapters=profile.minimum_chapters if profile else None)
    maps = [
        ("historiography-map", paths.historiography_map_path),
        ("novelty-contribution-map", paths.novelty_map_path),
        ("dissertation-claim-map", paths.claim_map_path),
    ]
    reviews = [
        ("counterargument-review", paths.counterargument_review_path),
        ("dissertation-review", paths.dissertation_review_path),
    ]
    artifacts = [
        ("author-abstract", paths.author_abstract_path),
        ("defense-checklist", paths.defense_checklist_path),
    ]
    publication_artifacts = []
    if profile is not None and "publication-claim-matrix" in profile.required_artifact_groups:
        publication_artifacts.append(("publication-claim-matrix", paths.publication_claim_matrix_path))
    defense_artifacts = []
    if profile is not None and "defense-packet" in profile.required_artifact_groups:
        defense_artifacts = [
            ("leading-organization", paths.leading_organization_path),
            ("opponents", paths.opponents_path),
        ]

    map_entries = [
        {"artifact_id": artifact_id, "path": str(path), "exists": path.exists()} for artifact_id, path in maps
    ]
    review_entries = [
        {"artifact_id": artifact_id, "path": str(path), "exists": path.exists()} for artifact_id, path in reviews
    ]
    artifact_entries = [
        {"artifact_id": artifact_id, "path": str(path), "exists": path.exists()} for artifact_id, path in artifacts
    ]
    publication_entries = [
        {"artifact_id": artifact_id, "path": str(path), "exists": path.exists()}
        for artifact_id, path in publication_artifacts
    ]
    defense_entries = [
        {"artifact_id": artifact_id, "path": str(path), "exists": path.exists()}
        for artifact_id, path in defense_artifacts
    ]
    contract_entries = [{"path": str(path), "exists": path.exists()} for path in contracts]

    if not all(item["exists"] for item in map_entries + contract_entries):
        suggested_next_action = "build-maps"
    elif not paths.dissertation_review_path.exists():
        suggested_next_action = "verify-claims"
    elif not paths.counterargument_review_path.exists():
        suggested_next_action = "counterargument-pass"
    elif (
        not all(item["exists"] for item in artifact_entries)
        or any(not item["exists"] for item in publication_entries)
        or any(not item["exists"] for item in defense_entries)
    ):
        suggested_next_action = "formal-artifacts"
    else:
        suggested_next_action = "draft-author-position"

    return {
        "kind": "dissertation-contour",
        "available": True,
        "profile_id": profile.identifier if profile else work.artifact_type,
        "artifact_family": profile.artifact_family if profile else "dissertation",
        "metadata": {"path": str(paths.metadata_path), "exists": paths.metadata_path.exists()},
        "maps": map_entries,
        "chapter_contracts": contract_entries,
        "reviews": review_entries,
        "artifacts": artifact_entries,
        "publication_artifacts": publication_entries,
        "defense_artifacts": defense_entries,
        "publication_evidence": {
            "path": str(paths.publication_evidence_path),
            "exists": paths.publication_evidence_path.exists(),
        },
        "publication_claim_matrix": {
            "path": str(paths.publication_claim_matrix_path),
            "exists": paths.publication_claim_matrix_path.exists(),
        },
        "publication_matrix_complete": bool(publication_entries)
        and all(item["exists"] for item in publication_entries),
        "review_sequence_complete": all(item["exists"] for item in review_entries),
        "character_count": manuscript_character_count(work),
        "suggested_next_action": suggested_next_action,
    }
