"""Snapshots: written once per market per bar, readable back exactly."""

from __future__ import annotations

import pytest

from momentum.lab import config, runner, snapshots
from tests.lab_fixtures import make_market, put_row


@pytest.fixture(autouse=True)
def tmp_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(config, "JOURNAL", tmp_path / "lab.jsonl")


def spy_with_chain(bar_date="2026-10-02"):
    return make_market(date=bar_date, chain_rows=[
        put_row(95.0, 1.20, 1.30, "2026-11-06"),
        put_row(90.0, 0.60, 0.70, "2026-11-06"),
    ])


def test_round_trip_keeps_quotes_exact():
    snapshots.save(spy_with_chain())
    bar, chain = snapshots.load("SPY", "2026-10-02")
    assert list(chain.strike) == [95.0, 90.0]
    assert list(chain.bid) == [1.20, 0.60] and list(chain.ask) == [1.30, 0.70]
    assert chain.expiry.iloc[0] == "2026-11-06"  # stays a string, not a parsed date
    assert bar.loc["2026-10-02", "close"] == 100.0


def test_a_snapshot_is_never_overwritten():
    assert snapshots.save(spy_with_chain()) is True
    later = make_market(date="2026-10-02", closes=[999.0] * 120)
    assert snapshots.save(later) is False
    bar, _ = snapshots.load("SPY", "2026-10-02")
    assert bar.loc["2026-10-02", "close"] == 100.0  # first write wins


def test_a_market_without_options_saves_its_bar_only():
    snapshots.save(make_market(date="2026-10-02", symbol=config.FTSE))
    bar, chain = snapshots.load(config.FTSE, "2026-10-02")
    assert len(bar) == 1 and chain.empty


def test_saved_dates_lists_complete_snapshots_in_order():
    for d in ("2026-10-05", "2026-10-02"):
        snapshots.save(spy_with_chain(d))
    assert snapshots.saved_dates("SPY") == ["2026-10-02", "2026-10-05"]
    assert snapshots.saved_dates(config.FTSE) == []


def test_the_daily_run_saves_every_market(monkeypatch):
    monkeypatch.setattr(runner, "fetch_market", lambda symbol: make_market(date="2026-10-02", symbol=symbol))
    runner.run()
    assert snapshots.saved_dates("SPY") == ["2026-10-02"]
    assert snapshots.saved_dates(config.FTSE) == ["2026-10-02"]
