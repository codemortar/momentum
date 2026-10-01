"""Append-only JSONL journal; the report is a pure function of it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config


def append(record: dict, path: Path | None = None) -> None:
    path = path or config.JOURNAL
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with path.open("a") as f:
        f.write(json.dumps(stamped) + "\n")


def read_all(path: Path | None = None) -> list[dict]:
    path = path or config.JOURNAL
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]
