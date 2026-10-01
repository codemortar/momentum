"""State mirroring: offline logic always; real Postgres only when
MOMENTUM_TEST_DATABASE_URL points at a disposable database."""

from __future__ import annotations

import json
import os

import pytest

from momentum import dbsync
from momentum.lab import config as lab_config, journal, snapshots
from tests.lab_fixtures import make_market, put_row


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(lab_config, "JOURNAL", tmp_path / "lab_journal.jsonl")
    monkeypatch.setattr(lab_config, "SNAPSHOT_DIR", tmp_path / "lab_snapshots")
    journal.append({"kind": "mark", "date": "2026-10-02", "method": "lunar", "value": 1.0})
    journal.append({"kind": "mark", "date": "2026-10-02", "method": "overnight", "value": 2.0})
    snapshots.save(make_market(date="2026-10-02", chain_rows=[
        put_row(95.0, 1.2, 1.3, "2026-11-06"),
        put_row(90.0, float("nan"), 0.7, "2026-11-06"),
    ]))
    snapshots.save(make_market(date="2026-10-02", symbol="ISF.L"))
    (tmp_path / "ledger_uk.json").write_text(json.dumps([{"strategy": "accel"}]))
    return tmp_path


class FakeStore:
    def __init__(self):
        self.journal, self.snaps, self.docs = {}, {}, {}

    def insert_journal(self, rows):
        new = [r for r in rows if r[0] not in self.journal]
        self.journal.update({r[0]: r for r in rows})
        return len(new)

    def synced_snapshots(self):
        return set(self.snaps)

    def insert_snapshot(self, snap):
        self.snaps[(snap.symbol, snap.bar_date)] = snap

    def upsert_documents(self, docs):
        changed = sum(1 for n, t in docs if self.docs.get(n) != t)
        self.docs.update(dict(docs))
        return changed


def test_first_sync_copies_everything(state):
    store = FakeStore()
    summary = dbsync.sync(store, state_dir=state)
    assert summary.journal_new == 2
    assert sorted(summary.snapshots_new) == [("ISF.L", "2026-10-02"), ("SPY", "2026-10-02")]
    assert summary.documents_changed == 1


def test_second_sync_is_a_no_op(state):
    store = FakeStore()
    dbsync.sync(store, state_dir=state)
    again = dbsync.sync(store, state_dir=state)
    assert (again.journal_new, again.snapshots_new, again.documents_changed) == (0, [], 0)


def test_a_missed_day_is_caught_up(state):
    store = FakeStore()
    dbsync.sync(store, state_dir=state)
    snapshots.save(make_market(date="2026-10-05"))
    journal.append({"kind": "mark", "date": "2026-10-05", "method": "lunar", "value": 3.0})
    later = dbsync.sync(store, state_dir=state)
    assert later.journal_new == 1 and later.snapshots_new == [("SPY", "2026-10-05")]


def test_missing_quotes_become_nulls_not_nans(state):
    snap = dbsync.read_snapshot(lab_config.SNAPSHOT_DIR, "SPY", "2026-10-02")
    bids = {row[1]: row[3] for row in snap.chain}
    assert bids[90.0] is None and bids[95.0] == 1.2


def test_identical_journal_lines_hash_identically(state):
    first = dbsync.journal_rows(lab_config.JOURNAL)
    second = dbsync.journal_rows(lab_config.JOURNAL)
    assert [r[0] for r in first] == [r[0] for r in second]
    assert len({r[0] for r in first}) == 2


def test_sync_without_a_url_does_nothing(monkeypatch, capsys):
    monkeypatch.delenv(dbsync.URL_VAR, raising=False)
    assert dbsync.run_sync() == 0
    assert "not set" in capsys.readouterr().out


@pytest.mark.skipif(not os.environ.get("MOMENTUM_TEST_DATABASE_URL"),
                    reason="set MOMENTUM_TEST_DATABASE_URL to a disposable Postgres database")
def test_round_trip_through_real_postgres(state):
    url = os.environ["MOMENTUM_TEST_DATABASE_URL"]
    store = dbsync.PostgresStore(url)
    try:
        store.conn.execute(
            "drop table if exists lab_journal, lab_bars, lab_chains, state_documents")
        store.close()
        store = dbsync.PostgresStore(url)  # recreate the schema from scratch
        first = dbsync.sync(store, state_dir=state)

        # Regression: writes must be committed, i.e. visible from a fresh connection.
        import psycopg
        with psycopg.connect(url) as other:
            assert other.execute("select count(*) from lab_bars").fetchone()[0] == 2
            assert other.execute("select count(*) from lab_journal").fetchone()[0] == 2
        again = dbsync.sync(store, state_dir=state)
        assert first.journal_new == 2 and len(first.snapshots_new) == 2
        assert (again.journal_new, again.snapshots_new, again.documents_changed) == (0, [], 0)

        q = store.conn.execute
        assert q("select count(*) from lab_chains").fetchone()[0] == 2
        assert q("select bid from lab_chains where strike = 90").fetchone()[0] is None
        assert q("select record->>'method' from lab_journal order by ts").fetchall() == [
            ("lunar",), ("overnight",)]
        assert q("select close from lab_bars where symbol = 'ISF.L'").fetchone()[0] == 100.0

        (state / "ledger_uk.json").write_text(json.dumps([{"strategy": "changed"}]))
        assert dbsync.sync(store, state_dir=state).documents_changed == 1
    finally:
        store.close()
