#!/usr/bin/env python3
"""Minimal .env loader (no external dependency)."""

from __future__ import annotations

import os
from pathlib import Path


def _parse_line(line: str) -> tuple[str, str] | None:
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None
    if raw.startswith("export "):
        raw = raw[len("export ") :].strip()
    if "=" not in raw:
        return None
    key, value = raw.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return key, value


def load_dotenv() -> str | None:
    """Load env vars from .env if present. Existing OS env vars win."""
    explicit = os.getenv("WW_ENV_FILE", "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(Path.cwd() / ".env")

    # Also try project root when script runs from a different cwd.
    here = Path(__file__).resolve()
    for parent in [here.parent, *here.parents]:
        p = parent / ".env"
        if p not in candidates:
            candidates.append(p)
        if parent.name == "Documents":
            break

    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_line(line)
            if not parsed:
                continue
            key, value = parsed
            os.environ.setdefault(key, value)
        return str(path)
    return None

