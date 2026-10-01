"""Fill honesty and account replay: the numbers the whole contest rests on."""

from __future__ import annotations

import pytest

from momentum.lab.broker import Equity, NoMarketError, Option, Order, fill_equity, fill_option
from momentum.lab.config import START_CAPITAL
from momentum.lab.portfolio import Account, replay

from tests.lab_fixtures import make_market

SPY = Equity("SPY")
PUT = Option("SPY", "2026-11-06", 95.0, "P")


def test_equity_buys_fill_above_close_and_sells_below():
    bar = make_market(closes=[100.0] * 120).bars.iloc[-1]
    buy = fill_equity(Order(SPY, qty=10), bar)
    sell = fill_equity(Order(SPY, qty=-10), bar)
    assert buy.price > 100.0 > sell.price  # slippage always against you
    assert buy.cash_delta == pytest.approx(-buy.price * 10)


def test_option_buys_at_ask_sells_at_bid_with_commission():
    buy = fill_option(Order(PUT, qty=1), bid=1.00, ask=1.10)
    sell = fill_option(Order(PUT, qty=-1), bid=1.00, ask=1.10)
    assert buy.price == 1.10 and sell.price == 1.00
    # Short 1 put at the bid: collect 100.00 premium minus 0.65 commission.
    assert sell.cash_delta == pytest.approx(100.0 - 0.65)
    # Friction = half-spread (5.00) + commission per contract.
    assert sell.friction == pytest.approx(5.0 + 0.65)


def test_one_sided_markets_are_rejected_not_invented():
    with pytest.raises(NoMarketError):
        fill_option(Order(PUT, qty=-1), bid=0.0, ask=1.10)


def test_replay_reconstructs_cash_positions_and_closes_them():
    records = [
        {"kind": "fill", "method": "m", "instrument": SPY.key, "qty": 10,
         "price": 100.0, "cash_delta": -1000.0, "friction": 0.2},
        {"kind": "fill", "method": "m", "instrument": SPY.key, "qty": -10,
         "price": 110.0, "cash_delta": 1100.0, "friction": 0.2},
    ]
    account = replay(records, ["m"])["m"]
    assert account.cash == pytest.approx(START_CAPITAL + 100.0)
    assert account.positions == {}
    assert account.n_fills == 2


def test_short_put_settlement_charges_intrinsic():
    records = [
        {"kind": "fill", "method": "m", "instrument": PUT.key, "qty": -1,
         "price": 1.5, "cash_delta": 149.35, "friction": 5.65},
        {"kind": "settle", "method": "m", "instrument": PUT.key, "intrinsic": 3.0},
    ]
    account = replay(records, ["m"])["m"]
    # Short qty -1 settling at intrinsic 3.0 costs 300.
    assert account.cash == pytest.approx(START_CAPITAL + 149.35 - 300.0)
    assert account.positions == {}


def test_value_marks_options_at_the_hundred_multiplier():
    account = Account()
    account.apply_fill(PUT.key, -1, 1.5, 149.35, 5.65)
    value = account.value({PUT.key: 2.0})
    assert value == pytest.approx(START_CAPITAL + 149.35 - 200.0)


def test_unquoted_position_is_carried_at_entry_not_zero():
    account = Account()
    account.apply_fill(PUT.key, -1, 1.5, 149.35, 5.65)
    # No mark available: carrying at entry keeps the curve sane, not flattering.
    assert account.value({}) == pytest.approx(START_CAPITAL + 149.35 - 150.0)
