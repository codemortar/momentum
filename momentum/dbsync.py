"""Mirror irreplaceable state/ files into Postgres. Files stay the source of
truth; writes are idempotent, so a missed sync is caught up by the next one."""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd

from . import config
from .lab import config as lab_config

URL_VAR = "MOMENTUM_DATABASE_URL"

SCHEMA = """
create table if not exists lab_journal (
    line_hash text primary key,
    ts timestamptz,
    kind text not null,
    bar_date date,
    method text,
    record jsonb not null
);
create table if not exists lab_bars (
    symbol text not null,
    bar_date date not null,
    open double precision,
    close double precision,
    currency text,
    primary key (symbol, bar_date)
);
create table if not exists lab_chains (
    symbol text not null,
    bar_date date not null,
    expiry date not null,
    strike double precision not null,
    opt_right char(1) not null,
    bid double precision,
    ask double precision,
    primary key (symbol, bar_date, expiry, strike, opt_right)
);
create table if not exists state_documents (
    name text primary key,
    content jsonb not null,
    updated_at timestamptz not null default now()
);
"""


@dataclass
class Snapshot:
    symbol: str
    bar_date: str
    bar: tuple[float, float, str]          # open, close, currency
    chain: list[tuple]                     # (expiry, strike, right, bid, ask)


class Store(Protocol):
    def insert_journal(self, rows: list[tuple]) -> int: ...
    def synced_snapshots(self) -> set[tuple[str, str]]: ...
    def insert_snapshot(self, snap: Snapshot) -> None: ...
    def upsert_documents(self, docs: list[tuple[str, str]]) -> int: ...


# --- reading state/ -------------------------------------------------------------

def journal_rows(path: Path) -> list[tuple]:
    """(hash, ts, kind, bar_date, method, record_json) per journal line."""
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        rows.append((
            hashlib.sha256(line.encode()).hexdigest(),
            record.get("ts"), record["kind"], record.get("date"),
            record.get("method"), line,
        ))
    return rows


def _num(value) -> float | None:
    value = float(value)
    return None if math.isnan(value) else value


def saved_snapshots(root: Path) -> list[tuple[str, str]]:
    """(symbol, bar_date) for every complete snapshot on disk."""
    if not root.exists():
        return []
    return sorted(
        (bar.name.removesuffix("_bar.csv"), day.name)
        for day in root.iterdir() if day.is_dir()
        for bar in day.glob("*_bar.csv")
    )


def read_snapshot(root: Path, symbol: str, bar_date: str) -> Snapshot:
    folder = root / bar_date
    bar = pd.read_csv(folder / f"{symbol}_bar.csv").iloc[0]
    chain_path = folder / f"{symbol}_chain.csv.gz"
    chain: list[tuple] = []
    if chain_path.exists():
        frame = pd.read_csv(chain_path, dtype={"expiry": str})
        chain = [
            (r.expiry, float(r.strike), r.right, _num(r.bid), _num(r.ask))
            for r in frame.itertuples(index=False)
        ]
    return Snapshot(symbol, bar_date, (_num(bar["open"]), _num(bar["close"]), str(bar["currency"])), chain)


def state_documents(state_dir: Path) -> list[tuple[str, str]]:
    return [(p.name, p.read_text()) for p in sorted(state_dir.glob("*.json"))]


# --- the sync ----------------------------------------------------------------------

@dataclass
class Summary:
    journal_new: int = 0
    snapshots_new: list[tuple[str, str]] = field(default_factory=list)
    documents_changed: int = 0


def sync(store: Store, state_dir: Path | None = None, snapshot_dir: Path | None = None,
         journal_path: Path | None = None) -> Summary:
    state_dir = state_dir or config.STATE_DIR
    snapshot_dir = snapshot_dir or lab_config.SNAPSHOT_DIR
    journal_path = journal_path or lab_config.JOURNAL

    summary = Summary()
    summary.journal_new = store.insert_journal(journal_rows(journal_path))
    done = store.synced_snapshots()
    for symbol, bar_date in saved_snapshots(snapshot_dir):
        if (symbol, bar_date) not in done:
            store.insert_snapshot(read_snapshot(snapshot_dir, symbol, bar_date))
            summary.snapshots_new.append((symbol, bar_date))
    summary.documents_changed = store.upsert_documents(state_documents(state_dir))
    return summary


class PostgresStore:
    def __init__(self, url: str):
        import psycopg

        # Autocommit, so each `transaction()` block truly commits; otherwise a bare read
        # opens an implicit transaction and later blocks become never-committed savepoints.
        self.conn = psycopg.connect(url, connect_timeout=15, autocommit=True)
        with self.conn.transaction():
            self.conn.execute(SCHEMA)

    def insert_journal(self, rows: list[tuple]) -> int:
        with self.conn.transaction(), self.conn.cursor() as cur:
            before = cur.execute("select count(*) from lab_journal").fetchone()[0]
            cur.executemany(
                "insert into lab_journal values (%s, %s, %s, %s, %s, %s::jsonb) "
                "on conflict (line_hash) do nothing",
                rows,
            )
            return cur.execute("select count(*) from lab_journal").fetchone()[0] - before

    def synced_snapshots(self) -> set[tuple[str, str]]:
        rows = self.conn.execute("select symbol, bar_date::text from lab_bars").fetchall()
        return {(s, d) for s, d in rows}

    def insert_snapshot(self, snap: Snapshot) -> None:
        # Bar is written last in the same transaction, so it marks a complete snapshot.
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(
                "insert into lab_chains values (%s, %s, %s, %s, %s, %s, %s) "
                "on conflict do nothing",
                [(snap.symbol, snap.bar_date, *row) for row in snap.chain],
            )
            cur.execute(
                "insert into lab_bars values (%s, %s, %s, %s, %s) on conflict do nothing",
                (snap.symbol, snap.bar_date, *snap.bar),
            )

    def upsert_documents(self, docs: list[tuple[str, str]]) -> int:
        changed = 0
        with self.conn.transaction(), self.conn.cursor() as cur:
            for name, text in docs:
                cur.execute(
                    "insert into state_documents (name, content) values (%s, %s::jsonb) "
                    "on conflict (name) do update set content = excluded.content, "
                    "updated_at = now() "
                    "where state_documents.content is distinct from excluded.content",
                    (name, text),
                )
                changed += cur.rowcount
        return changed

    def close(self) -> None:
        self.conn.close()


def run_sync() -> int:
    url = os.environ.get(URL_VAR)
    if not url:
        print(f"{URL_VAR} not set; nothing synced (state/ files are unaffected).")
        return 0
    import psycopg

    try:
        store = PostgresStore(url)
    except psycopg.errors.ConnectionTimeout:
        print("Sync: database connection timed out. On a managed cluster this usually "
              "means this server is not in its trusted sources.")
        return 1
    except psycopg.errors.InsufficientPrivilege:
        print("Sync: the database user cannot create tables. As the admin user, run in "
              "the momentum database: GRANT USAGE, CREATE ON SCHEMA public TO <user>;")
        return 1
    except psycopg.OperationalError as exc:
        print(f"Sync: could not connect ({str(exc).splitlines()[0]}).")
        return 1
    try:
        summary = sync(store)
    finally:
        store.close()
    print(f"Synced: {summary.journal_new} journal lines, "
          f"{len(summary.snapshots_new)} snapshots, "
          f"{summary.documents_changed} state documents changed.")
    return 0
