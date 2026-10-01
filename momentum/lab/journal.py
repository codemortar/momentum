"""Append-only JSONL journal; the report is a pure function of it."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import JOURNAL


def append(record: dict, path: Path = JOURNAL) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = {"ts": datetime.now(timezone.utc).isoformat(), **record}
    with path.open("a") as f:
        f.write(json.dumps(stamped) + "\n")


def read_all(path: Path = JOURNAL) -> list[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def last_mark_date(records: list[dict]) -> str | None:
    dates = [r["date"] for r in records if r.get("kind") == "mark"]
    return max(dates) if dates else None
