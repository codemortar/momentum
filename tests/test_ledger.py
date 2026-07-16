"""Ledger logic: recording is idempotent, only real unanswered trades are asked
about, and the strategy-vs-realized curves match answers known by construction.
All state goes to tmp_path; no network, as ever.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import config, ledger
from momentum.config import BONDS, EQUITIES_US, get_universe
from momentum.ledger import LedgerEntry
from momentum.signal import SignalReport
from tests.conftest import frame


@pytest.fixture
def uk(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return get_universe("uk")


def _report(strategy: str, role: str, as_of: str) -> SignalReport:
    return SignalReport(strategy=strategy, role=role, ticker=f"T-{role}", as_of=as_of)


# --- Recording & confirmation --------------------------------------------------

def test_record_is_idempotent_within_a_month(uk):
    reports = [_report("ma_200", EQUITIES_US, "2026-06-30")]
    assert ledger.record(uk, reports) == 1
    assert ledger.record(uk, reports) == 0  # same month again: no duplicates
    assert len(ledger.load_ledger(uk)) == 1


def test_set_confirmation_updates_the_latest_entry(uk):
    ledger.record(uk, [_report("ma_200", EQUITIES_US, "2026-05-29")])
    ledger.record(uk, [_report("ma_200", BONDS, "2026-06-30")])
    ledger.set_confirmation(uk, "ma_200", True)
    entries = ledger.load_ledger(uk)
    by_as_of = {e.as_of: e.confirmed for e in entries}
    assert by_as_of == {"2026-05-29": None, "2026-06-30": True}


def test_pending_only_flags_unanswered_actual_changes():
    entries = [
        # First-ever entry: assumed executed, never pending.
        LedgerEntry("2026-04-30", "a", EQUITIES_US, "T", None),
        # No-change month: nothing to ask.
        LedgerEntry("2026-04-30", "b", EQUITIES_US, "T", None),
        LedgerEntry("2026-05-29", "b", EQUITIES_US, "T", None),
        # A real change, unanswered: pending.
        LedgerEntry("2026-04-30", "c", EQUITIES_US, "T", None),
        LedgerEntry("2026-05-29", "c", BONDS, "T", None),
        # A real change, already answered: not pending.
        LedgerEntry("2026-04-30", "d", EQUITIES_US, "T", None),
        LedgerEntry("2026-05-29", "d", BONDS, "T", False),
    ]
    assert [e.strategy for e in ledger.pending_changes(entries)] == ["c"]


# --- Curves ---------------------------------------------------------------------

def test_declined_trade_keeps_previous_holding_in_realized_curve():
    # Equities rise geometrically, bonds stay flat. The strategy recommends
    # equities, then a switch to bonds which the user declines. So: the strategy
    # path is flat after the switch, while the realized path keeps riding
    # equities. Exact end values are known by construction.
    n = 100
    eq = 100 * (1.001 ** np.arange(n))
    prices = frame({EQUITIES_US: eq, BONDS: np.ones(n)})
    idx = prices.index
    history = [
        LedgerEntry(idx[10].date().isoformat(), "s", EQUITIES_US, "T", None),
        LedgerEntry(idx[50].date().isoformat(), "s", BONDS, "T", False),
    ]
    ideal, realized = ledger.strategy_and_realized_curves(prices, history)
    # Both curves start at 1.0 on the execution day AFTER the first as_of (t+1).
    assert ideal.index[0] == idx[11]
    # Strategy path: equities from close of day 11 to close of day 51, flat after.
    assert np.isclose(ideal.iloc[-1], eq[51] / eq[11])
    # Realized path: never left equities.
    assert np.isclose(realized.iloc[-1], eq[n - 1] / eq[11])


def test_confirmed_trade_matches_strategy_path():
    n = 100
    eq = 100 * (1.001 ** np.arange(n))
    prices = frame({EQUITIES_US: eq, BONDS: np.ones(n)})
    idx = prices.index
    history = [
        LedgerEntry(idx[10].date().isoformat(), "s", EQUITIES_US, "T", None),
        LedgerEntry(idx[50].date().isoformat(), "s", BONDS, "T", True),
    ]
    ideal, realized = ledger.strategy_and_realized_curves(prices, history)
    pd.testing.assert_series_equal(ideal, realized)


def test_unexecutable_recommendation_is_skipped():
    # A recommendation on the very last data day has no t+1 to execute on —
    # the same rule the backtest enforces.
    n = 30
    prices = frame({EQUITIES_US: np.ones(n)})
    last = prices.index[-1].date().isoformat()
    assert ledger.strategy_and_realized_curves(
        prices, [LedgerEntry(last, "s", EQUITIES_US, "T", None)]
    ) is None
