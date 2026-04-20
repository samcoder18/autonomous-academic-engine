from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_LEGACY_TIMESTAMP_PATTERN = re.compile(r"\d{8}-\d{6}")


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def parse_datetime(raw: str | None) -> datetime:
    if not raw:
        return datetime.fromtimestamp(0, tz=UTC)
    if _LEGACY_TIMESTAMP_PATTERN.fullmatch(raw):
        return datetime.strptime(raw, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.fromtimestamp(0, tz=UTC)


def shorten_text(value: str | None, limit: int = 140) -> str:
    clean = re.sub(r"\s+", " ", (value or "").strip())
    if not clean:
        return ""
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"


def split_message(text: str, limit: int = 3500) -> list[str]:
    clean = text.strip()
    if not clean:
        return []
    if len(clean) <= limit:
        return [clean]

    chunks: list[str] = []
    current = ""
    for block in clean.split("\n\n"):
        candidate = block if not current else f"{current}\n\n{block}"
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(block) > limit:
            chunks.append(block[:limit].rstrip())
            block = block[limit:].lstrip()
        current = block
    if current:
        chunks.append(current)
    return chunks or [clean[:limit]]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_text(path: Path, header: str, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(header)
        handle.write("\n")
        handle.write(content)
        if content and not content.endswith("\n"):
            handle.write("\n")
        handle.write("\n")


def resolve_executable(
    configured: str | None,
    default_name: str,
    *,
    extra_candidates: Sequence[str] = (),
) -> str | None:
    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.append(default_name)
    candidates.extend(extra_candidates)

    seen: set[str] = set()
    for raw_candidate in candidates:
        candidate = str(raw_candidate or "").strip()
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)

        direct = Path(candidate).expanduser()
        if direct.is_file() and os.access(direct, os.X_OK):
            return str(direct.resolve())

        resolved = shutil.which(candidate)
        if resolved:
            path = Path(resolved).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path.resolve())
    return None
