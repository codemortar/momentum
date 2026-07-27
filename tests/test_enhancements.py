"""Tranching and trade buffers: the two robustness options on the engine.

Both change *when* and *whether* trades happen, so the no-lookahead guarantee
matters more here than anywhere — a buffered strategy is stateful, and state is
exactly where future information tends to leak in.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import backtest, buffers
from momentum.config import BONDS, CostModel, EQUITIES_INTL, EQUITIES_US
from momentum.strategies import STRATEGIES, accel_momentum, buy_and_hold
from tests.conftest import frame


# --- Tranching ---------------------------------------------------------------

def test_single_tranche_is_the_original_schedule(trending_prices):
    days = backtest.tranche_decision_days(trending_prices.index, 1)
    assert len(days) == 1
    pd.testing.assert_index_equal(
        days[0], backtest.month_end_trading_days(trending_prices.index)
    )


def test_tranches_are_offset_through_the_month(trending_prices):
    index = trending_prices.index
    schedules = backtest.tranche_decision_days(index, 4)
    assert len(schedules) == 4
    # Each tranche trades roughly a quarter-month after the previous one.
    first_positions = [index.get_loc(s[0]) for s in schedules]
    gaps = np.diff(first_positions)
    assert all(4 <= g <= 6 for g in gaps), first_positions


def test_run_tranched_with_one_tranche_equals_a_plain_run(trending_prices):
    plain = backtest.run(trending_prices, accel_momentum, "am", CostModel())
    one = backtest.run_tranched(trending_prices, accel_momentum, "am", CostModel(), 1)
    pd.testing.assert_series_equal(plain.equity_curve, one.equity_curve)


def test_tranched_curve_is_the_average_of_its_tranches(trending_prices):
    n = 4
    combined = backtest.run_tranched(
        trending_prices, accel_momentum, "am", CostModel(), n
    )
    singles = [
        backtest.run(
            trending_prices, accel_momentum, "am", CostModel(), decision_days=days
        )
        for days in backtest.tranche_decision_days(trending_prices.index, n)
    ]
    start = combined.equity_curve.index[0]
    expected = pd.concat(
        [s.equity_curve.loc[start:] / s.equity_curve.loc[start] for s in singles],
        axis=1,
    ).mean(axis=1)
    assert np.allclose(combined.equity_curve.values, expected.values)
    # Every tranche trades, so the combined count is the sum, not one tranche's.
    assert combined.n_trades == sum(s.n_trades for s in singles)


def test_tranching_a_never_trading_strategy_changes_nothing(flat_prices):
    combined = backtest.run_tranched(
        flat_prices, buy_and_hold, "bh", CostModel(trade_cost_bps=50.0), 4
    )
    assert combined.n_trades == 0
    assert np.isclose(combined.equity_curve.iloc[-1], 1.0)


# --- Buffers -----------------------------------------------------------------

def _switching_prices(n=700, crossover=450):
    """US leads, then intl accelerates past it — with the crossover placed well
    after the 253-day warmup so the engine actually sees a switch."""
    t = np.arange(n)
    us = 100 * (1.0006 ** t)
    intl = 100 * (1.0002 ** t)
    intl[crossover:] = intl[crossover] * (1.0018 ** np.arange(n - crossover))
    return frame({
        EQUITIES_US: us,
        EQUITIES_INTL: intl,
        BONDS: 100 * (1.00005 ** t),
        CASH_ROLE: 100 * (1.00002 ** t),
    })


from momentum.config import CASH as CASH_ROLE  # noqa: E402  (used above)


def test_buffer_holds_on_when_the_challenger_barely_leads():
    prices = _switching_prices()
    plain = backtest.run(prices, accel_momentum, "am", CostModel())
    buffered = backtest.run(
        prices,
        buffers.buffered_strategy("accel_momentum", accel_momentum, margin=0.50),
        "am",
        CostModel(),
    )
    # A 50-percentage-point hurdle is never cleared here, so after the opening
    # position the buffered run should trade strictly less.
    assert plain.n_trades > 0
    assert buffered.n_trades < plain.n_trades


def test_zero_margin_buffer_matches_the_unbuffered_strategy():
    prices = _switching_prices()
    plain = backtest.run(prices, accel_momentum, "am", CostModel())
    buffered = backtest.run(
        prices,
        buffers.buffered_strategy("accel_momentum", accel_momentum, margin=0.0),
        "am",
        CostModel(),
    )
    pd.testing.assert_series_equal(plain.equity_curve, buffered.equity_curve)


def test_buffer_takes_the_signal_when_there_is_nothing_held_yet():
    prices = _switching_prices()
    strategy = buffers.buffered_strategy("accel_momentum", accel_momentum, margin=9.9)
    # held=None on the very first decision: the buffer cannot "stay put".
    assert strategy(prices, None) == accel_momentum(prices)


def test_buffer_switches_once_the_margin_is_genuinely_cleared():
    prices = _switching_prices()
    strategy = buffers.buffered_strategy("accel_momentum", accel_momentum, margin=0.01)
    unbuffered = accel_momentum(prices)
    winner = max(unbuffered, key=unbuffered.get)
    loser = EQUITIES_US if winner == EQUITIES_INTL else EQUITIES_INTL
    # Holding the clear loser, a 1-point hurdle is easily beaten -> switch.
    assert strategy(prices, loser) == unbuffered


def test_ma_200_buffer_is_a_band_around_the_moving_average():
    # Price just 1% above its own average: with a 5% band, a holder of bonds
    # should not be tempted in, but with no band it would switch.
    n = 260
    prices = frame({EQUITIES_US: np.concatenate([
        np.full(n - 1, 100.0), [101.0],
    ])})
    tight = buffers.buffered_strategy("ma_200", STRATEGIES["ma_200"], margin=0.05)
    loose = buffers.buffered_strategy("ma_200", STRATEGIES["ma_200"], margin=0.0)
    assert tight(prices, BONDS) == {BONDS: 1.0}
    assert loose(prices, BONDS) == {EQUITIES_US: 1.0}


def test_negative_margin_is_rejected():
    with pytest.raises(ValueError):
        buffers.with_buffer(accel_momentum, buffers.SCORES["accel_momentum"], -0.1)


# --- The guarantee still holds for stateful strategies -----------------------

@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_buffered_strategies_do_not_look_ahead(trending_prices, name):
    strategy = buffers.buffered_strategy(name, STRATEGIES[name], margin=0.02)
    full = backtest.run(trending_prices, strategy, name, CostModel())
    trunc = backtest.run(trending_prices.iloc[:-100], strategy, name, CostModel())
    pd.testing.assert_frame_equal(
        full.weights_history.loc[trunc.weights_history.index], trunc.weights_history
    )


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_tranched_runs_do_not_look_ahead(trending_prices, name):
    full = backtest.run_tranched(
        trending_prices, STRATEGIES[name], name, CostModel(), 4
    )
    trunc = backtest.run_tranched(
        trending_prices.iloc[:-100], STRATEGIES[name], name, CostModel(), 4
    )
    overlap = trunc.weights_history.index
    pd.testing.assert_frame_equal(
        full.weights_history.loc[overlap], trunc.weights_history
    )
