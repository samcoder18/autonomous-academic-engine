"""Explain DOCX export readiness without invoking export."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .one_shot import ONE_SHOT_REPORT_VERSION
from .workspace import article_bundle_paths, load_workspace_config, resolve_work_config


def explain_export(root_dir: str | Path, subject: str, *, work_id: str | None = None) -> dict[str, Any]:
    """Return a JSON-ready explanation of current DOCX export blockers."""

    workspace = load_workspace_config(root_dir)
    work = resolve_work_config(workspace, work_id=work_id)
    root = workspace.root_dir

    lane, article_slug = _resolve_subject(subject)
    if lane is None:
        return _payload(
            subject,
            work.slug,
            [
                _reason(
                    "unsupported-export-subject",
                    f"Unsupported export subject: {subject}",
                    {"subject": subject, "supported_subjects": ["thesis", "article:<slug>"]},
                )
            ],
        )

    latest = _latest_workflow(root, work.slug, lane)
    if not latest or latest.get("execution_status") != "succeeded":
        return _payload(
            subject,
            work.slug,
            [
                _reason(
                    "no-successful-workflow",
                    f"No latest successful workflow was found for {work.slug}/{lane}.",
                    _workflow_details(latest, lane=lane, work_id=work.slug),
                )
            ],
        )

    readiness = latest.get("readiness_status")
    if readiness != "submission-ready":
        return _payload(
            subject,
            work.slug,
            [
                _reason(
                    "latest-workflow-not-submission-ready",
                    "Latest workflow is not submission-ready.",
                    {
                        **_workflow_details(latest, lane=lane, work_id=work.slug),
                        "readiness_status": readiness or "not-evaluated",
                    },
                )
            ],
        )

    failed_gates = _failed_mandatory_gates(latest)
    if failed_gates:
        return _payload(
            subject,
            work.slug,
            [
                _reason(
                    "mandatory-gates-failed",
                    "Latest workflow contains failed mandatory gates.",
                    {
                        **_workflow_details(latest, lane=lane, work_id=work.slug),
                        "gates": failed_gates,
                        "gate_ids": [gate.get("gate_id") or gate.get("gate") for gate in failed_gates],
                    },
                )
            ],
        )

    promotion = latest.get("promotion")
    promotion_status = promotion.get("status") if isinstance(promotion, dict) else None
    if promotion_status in {"blocked", "conflict"}:
        return _payload(
            subject,
            work.slug,
            [
                _reason(
                    "promotion-not-safe",
                    "Latest workflow promotion did not complete safely.",
                    {
                        **_workflow_details(latest, lane=lane, work_id=work.slug),
                        "promotion_status": promotion_status,
                    },
                )
            ],
        )

    if lane == "thesis":
        if work.thesis is None or not _machine_gates_passed(work.thesis.reviews_dir):
            return _payload(
                subject,
                work.slug,
                [
                    _reason(
                        "machine-gates-not-passed",
                        "Thesis one-shot machine gates have not passed.",
                        {
                            **_workflow_details(latest, lane=lane, work_id=work.slug),
                            "readiness_status": latest.get("readiness_status"),
                            "reviews_dir": str(work.thesis.reviews_dir) if work.thesis else None,
                            "report_glob": "*one-shot-report.json",
                            "required_version": ONE_SHOT_REPORT_VERSION,
                            "required_status": "machine-gates-passed",
                        },
                    )
                ],
            )

    if lane == "article":
        final_markdown = article_bundle_paths(work, article_slug or "")["final_markdown"]
        if not final_markdown.exists():
            return _payload(
                subject,
                work.slug,
                [
                    _reason(
                        "article-final-markdown-missing",
                        "Article final Markdown file is missing.",
                        {
                            **_workflow_details(latest, lane=lane, work_id=work.slug),
                            "readiness_status": latest.get("readiness_status"),
                            "article_slug": article_slug,
                            "expected_path": str(final_markdown),
                        },
                    )
                ],
            )

    return _payload(subject, work.slug, [])


def _resolve_subject(subject: str) -> tuple[str | None, str | None]:
    if subject == "thesis":
        return "thesis", None
    if subject.startswith("article:"):
        article_slug = subject.split(":", 1)[1].strip()
        if article_slug:
            return "article", article_slug
        return None, None
    return None, None


def _latest_workflow(root: Path, work_id: str, lane: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    workflow_root = root / "output" / "runs"
    if workflow_root.exists():
        for path in workflow_root.glob("*/workflow.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if not isinstance(payload, dict):
                continue
            if payload.get("version") != "workflow-run/v1":
                continue
            if payload.get("work_id") != work_id or payload.get("lane") != lane:
                continue
            candidates.append(payload)
    candidates.sort(key=lambda item: str(item.get("finished_at") or item.get("started_at") or ""), reverse=True)
    return candidates[0] if candidates else None


def _failed_mandatory_gates(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    gates = workflow.get("gates")
    if not isinstance(gates, list):
        return [{"gate_id": "gates", "status": "missing", "blocking": True}]
    return [
        gate
        for gate in gates
        if isinstance(gate, dict) and bool(gate.get("blocking")) and gate.get("status") != "pass"
    ]


def _machine_gates_passed(reviews_dir: Path) -> bool:
    reports: list[tuple[float, Any]] = []
    if reviews_dir.exists():
        for path in reviews_dir.glob("*one-shot-report.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = None
            except OSError:
                payload = None
            reports.append((_one_shot_report_recency(path, payload), payload))
    reports.sort(key=lambda item: item[0], reverse=True)
    latest = reports[0][1] if reports else None
    return bool(
        isinstance(latest, dict)
        and latest.get("version") == ONE_SHOT_REPORT_VERSION
        and latest.get("status") == "machine-gates-passed"
    )


def _one_shot_report_recency(path: Path, payload: Any) -> float:
    if isinstance(payload, dict):
        finished_at = _parse_timestamp(payload.get("finished_at"))
        if finished_at is not None:
            return finished_at
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _parse_timestamp(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _workflow_details(workflow: dict[str, Any] | None, *, lane: str, work_id: str) -> dict[str, Any]:
    details: dict[str, Any] = {"work_id": work_id, "lane": lane}
    if workflow:
        details.update(
            {
                "workflow_id": workflow.get("workflow_id") or workflow.get("run_id"),
                "execution_status": workflow.get("execution_status"),
                "started_at": workflow.get("started_at"),
                "finished_at": workflow.get("finished_at"),
            }
        )
    return details


def _reason(code: str, message: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details}


def _payload(subject: str, work_id: str, reasons: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "kind": "export-explanation",
        "version": "v1",
        "subject": subject,
        "work_id": work_id,
        "status": "blocked" if reasons else "ready",
        "reasons": reasons,
    }
