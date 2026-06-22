from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

from .runtime_status import build_attachments, build_checkpoint, build_failure, build_runtime_status, write_status
from .utils import utc_now, write_json


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run launcher commands in the background.")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("missing command after --")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "launcher.log"
    result_path = run_dir / "result.json"
    request_path = run_dir / "request.json"
    status_path = run_dir / "status.json"
    started_at = utc_now()
    request = _read_request(request_path)
    record_id = str(request.get("run_id") or run_dir.name)
    attachments = build_attachments(
        {
            "status": status_path,
            "request": request_path,
            "result": result_path,
            "log": log_path,
        }
    )
    checkpoints = [
        build_checkpoint(
            "queued",
            status="queued",
            stage="queued",
            timestamp=started_at,
            message="Run wrapper started.",
        )
    ]
    write_status(
        status_path,
        build_runtime_status(
            record_id=record_id,
            entity_kind="workflow-run",
            status="queued",
            stage="queued",
            project_id=_optional_text(request.get("project_id")),
            project_title=_optional_text(request.get("project_title")),
            project_root=_optional_text(request.get("project_root")),
            work_id=_optional_text(request.get("work_id")),
            work_title=_optional_text(request.get("work_title")),
            lane=_optional_text(request.get("lane")),
            action=_optional_text(request.get("action")),
            started_at=started_at,
            summary="Run queued for launch.",
            checkpoints=checkpoints,
            attachments=attachments,
        ),
    )

    return_code = 1
    error_text = None

    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"[{started_at}] Running command:\n")
        log_handle.write(f"{shlex.join(args.command)}\n\n")
        log_handle.flush()
        checkpoints.append(
            build_checkpoint(
                "command-started",
                status="running",
                stage="launching",
                timestamp=started_at,
                message=shlex.join(args.command),
            )
        )
        write_status(
            status_path,
            build_runtime_status(
                record_id=record_id,
                entity_kind="workflow-run",
                status="running",
                stage="launching",
                project_id=_optional_text(request.get("project_id")),
                project_title=_optional_text(request.get("project_title")),
                project_root=_optional_text(request.get("project_root")),
                work_id=_optional_text(request.get("work_id")),
                work_title=_optional_text(request.get("work_title")),
                lane=_optional_text(request.get("lane")),
                action=_optional_text(request.get("action")),
                started_at=started_at,
                summary="Launcher command is running.",
                checkpoints=checkpoints,
                attachments=build_attachments(
                    {
                        "status": status_path,
                        "request": request_path,
                        "result": result_path,
                        "log": log_path,
                    }
                ),
            ),
        )
        try:
            completed = subprocess.run(
                args.command,
                cwd=args.cwd,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            return_code = completed.returncode
        except OSError as exc:
            error_text = str(exc)
            log_handle.write(f"\n[wrapper-error] {error_text}\n")
            log_handle.flush()

    finished_at = utc_now()
    payload: dict[str, object] = {
        "started_at": started_at,
        "finished_at": finished_at,
        "returncode": return_code,
        "status": "success" if return_code == 0 else "failed",
        "command": args.command,
        "cwd": str(Path(args.cwd).resolve()),
        "log_path": str(log_path),
    }
    if error_text:
        payload["error"] = error_text
    write_json(result_path, payload)
    failure = None
    final_status = "succeeded" if return_code == 0 else "failed"
    final_stage = "completed" if return_code == 0 else "failed"
    final_message = "Launcher command completed successfully."
    if error_text:
        failure = build_failure("runtime", "launcher-os-error", error_text, retryable=False)
        final_message = error_text
    elif return_code != 0:
        failure = build_failure(
            "process",
            "command-exited-nonzero",
            f"Launcher command exited with code {return_code}.",
            retryable=True,
            details={"returncode": return_code},
        )
        final_message = f"Launcher command exited with code {return_code}."
    checkpoints.append(
        build_checkpoint(
            "command-finished",
            status=final_status,
            stage=final_stage,
            timestamp=finished_at,
            message=final_message,
            failure=failure,
        )
    )
    write_status(
        status_path,
        build_runtime_status(
            record_id=record_id,
            entity_kind="workflow-run",
            status=final_status,
            stage=final_stage,
            project_id=_optional_text(request.get("project_id")),
            project_title=_optional_text(request.get("project_title")),
            project_root=_optional_text(request.get("project_root")),
            work_id=_optional_text(request.get("work_id")),
            work_title=_optional_text(request.get("work_title")),
            lane=_optional_text(request.get("lane")),
            action=_optional_text(request.get("action")),
            started_at=started_at,
            finished_at=finished_at,
            summary=final_message,
            failure=failure,
            checkpoints=checkpoints,
            attachments=build_attachments(
                {
                    "status": status_path,
                    "request": request_path,
                    "result": result_path,
                    "log": log_path,
                }
            ),
        ),
    )
    return return_code


def _read_request(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
