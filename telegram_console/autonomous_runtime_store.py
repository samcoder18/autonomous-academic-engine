from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .utils import utc_now

LOCK_KIND = "autonomous-daemon-lock"
STOP_REQUEST_KIND = "autonomous-daemon-stop-request"
INHERITED_LOCK_FD_ENV = "AUTONOMOUS_DAEMON_LOCK_FD"
INHERITED_LOCK_PATH_ENV = "AUTONOMOUS_DAEMON_LOCK_PATH"
INHERITED_LOCK_READY_FD_ENV = "AUTONOMOUS_DAEMON_LOCK_READY_FD"


@dataclass
class RuntimeLockHandle:
    fd: int
    owner_pid: int
    inherited: bool = False


_LOCK_HANDLES: dict[Path, RuntimeLockHandle] = {}


def autonomous_runtime_dir(root_dir: str | Path) -> Path:
    return Path(root_dir).resolve() / "output" / "telegram" / "runtime" / "autonomous"


def runtime_file_path(root_dir: str | Path, filename: str) -> Path:
    return autonomous_runtime_dir(root_dir) / filename


def runtime_lock_fd_path(metadata_path: Path) -> Path:
    return metadata_path.with_name(f"{metadata_path.name}.fd")


def read_json_payload(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def write_json_payload(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(path.parent)) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def remove_runtime_file(path: Path) -> None:
    if path.exists():
        path.unlink()


def acquire_runtime_lock(metadata_path: Path, *, owner_pid: int) -> dict[str, Any]:
    path = metadata_path.resolve()
    existing_handle = _LOCK_HANDLES.get(path)
    if existing_handle is not None:
        return {
            "acquired": existing_handle.owner_pid == owner_pid,
            "inherited": existing_handle.inherited,
            "existing_lock": read_json_payload(path),
        }

    inherited_fd = _take_inherited_lock_fd(path)
    if inherited_fd is not None:
        fcntl.flock(inherited_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _LOCK_HANDLES[path] = RuntimeLockHandle(fd=inherited_fd, owner_pid=owner_pid, inherited=True)
        return {
            "acquired": True,
            "inherited": True,
            "existing_lock": read_json_payload(path),
        }

    fd_path = runtime_lock_fd_path(path)
    fd_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(fd_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return {
            "acquired": False,
            "inherited": False,
            "existing_lock": read_json_payload(path),
        }
    _LOCK_HANDLES[path] = RuntimeLockHandle(fd=fd, owner_pid=owner_pid)
    return {
        "acquired": True,
        "inherited": False,
        "existing_lock": read_json_payload(path),
    }


def runtime_lock_fd(metadata_path: Path) -> int | None:
    handle = _LOCK_HANDLES.get(metadata_path.resolve())
    return handle.fd if handle is not None else None


def inherited_runtime_lock_env(metadata_path: Path, *, ready_fd: int) -> dict[str, str]:
    fd = runtime_lock_fd(metadata_path)
    if fd is None:
        raise RuntimeError(f"Runtime lock is not held: {metadata_path}")
    return {
        INHERITED_LOCK_FD_ENV: str(fd),
        INHERITED_LOCK_PATH_ENV: str(metadata_path.resolve()),
        INHERITED_LOCK_READY_FD_ENV: str(ready_fd),
    }


def detach_runtime_lock(metadata_path: Path) -> None:
    handle = _LOCK_HANDLES.pop(metadata_path.resolve(), None)
    if handle is not None:
        os.close(handle.fd)


def release_runtime_lock(metadata_path: Path, *, remove_metadata: bool = True) -> None:
    path = metadata_path.resolve()
    handle = _LOCK_HANDLES.pop(path, None)
    if handle is None:
        return
    try:
        if remove_metadata:
            remove_runtime_file(path)
        fcntl.flock(handle.fd, fcntl.LOCK_UN)
    finally:
        os.close(handle.fd)


def _take_inherited_lock_fd(metadata_path: Path) -> int | None:
    inherited_path = os.environ.get(INHERITED_LOCK_PATH_ENV)
    inherited_fd = os.environ.get(INHERITED_LOCK_FD_ENV)
    if inherited_path != str(metadata_path) or inherited_fd is None:
        return None
    try:
        fd = int(inherited_fd)
    except ValueError:
        return None

    ready_fd_raw = os.environ.pop(INHERITED_LOCK_READY_FD_ENV, None)
    if ready_fd_raw is not None:
        try:
            ready_fd = int(ready_fd_raw)
            if os.read(ready_fd, 1) != b"1":
                os.close(fd)
                raise RuntimeError("Runtime lock handoff was cancelled before child startup.")
        finally:
            try:
                os.close(int(ready_fd_raw))
            except (OSError, ValueError):
                pass
    os.environ.pop(INHERITED_LOCK_FD_ENV, None)
    os.environ.pop(INHERITED_LOCK_PATH_ENV, None)
    return fd


def build_lock_payload(
    root_dir: str | Path,
    owner_id: str,
    *,
    version: str,
    mode: str | None = None,
    pid: int | None = None,
    started_at: str | None = None,
    heartbeat_at: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = utc_now()
    payload: dict[str, Any] = {
        "kind": LOCK_KIND,
        "version": version,
        "work_id": owner_id,
        "mode": _optional_text(mode) or "autonomous-full",
        "root_dir": str(Path(root_dir).resolve()),
        "pid": pid or os.getpid(),
        "started_at": _optional_text(started_at) or now,
        "heartbeat_at": _optional_text(heartbeat_at) or now,
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def build_stop_request_payload(
    owner_id: str,
    *,
    version: str,
    reason: str,
    requested_at: str | None = None,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": STOP_REQUEST_KIND,
        "version": version,
        "work_id": owner_id,
        "reason": reason,
        "requested_at": _optional_text(requested_at) or utc_now(),
        "status": "stop-requested",
        "stop_reason": reason,
        "readiness_claim": "none",
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
