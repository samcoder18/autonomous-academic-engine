from __future__ import annotations

from pathlib import Path
import argparse
import shlex
import subprocess
import sys

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
    started_at = utc_now()

    return_code = 1
    error_text = None

    with log_path.open("a", encoding="utf-8") as log_handle:
        log_handle.write(f"[{started_at}] Running command:\n")
        log_handle.write(f"{shlex.join(args.command)}\n\n")
        log_handle.flush()
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
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
