"""Multi-market behaviour: FTSE methods, per-market costs, and the stale-bar guard."""

from __future__ import annotations

import hashlib

import pytest

from momentum.lab import config, runner
from momentum.lab.broker import Equity, Order, fill_equity
from momentum.lab.methods import REGISTRY
from momentum.lab.methods.base import Context
from momentum.lab.methods.simple import random_walk
from momentum.lab.portfolio import Account
from tests.lab_fixtures import make_market


def test_london_fills_cost_more_than_spy():
    bar = make_market().bars.iloc[-1]
    spy = fill_equity(Order(Equity("SPY"), qty=100), bar)
    ftse = fill_equity(Order(Equity(config.FTSE), qty=100), bar)
    assert ftse.friction > spy.friction


def test_ftse_methods_trade_the_ftse_fund():
    market = make_market(closes=list(range(50, 170)), symbol=config.FTSE)
    for name in ("overnight_ftse", "sma_cross_ftse"):
        method = REGISTRY[name]
        assert method.underlying == config.FTSE
        orders = method.decide(Context(market=market, account=Account(), name=name))
        assert orders and all(o.instrument.symbol == config.FTSE for o in orders)


def test_spy_control_seed_is_unchanged_by_the_ftse_addition():
    # Pre-registration: the original control's coin must not have been re-rolled.
    market = make_market(date="2026-10-05")
    flip = hashlib.sha256(b"random_walk:2026-10-05").digest()[0] % 2 == 0
    orders = random_walk(Context(market=market, account=Account(), name="random_walk"))
    assert bool(orders) == flip


def test_each_market_has_its_own_coin():
    dates = [f"2026-10-{d:02d}" for d in range(1, 29)]
    spy = [hashlib.sha256(f"random_walk:{d}".encode()).digest()[0] % 2 for d in dates]
    ftse = [hashlib.sha256(f"random_walk_ftse:{d}".encode()).digest()[0] % 2 for d in dates]
    assert spy != ftse


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOURNAL", tmp_path / "lab.jsonl")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")
    bars = {"SPY": "2026-10-02", config.FTSE: "2026-10-02"}

    def fake_fetch(symbol):
        return make_market(date=bars[symbol], symbol=symbol)

    monkeypatch.setattr(runner, "fetch_market", fake_fetch)
    return bars


def _marks(name: str) -> list[str]:
    from momentum.lab import journal

    return [r["date"] for r in journal.read_all() if r["kind"] == "mark" and r["method"] == name]


def test_a_stale_bar_does_not_trade_twice(offline):
    runner.run()
    # Next day New York has a new bar but London's is missing (holiday or data gap).
    offline["SPY"] = "2026-10-05"
    runner.run()
    assert _marks("overnight") == ["2026-10-02", "2026-10-05"]
    assert _marks("overnight_ftse") == ["2026-10-02"]


def test_rerunning_the_same_day_changes_nothing(offline):
    runner.run()
    from momentum.lab import journal

    before = len(journal.read_all())
    runner.run()
    assert len(journal.read_all()) == before
