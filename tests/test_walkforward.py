"""Walk-forward analysis. The property that matters most: selection must use
only the in-sample window, so a candidate that only wins *after* the decision
point can never be chosen before it."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import walkforward
from momentum.config import BONDS, CASH, CostModel, EQUITIES_INTL, EQUITIES_US
from tests.conftest import frame


# --- Fold construction --------------------------------------------------------

def _index(years: int) -> pd.DatetimeIndex:
    return pd.bdate_range("2000-01-03", periods=252 * years)


def test_folds_roll_forward_without_overlapping_out_of_sample():
    folds = walkforward.make_folds(_index(10), train_years=5, test_years=1)
    assert folds, "expected several folds from ten years"
    for is_start, is_end, oos_end in folds:
        assert is_start < is_end < oos_end
        assert (is_end - is_start).days == pytest.approx(365 * 5, abs=3)
    # Each fold's out-of-sample window begins where the previous one ended.
    for (_, prev_end, prev_oos), (_, this_end, _) in zip(folds, folds[1:]):
        assert this_end == prev_oos


def test_no_folds_when_history_is_too_short():
    assert walkforward.make_folds(_index(3), train_years=5, test_years=1) == []


def test_walk_forward_refuses_to_report_on_too_little_history(trending_prices):
    with pytest.raises(ValueError, match="shorter than"):
        walkforward.walk_forward(
            trending_prices,
            walkforward.strategy_candidates(),
            CostModel(),
            train_years=20,
            test_years=1,
        )


# --- Selection uses only the past ----------------------------------------------

def _two_regime_prices(years=12, switch_year=6):
    """US equities win the first half; intl wins the second, by a wide margin.

    A selector that can see the future would pick the intl-favouring strategy
    from the start. One that cannot must keep choosing the US-favouring one
    until the regime has actually turned.
    """
    n = 252 * years
    switch = 252 * switch_year
    t = np.arange(n)
    us = 100 * (1.0007 ** t)
    us[switch:] = us[switch] * (1.00005 ** np.arange(n - switch))
    intl = 100 * (1.00005 ** t)
    intl[switch:] = intl[switch] * (1.0007 ** np.arange(n - switch))
    return frame({
        EQUITIES_US: us,
        EQUITIES_INTL: intl,
        BONDS: 100 * (1.0001 ** t),
        CASH: 100 * (1.00002 ** t),
    })


def test_selection_cannot_see_the_regime_change_before_it_happens():
    prices = _two_regime_prices()
    candidates = {
        "always_us": lambda p: {EQUITIES_US: 1.0},
        "always_intl": lambda p: {EQUITIES_INTL: 1.0},
    }
    result = walkforward.walk_forward(
        prices, candidates, CostModel(), train_years=3, test_years=1
    )
    picks = [f.chosen for f in result.folds]
    # The first fold trains entirely inside the US-winning regime.
    assert picks[0] == "always_us"
    # And by the end, after years of intl outperformance, it has switched.
    assert picks[-1] == "always_intl"


def test_walk_forward_curve_is_stitched_from_the_chosen_candidates():
    prices = _two_regime_prices()
    candidates = {
        "always_us": lambda p: {EQUITIES_US: 1.0},
        "always_intl": lambda p: {EQUITIES_INTL: 1.0},
    }
    result = walkforward.walk_forward(
        prices, candidates, CostModel(trade_cost_bps=0.0), train_years=3, test_years=1
    )
    # Compounding the per-fold out-of-sample returns must reproduce the curve.
    expected = np.prod([1.0 + f.oos_return for f in result.folds])
    assert np.isclose(result.equity_curve.iloc[-1], expected, rtol=1e-6)
    assert result.equity_curve.index[0] > result.folds[0].is_end


def test_churn_counts_changes_of_mind():
    prices = _two_regime_prices()
    candidates = {
        "always_us": lambda p: {EQUITIES_US: 1.0},
        "always_intl": lambda p: {EQUITIES_INTL: 1.0},
    }
    result = walkforward.walk_forward(
        prices, candidates, CostModel(), train_years=3, test_years=1
    )
    picks = [f.chosen for f in result.folds]
    assert result.churn == sum(1 for a, b in zip(picks, picks[1:]) if a != b)
    assert result.churn >= 1  # the regime turns, so it must change at least once


def test_a_single_candidate_walks_forward_to_itself():
    prices = _two_regime_prices()
    only = {"always_us": lambda p: {EQUITIES_US: 1.0}}
    result = walkforward.walk_forward(
        prices, only, CostModel(trade_cost_bps=0.0), train_years=3, test_years=1
    )
    assert result.churn == 0
    assert {f.chosen for f in result.folds} == {"always_us"}


# --- Candidate builders -----------------------------------------------------------

def test_buffer_candidates_include_the_unbuffered_baseline():
    candidates = walkforward.buffer_candidates("accel_momentum", margins_bps=(0, 300))
    assert set(candidates) == {"accel_momentum@0bps", "accel_momentum@300bps"}
    # The 0bps entry must be the plain strategy, not a wrapper around it.
    from momentum.strategies import accel_momentum
    assert candidates["accel_momentum@0bps"] is accel_momentum
