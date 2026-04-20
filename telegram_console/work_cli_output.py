from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any


def print_json_payload(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def print_payload(
    payload: dict[str, Any],
    *,
    as_json: bool,
    formatter: Callable[[dict[str, Any]], str] | None = None,
) -> None:
    if as_json:
        print_json_payload(payload)
        return
    if formatter is not None:
        print(formatter(payload))
        return
    print_json_payload(payload)


def cli_error_payload(
    *,
    kind: str,
    message: str,
    stop_reason: str,
    status: str = "blocked",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": kind,
        "status": status,
        "stop_reason": stop_reason,
        "message": message,
        "readiness_claim": "none",
    }
    if details:
        payload["details"] = details
    return payload


def emit_cli_error(
    message: str,
    *,
    as_json: bool,
    kind: str,
    stop_reason: str,
    status: str = "blocked",
    exit_code: int = 1,
    details: dict[str, Any] | None = None,
) -> int:
    if as_json:
        print_json_payload(
            cli_error_payload(
                kind=kind,
                message=message,
                stop_reason=stop_reason,
                status=status,
                details=details,
            )
        )
    else:
        print(message, file=sys.stderr)
    return exit_code
