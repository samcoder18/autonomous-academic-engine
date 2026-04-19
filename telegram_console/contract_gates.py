from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .action_specs import ExecutionContract, RequiredArtifact
from .standards import StandardProfileResolution

CONTRACT_GATE_STATUSES = {"pass", "warn", "block", "not-applicable"}


@dataclass(frozen=True)
class ContractGateResult:
    gate_id: str
    status: str
    reason: str
    blocks_export: bool = False
    blocks_submission_ready: bool = False
    lane: str | None = None
    action: str | None = None
    profile_id: str | None = None
    artifact: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "gate_id": self.gate_id,
            "status": self.status if self.status in CONTRACT_GATE_STATUSES else "warn",
            "reason": self.reason,
            "blocks_export": self.blocks_export,
            "blocks_submission_ready": self.blocks_submission_ready,
            "lane": self.lane,
            "action": self.action,
            "profile_id": self.profile_id,
            "artifact": self.artifact,
        }
        if self.details:
            payload["details"] = self.details
        return payload


def evaluate_contract_gates(
    *,
    contract: ExecutionContract | None,
    profile: StandardProfileResolution | dict[str, Any] | None,
) -> tuple[ContractGateResult, ...]:
    if contract is None:
        return (
            ContractGateResult(
                gate_id="execution-contract",
                status="block",
                reason="Execution contract is missing, so required artifacts and gates cannot be evaluated.",
                blocks_export=True,
                blocks_submission_ready=True,
            ),
        )

    results: list[ContractGateResult] = []
    results.extend(_artifact_gates("required-context", contract.required_context, contract=contract))
    results.extend(_artifact_gates("required-output", contract.required_outputs, contract=contract))
    results.extend(_standards_gates(contract=contract, profile=profile))
    return tuple(results)


def blocking_gate_blockers(
    gates: Iterable[ContractGateResult | dict[str, Any]],
    *,
    lane: str | None,
    action: str | None = None,
) -> tuple[dict[str, Any], ...]:
    blockers: list[dict[str, Any]] = []
    for gate in gates:
        payload = gate.to_dict() if isinstance(gate, ContractGateResult) else gate if isinstance(gate, dict) else {}
        if not payload or payload.get("status") != "block":
            continue
        if not payload.get("blocks_export") and not payload.get("blocks_submission_ready"):
            continue
        gate_id = _optional_text(payload.get("gate_id")) or "contract-gate"
        gate_lane = _optional_text(payload.get("lane")) or lane
        category = "standards-consistency" if gate_id.startswith("standards-") else "contract-gate"
        code = f"{gate_lane or 'workflow'}-{gate_id.replace(':', '-')}"
        blocker: dict[str, Any] = {
            "category": category,
            "code": code,
            "message": _optional_text(payload.get("reason")) or gate_id,
            "repairable": True,
            "lane": gate_lane,
            "details": {
                "gate_id": gate_id,
                "status": payload.get("status"),
                "blocks_export": bool(payload.get("blocks_export")),
                "blocks_submission_ready": bool(payload.get("blocks_submission_ready")),
                "action": _optional_text(payload.get("action")) or action,
            },
        }
        profile_id = _optional_text(payload.get("profile_id"))
        if profile_id:
            blocker["profile_id"] = profile_id
            blocker["details"]["profile_id"] = profile_id
        artifact = _optional_text(payload.get("artifact"))
        if artifact:
            blocker["target"] = artifact
            blocker["details"]["artifact"] = artifact
        if payload.get("blocks_submission_ready"):
            blocker["blocks_statuses"] = ["submission-ready"]
        blockers.append(blocker)
    return tuple(blockers)


