from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .runtime_status import build_attachments, build_checkpoint, build_failure, build_runtime_status, write_status
from .utils import append_text, utc_now, write_json


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex chat turns in the background.")
    parser.add_argument("--task-dir", required=True)
    return parser.parse_args(argv)


def read_request(task_dir: Path) -> dict[str, object]:
    path = task_dir / "request.json"
    if not path.exists():
        raise RuntimeError(f"Missing request file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_command(request: dict[str, object], response_path: Path, session_id: str | None) -> list[str]:
    codex_bin = str(request.get("codex_bin") or "").strip() or "codex"
    project_root = str(request.get("project_root") or "").strip()
    if not project_root:
        raise RuntimeError("request.project_root is required")

    model = str(request.get("codex_model") or "").strip() or None
    command = [
        codex_bin,
        "exec",
        "-C",
        project_root,
        "--skip-git-repo-check",
        "--full-auto",
    ]
    if session_id:
        command.extend(["resume", "--json", "-o", str(response_path)])
        if model:
            command.extend(["-m", model])
        command.extend([session_id, "-"])
        return command

    command.extend(["--json", "-o", str(response_path)])
    if model:
        command.extend(["-m", model])
    command.append("-")
    return command


def parse_json_events(stdout_text: str) -> tuple[str | None, str | None]:
    thread_id = None
    last_message = None
    for raw_line in stdout_text.splitlines():
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("type") == "thread.started":
            value = payload.get("thread_id")
            if isinstance(value, str) and value.strip():
                thread_id = value.strip()
        item = payload.get("item")
        if payload.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message":
                value = item.get("text")
                if isinstance(value, str) and value.strip():
                    last_message = value.strip()
    return thread_id, last_message


def run_attempt(
    request: dict[str, object],
    *,
    session_id: str | None,
    response_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    label: str,
) -> dict[str, object]:
    if response_path.exists():
        response_path.unlink()

    prompt = str(request.get("prompt") or "")
    command = build_command(request, response_path, session_id)
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )

    append_text(stdout_path, f"[{label}] stdout", completed.stdout)
    append_text(stderr_path, f"[{label}] stderr", completed.stderr)

    thread_id, parsed_message = parse_json_events(completed.stdout)
    response_text = None
    if response_path.exists():
        response_text = response_path.read_text(encoding="utf-8").strip() or None
    if not response_text:
        response_text = parsed_message

    return {
        "returncode": completed.returncode,
        "thread_id": thread_id,
        "response_text": response_text,
        "command": command,
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    task_dir = Path(args.task_dir).resolve()
    task_dir.mkdir(parents=True, exist_ok=True)
    request = read_request(task_dir)

    stdout_path = task_dir / "codex.stdout.jsonl"
    stderr_path = task_dir / "codex.stderr.log"
    response_path = task_dir / "assistant.txt"
    result_path = task_dir / "result.json"
    status_path = task_dir / "status.json"
    if stdout_path.exists():
        stdout_path.unlink()
    if stderr_path.exists():
        stderr_path.unlink()

    started_at = str(request.get("started_at") or utc_now())
    previous_session_id = str(request.get("session_id") or "").strip() or None
    reset_session = False
    record_id = str(request.get("task_id") or task_dir.name)
    attachments = {
        "status": status_path,
        "request": task_dir / "request.json",
        "result": result_path,
        "response": response_path,
        "stdout": stdout_path,
        "stderr": stderr_path,
    }
    checkpoints = [
        build_checkpoint(
            "queued",
            status="queued",
            stage="queued",
            timestamp=started_at,
            message="Chat wrapper started.",
        )
    ]
    write_status(
        status_path,
        build_runtime_status(
            record_id=record_id,
            entity_kind="chat-turn",
            status="queued",
            stage="queued",
            project_id=_optional_text(request.get("project_id")),
            project_title=_optional_text(request.get("project_title")),
            project_root=_optional_text(request.get("project_root")),
            work_id=_optional_text(request.get("work_id")),
            work_title=_optional_text(request.get("work_title")),
            profile=_optional_text(request.get("profile")),
            action="chat",
            started_at=started_at,
            summary="Chat turn queued for Codex.",
            checkpoints=checkpoints,
            attachments=build_attachments(attachments),
        ),
    )

    try:
        attempt_name = "resume-attempted" if previous_session_id else "start-attempted"
        attempt_message = "Resuming prior Codex session." if previous_session_id else "Starting new Codex session."
        checkpoints.append(
            build_checkpoint(
                attempt_name,
                status="running",
                stage="executing",
                timestamp=utc_now(),
                message=attempt_message,
            )
        )
        write_status(
            status_path,
            build_runtime_status(
                record_id=record_id,
                entity_kind="chat-turn",
                status="running",
                stage="executing",
                project_id=_optional_text(request.get("project_id")),
                project_title=_optional_text(request.get("project_title")),
                project_root=_optional_text(request.get("project_root")),
                work_id=_optional_text(request.get("work_id")),
                work_title=_optional_text(request.get("work_title")),
                profile=_optional_text(request.get("profile")),
                action="chat",
                started_at=started_at,
                summary=attempt_message,
                checkpoints=checkpoints,
                attachments=build_attachments(attachments),
            ),
        )

        attempt = run_attempt(
            request,
            session_id=previous_session_id,
            response_path=response_path,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            label="resume" if previous_session_id else "start",
        )

        if attempt["returncode"] != 0 and previous_session_id:
            reset_session = True
            resume_failure = build_failure(
                "codex",
                "resume-session-failed",
                "Codex resume attempt failed; restarting with a fresh session.",
                retryable=True,
            )
            checkpoints.append(
                build_checkpoint(
                    "resume-failed",
                    status="running",
                    stage="restarting",
                    timestamp=utc_now(),
                    message="Resume attempt failed; falling back to a fresh session.",
                    failure=resume_failure,
                )
            )
            checkpoints.append(
                build_checkpoint(
                    "restart-after-resume-failure",
                    status="running",
                    stage="restarting",
                    timestamp=utc_now(),
                    message="Launching a fresh Codex session after resume failure.",
                )
            )
            write_status(
                status_path,
                build_runtime_status(
                    record_id=record_id,
                    entity_kind="chat-turn",
                    status="running",
                    stage="restarting",
                    project_id=_optional_text(request.get("project_id")),
                    project_title=_optional_text(request.get("project_title")),
                    project_root=_optional_text(request.get("project_root")),
                    work_id=_optional_text(request.get("work_id")),
                    work_title=_optional_text(request.get("work_title")),
                    profile=_optional_text(request.get("profile")),
                    action="chat",
                    started_at=started_at,
                    summary="Resume failed; starting a fresh session.",
                    checkpoints=checkpoints,
                    attachments=build_attachments(attachments),
                ),
            )
            attempt = run_attempt(
                request,
                session_id=None,
                response_path=response_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                label="restart-after-resume-failure",
            )

        finished_at = utc_now()
        final_failure = None
        final_status = "succeeded" if int(attempt["returncode"]) == 0 else "failed"
        final_message = attempt.get("response_text") or "Chat turn completed."
        if int(attempt["returncode"]) != 0:
            final_failure = build_failure(
                "codex",
                "codex-exit-nonzero",
                "Codex CLI завершился с ошибкой. Смотри stderr log.",
                retryable=True,
                details={"returncode": int(attempt["returncode"])},
            )
            final_message = "Codex CLI завершился с ошибкой. Смотри stderr log."

        checkpoints.append(
            build_checkpoint(
                "response-finished",
                status=final_status,
                stage="completed" if final_status == "succeeded" else "failed",
                timestamp=finished_at,
                message=final_message,
                failure=final_failure,
            )
        )
        payload: dict[str, object] = {
            "started_at": started_at,
            "finished_at": finished_at,
            "returncode": int(attempt["returncode"]),
            "status": "success" if int(attempt["returncode"]) == 0 else "failed",
            "thread_id": attempt.get("thread_id") or previous_session_id,
            "response_text": attempt.get("response_text"),
            "response_path": str(response_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "reset_session": reset_session,
            "command": attempt.get("command"),
        }
        if int(attempt["returncode"]) != 0:
            payload["error"] = "Codex CLI завершился с ошибкой. Смотри stderr log."
        write_json(result_path, payload)
        write_status(
            status_path,
            build_runtime_status(
                record_id=record_id,
                entity_kind="chat-turn",
                status=final_status,
                stage="completed" if final_status == "succeeded" else "failed",
                project_id=_optional_text(request.get("project_id")),
                project_title=_optional_text(request.get("project_title")),
                project_root=_optional_text(request.get("project_root")),
                work_id=_optional_text(request.get("work_id")),
                work_title=_optional_text(request.get("work_title")),
                profile=_optional_text(request.get("profile")),
                action="chat",
                started_at=started_at,
                finished_at=finished_at,
                summary=final_message,
                failure=final_failure,
                checkpoints=checkpoints,
                attachments=build_attachments(attachments),
            ),
        )
        return int(attempt["returncode"])
    except Exception as exc:
        finished_at = utc_now()
        failure = build_failure("runtime", "chat-wrapper-exception", str(exc), retryable=False)
        checkpoints.append(
            build_checkpoint(
                "wrapper-failed",
                status="failed",
                stage="failed",
                timestamp=finished_at,
                message=str(exc),
                failure=failure,
            )
        )
        payload = {
            "started_at": started_at,
            "finished_at": finished_at,
            "returncode": 1,
            "status": "failed",
            "thread_id": previous_session_id,
            "response_text": None,
            "response_path": str(response_path),
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "reset_session": reset_session,
            "command": None,
            "error": str(exc),
        }
        write_json(result_path, payload)
        write_status(
            status_path,
            build_runtime_status(
                record_id=record_id,
                entity_kind="chat-turn",
                status="failed",
                stage="failed",
                project_id=_optional_text(request.get("project_id")),
                project_title=_optional_text(request.get("project_title")),
                project_root=_optional_text(request.get("project_root")),
                work_id=_optional_text(request.get("work_id")),
                work_title=_optional_text(request.get("work_title")),
                profile=_optional_text(request.get("profile")),
                action="chat",
                started_at=started_at,
                finished_at=finished_at,
                summary=str(exc),
                failure=failure,
                checkpoints=checkpoints,
                attachments=build_attachments(attachments),
            ),
        )
        return 1


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
