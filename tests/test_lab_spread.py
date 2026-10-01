"""theta_spread rules, pilot-sized capital, and all-or-nothing multi-leg orders."""

from __future__ import annotations

import pytest

from momentum.lab import config, runner
from momentum.lab.broker import Option, Order
from momentum.lab.methods import REGISTRY
from momentum.lab.methods.base import Context, Method
from momentum.lab.methods.theta_spread import decide
from momentum.lab.portfolio import Account, replay
from tests.lab_fixtures import make_market, put_row

EXP = "2026-11-05"  # 35 days after the fixture date


def chain(short_bid=1.20, long_ask=0.60, with_long=True):
    rows = [put_row(95.0, short_bid, short_bid + 0.10, EXP), put_row(97.0, 1.9, 2.0, EXP)]
    if with_long:
        rows.append(put_row(90.0, long_ask - 0.10, long_ask, EXP))
    return rows


def pilot_account() -> Account:
    return Account(cash=config.PILOT_CAPITAL)


def test_opens_both_legs_five_dollars_apart():
    orders = decide(Context(market=make_market(chain_rows=chain()), account=pilot_account(), name="t"))
    legs = {o.instrument.strike: o.qty for o in orders}
    assert legs == {95.0: -1, 90.0: 1}       # short at <=95% of spot, long $5 below


def test_never_opens_a_naked_short_when_the_long_leg_is_missing():
    market = make_market(chain_rows=chain(with_long=False))
    assert decide(Context(market=market, account=pilot_account(), name="t")) == []


def test_skips_when_there_is_no_credit_to_collect():
    market = make_market(chain_rows=chain(short_bid=0.50, long_ask=0.60))
    assert decide(Context(market=market, account=pilot_account(), name="t")) == []


def test_max_loss_must_fit_in_cash():
    poor = Account(cash=300.0)  # (5 - 0.60) * 100 + fees > 300
    assert decide(Context(market=make_market(chain_rows=chain()), account=poor, name="t")) == []


def _holding_spread(short_px=1.20, long_px=0.60) -> Account:
    account = pilot_account()
    short, long = Option("SPY", EXP, 95.0, "P"), Option("SPY", EXP, 90.0, "P")
    account.apply_fill(short.key, -1, short_px, short_px * 100 - 0.65, 5.65)
    account.apply_fill(long.key, 1, long_px, -long_px * 100 - 0.65, 5.65)
    return account


def test_closes_both_legs_at_80pct_of_the_credit():
    # Credit 0.60; spread mid 0.10 <= 0.12 -> take profit.
    cheap = make_market(chain_rows=[put_row(95.0, 0.15, 0.17, EXP), put_row(90.0, 0.05, 0.07, EXP)])
    orders = decide(Context(market=cheap, account=_holding_spread(), name="t"))
    assert {o.instrument.strike: o.qty for o in orders} == {95.0: 1, 90.0: -1}


def test_holds_while_the_trade_is_still_working():
    working = make_market(chain_rows=[put_row(95.0, 0.80, 0.90, EXP), put_row(90.0, 0.30, 0.40, EXP)])
    assert decide(Context(market=working, account=_holding_spread(), name="t")) == []


def test_is_sized_to_the_pilot():
    assert REGISTRY["theta_spread"].capital == config.PILOT_CAPITAL
    accounts = replay([], list(REGISTRY), {n: m.capital for n, m in REGISTRY.items()})
    assert accounts["theta_spread"].cash == config.PILOT_CAPITAL
    assert accounts["theta_puts"].cash == config.START_CAPITAL


@pytest.fixture
def offline(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "JOURNAL", tmp_path / "lab.jsonl")
    monkeypatch.setattr(config, "SNAPSHOT_DIR", tmp_path / "snapshots")


def test_a_combo_with_one_unquoted_leg_trades_nothing(offline, monkeypatch):
    # The short leg is quoted, the long leg is not: filling only the short would
    # leave exactly the uncapped position the spread exists to avoid.
    half = lambda ctx: [Order(Option("SPY", EXP, 95.0, "P"), qty=-1),
                        Order(Option("SPY", EXP, 90.0, "P"), qty=1)]
    monkeypatch.setattr(runner, "REGISTRY", {"half": Method("half", "test", half)})
    monkeypatch.setattr(runner, "fetch_market",
                        lambda s: make_market(date="2026-10-02", symbol=s, chain_rows=chain(with_long=False)))
    runner.run()
    from momentum.lab import journal

    assert [r for r in journal.read_all() if r["kind"] == "fill"] == []