def _artifact_gates(
    gate_prefix: str,
    artifacts: tuple[RequiredArtifact, ...],
    *,
    contract: ExecutionContract,
) -> tuple[ContractGateResult, ...]:
    results: list[ContractGateResult] = []
    for artifact in artifacts:
        requirement = artifact.requirement.strip().lower()
        gate_id = f"{gate_prefix}:{artifact.name}"
        path = Path(artifact.path)
        exists = path.exists()
        details = {
            "path": artifact.path,
            "requirement": artifact.requirement,
            "description": artifact.description,
        }
        if requirement == "required":
            if exists:
                results.append(
                    ContractGateResult(
                        gate_id=gate_id,
                        status="pass",
                        reason=f"Required artifact `{artifact.name}` exists.",
                        lane=contract.lane,
                        action=contract.action,
                        artifact=artifact.path,
                        details=details,
                    )
                )
            else:
                results.append(
                    ContractGateResult(
                        gate_id=gate_id,
                        status="block",
                        reason=f"Required artifact `{artifact.name}` is missing.",
                        blocks_export=True,
                        blocks_submission_ready=True,
                        lane=contract.lane,
                        action=contract.action,
                        artifact=artifact.path,
                        details=details,
                    )
                )
            continue
        if requirement == "conditional":
            results.append(
                ContractGateResult(
                    gate_id=gate_id,
                    status="pass" if exists else "not-applicable",
                    reason=(
                        f"Conditional artifact `{artifact.name}` exists."
                        if exists
                        else f"Conditional artifact `{artifact.name}` is not required for this gate."
                    ),
                    lane=contract.lane,
                    action=contract.action,
                    artifact=artifact.path,
                    details=details,
                )
            )
            continue
        results.append(
            ContractGateResult(
                gate_id=gate_id,
                status="warn",
                reason=f"Artifact `{artifact.name}` has unknown requirement `{artifact.requirement}`.",
                lane=contract.lane,
                action=contract.action,
                artifact=artifact.path,
                details=details,
            )
        )
    return tuple(results)


def _standards_gates(
    *,
    contract: ExecutionContract,
    profile: StandardProfileResolution | dict[str, Any] | None,
) -> tuple[ContractGateResult, ...]:
    if profile is None:
        return (
            ContractGateResult(
                gate_id="standards-profile",
                status="not-applicable",
                reason="No standards profile is bound to this run.",
                lane=contract.lane,
                action=contract.action,
            ),
        )

    profile_id = _profile_text(profile, "resolved_profile_id") or _profile_text(profile, "profile_id")
    raw_status = _profile_text(profile, "raw_status")
    normalized_path = _profile_text(profile, "normalized_path")
    official_only = _profile_bool(profile, "official_only")
    conflict_flag = _profile_bool(profile, "conflict_flag")

    results: list[ContractGateResult] = []
    if normalized_path:
        normalized_exists = Path(normalized_path).exists()
        results.append(
            ContractGateResult(
                gate_id="standards-normalized-profile",
                status="pass" if normalized_exists else "block",
                reason=(
                    f"Normalized standards profile `{profile_id or 'n/a'}` exists."
                    if normalized_exists
                    else f"Normalized standards profile `{profile_id or 'n/a'}` is missing."
                ),
                blocks_export=not normalized_exists,
                blocks_submission_ready=not normalized_exists,
                lane=contract.lane,
                action=contract.action,
                profile_id=profile_id,
                artifact=normalized_path,
            )
        )

    if raw_status in {"missing", "partial"}:
        results.append(
            ContractGateResult(
                gate_id="standards-raw",
                status="block",
                reason=f"Raw standards bundle is {raw_status} for profile `{profile_id or 'n/a'}`.",
                blocks_export=True,
                blocks_submission_ready=True,
                lane=contract.lane,
                action=contract.action,
                profile_id=profile_id,
                details={"raw_status": raw_status},
            )
        )
    elif raw_status:
        results.append(
            ContractGateResult(
                gate_id="standards-raw",
                status="pass",
                reason=f"Raw standards bundle is {raw_status} for profile `{profile_id or 'n/a'}`.",
                lane=contract.lane,
                action=contract.action,
                profile_id=profile_id,
                details={"raw_status": raw_status},
            )
        )

    results.append(
        ContractGateResult(
            gate_id="standards-conflict",
            status="block" if conflict_flag else "pass",
            reason=(
                f"Standards profile `{profile_id or 'n/a'}` has a visible conflict flag."
                if conflict_flag
                else f"Standards profile `{profile_id or 'n/a'}` has no visible conflict flag."
            ),
            blocks_export=conflict_flag,
            blocks_submission_ready=conflict_flag,
            lane=contract.lane,
            action=contract.action,
            profile_id=profile_id,
        )
    )
    results.append(
        ContractGateResult(
            gate_id="standards-official-only",
            status="pass" if official_only else "warn",
            reason=(
                f"Standards profile `{profile_id or 'n/a'}` is marked official-only."
                if official_only
                else f"Standards profile `{profile_id or 'n/a'}` is not marked official-only."
            ),
            lane=contract.lane,
            action=contract.action,
            profile_id=profile_id,
        )
    )
    return tuple(results)


def _profile_text(profile: StandardProfileResolution | dict[str, Any], name: str) -> str | None:
    if isinstance(profile, dict):
        return _optional_text(profile.get(name))
    return _optional_text(getattr(profile, name, None))


def _profile_bool(profile: StandardProfileResolution | dict[str, Any], name: str) -> bool:
    if isinstance(profile, dict):
        return bool(profile.get(name))
    return bool(getattr(profile, name, False))


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
