"""Engine tests: cost accounting, buy-and-hold identity, and the two lookahead
guardrails (property test + execution lag)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import backtest
from momentum.config import BONDS, CostModel, EQUITIES_US
from momentum.strategies import STRATEGIES, buy_and_hold, ma_200


# --- M2: cost accounting & buy-and-hold identity -----------------------------

def test_buy_and_hold_zero_cost_equals_normalized_price(trending_prices):
    res = backtest.run(trending_prices, buy_and_hold, "bh", CostModel(trade_cost_bps=0.0))
    assert res.n_trades == 0
    curve = res.equity_curve
    eq = trending_prices[EQUITIES_US].loc[curve.index]
    normalized = eq / eq.iloc[0]
    assert np.allclose(curve.values, normalized.values)


def test_cost_charged_once_per_full_switch(flat_prices):
    # A strategy that holds equities, then flips to bonds on a chosen month, then
    # back to equities — a full round trip = two rebalances, each moving 100%.
    switch_dates = set()

    def flip(prices):
        # month index within the frame determines the holding, deterministically.
        month = (prices.index[-1].year, prices.index[-1].month)
        # Hold bonds only in exactly one month; equities otherwise.
        return {BONDS: 1.0} if month == flip.bond_month else {EQUITIES_US: 1.0}

    # Pick a bond month roughly in the middle of the flat series.
    flip.bond_month = (2001, 6)
    res = backtest.run(flat_prices, flip, "flip", CostModel(trade_cost_bps=10.0))

    # Out (equities->bonds) and back (bonds->equities) = exactly two trades.
    assert res.n_trades == 2
    # Flat prices, so the only thing that moves value is cost: (1-0.001)^2.
    expected = 1.0 * (1 - 0.001) * (1 - 0.001)
    assert np.isclose(res.equity_curve.iloc[-1], expected, rtol=1e-9)


def test_zero_turnover_rebalance_is_free(flat_prices):
    # buy_and_hold targets the same thing every month; no trade should ever fire.
    res = backtest.run(flat_prices, buy_and_hold, "bh", CostModel(trade_cost_bps=50.0))
    assert res.n_trades == 0
    assert res.total_cost == 0.0
    assert np.isclose(res.equity_curve.iloc[-1], 1.0)


# --- M3: lookahead guardrails ------------------------------------------------

@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_future_data_does_not_change_past_decisions(trending_prices, name):
    strategy = STRATEGIES[name]
    full = backtest.run(trending_prices, strategy, name, CostModel())
    trunc = backtest.run(trending_prices.iloc[:-100], strategy, name, CostModel())
    overlap = trunc.weights_history.index
    # Every decision made in the truncated run must be identical in the full run.
    pd.testing.assert_frame_equal(
        full.weights_history.loc[overlap], trunc.weights_history
    )


def test_execution_happens_the_day_after_the_decision(trending_prices):
    # Reconstruct the schedule the engine uses and assert the t -> t+1 lag holds.
    decisions, executions = backtest._decision_schedule(trending_prices, ma_200)
    index = trending_prices.index
    for decision_day in decisions:
        pos = index.get_loc(decision_day)
        exec_day = index[pos + 1]
        assert exec_day in executions
        assert executions[exec_day] == decisions[decision_day]
