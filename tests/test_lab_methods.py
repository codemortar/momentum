"""Each pre-registered rule, checked against markets built to have one right
answer."""

from __future__ import annotations

from datetime import date

import pytest

from momentum.lab.broker import Equity, Option
from momentum.lab.methods import REGISTRY
from momentum.lab.methods.base import Context
from momentum.lab.methods.simple import lunar, moon_is_waxing, overnight, random_walk, sma_cross
from momentum.lab.methods.theta_puts import decide as theta_decide
from momentum.lab.portfolio import Account

from tests.lab_fixtures import make_market, put_row

SPY_KEY = Equity("SPY").key


def ctx(market, account=None, name="test") -> Context:
    return Context(market=market, account=account or Account(), name=name)


# --- theta_puts ---------------------------------------------------------------

def test_theta_sells_the_highest_strike_at_or_below_95pct():
    market = make_market(chain_rows=[
        put_row(93.0, 0.9, 1.0, "2026-11-05"),   # 35 dte
        put_row(95.0, 1.2, 1.3, "2026-11-05"),
        put_row(97.0, 1.8, 1.9, "2026-11-05"),   # above 95% of spot: too close
    ])
    orders = theta_decide(ctx(market))
    assert len(orders) == 1
    assert orders[0].qty == -1
    assert isinstance(orders[0].instrument, Option)
    assert orders[0].instrument.strike == 95.0


def test_theta_ignores_expiries_outside_the_window():
    market = make_market(chain_rows=[
        put_row(95.0, 1.0, 1.1, "2026-10-10"),   # 9 dte: too soon
        put_row(95.0, 2.0, 2.1, "2027-01-15"),   # 106 dte: too far
    ])
    assert theta_decide(ctx(market)) == []


def test_theta_requires_cash_security():
    market = make_market(chain_rows=[put_row(95.0, 1.2, 1.3, "2026-11-05")])
    poor = Account()
    poor.cash = 500.0  # cannot secure a 95-strike contract
    assert theta_decide(ctx(market, poor)) == []


def test_theta_takes_profit_at_80pct_of_premium():
    key = Option("SPY", "2026-11-05", 95.0, "P").key
    account = Account()
    account.apply_fill(key, -1, 1.50, 149.35, 5.65)
    cheap = make_market(chain_rows=[put_row(95.0, 0.25, 0.30, "2026-11-05")])
    rich = make_market(chain_rows=[put_row(95.0, 1.00, 1.10, "2026-11-05")])
    assert [o.qty for o in theta_decide(ctx(cheap, account))] == [1]   # mid 0.275 <= 0.30
    assert theta_decide(ctx(rich, account)) == []                      # still working


def test_theta_rolls_near_expiry_regardless_of_price():
    key = Option("SPY", "2026-10-05", 95.0, "P").key
    account = Account()
    account.apply_fill(key, -1, 1.50, 149.35, 5.65)
    market = make_market(date="2026-10-01",
                         chain_rows=[put_row(95.0, 2.0, 2.2, "2026-10-05")])
    assert [o.qty for o in theta_decide(ctx(market, account))] == [1]


# --- the equity rules -----------------------------------------------------------

def test_overnight_sells_at_open_and_rebuys_at_close():
    account = Account()
    account.apply_fill(SPY_KEY, 10, 100.0, -1000.0, 0.2)
    market = make_market(opens=[101.0] * 120, closes=[100.0] * 120)
    orders = overnight(ctx(market, account))
    assert [(o.qty > 0, o.at) for o in orders] == [(False, "open"), (True, "close")]


def test_sma_cross_is_long_in_an_uptrend_and_flat_in_a_downtrend():
    up = make_market(closes=list(range(50, 170)))
    down = make_market(closes=list(range(170, 50, -1)))
    assert sum(o.qty for o in sma_cross(ctx(up))) > 0
    assert sma_cross(ctx(down)) == []  # flat and holding nothing: no order


def test_random_walk_is_deterministic_per_date():
    market = make_market()
    first = [(o.instrument.key, o.qty) for o in random_walk(ctx(market))]
    second = [(o.instrument.key, o.qty) for o in random_walk(ctx(market))]
    assert first == second  # the coin cannot be re-flipped


def test_lunar_tracks_the_actual_moon():
    # 2000-01-06 was a new moon, so a week later the moon was waxing.
    assert moon_is_waxing(date(2000, 1, 13))
    assert not moon_is_waxing(date(2000, 1, 28))
    waxing_market = make_market(date="2000-01-13")
    assert sum(o.qty for o in lunar(ctx(waxing_market))) > 0


def test_every_method_survives_an_empty_market():
    bare = make_market(closes=[100.0] * 5)
    for method in REGISTRY.values():
        method.decide(ctx(bare))  # must not raise, orders optional


def test_registry_includes_control_and_placebo():
    assert {"random_walk", "lunar"} <= set(REGISTRY)
