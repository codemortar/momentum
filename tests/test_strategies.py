"""Strategy logic tested against constructed price frames (truth tables)."""

from __future__ import annotations

import numpy as np
import pytest

from momentum.config import BONDS, CASH, EQUITIES_INTL, EQUITIES_US, GOLD
from momentum.strategies import (
    MA_WINDOW,
    MOMENTUM_LOOKBACK,
    accel_momentum,
    dual_momentum,
    ma_200,
    vaa,
)
from tests.conftest import frame


# --- ma_200 ------------------------------------------------------------------

def test_ma200_holds_equities_when_above_average():
    # Rising line: last close is above its own trailing 200d mean.
    prices = frame({EQUITIES_US: np.linspace(100, 200, MA_WINDOW + 10)})
    assert ma_200(prices) == {EQUITIES_US: 1.0}


def test_ma200_holds_bonds_when_below_average():
    # Falling line: last close is below its trailing mean.
    prices = frame({EQUITIES_US: np.linspace(200, 100, MA_WINDOW + 10)})
    assert ma_200(prices) == {BONDS: 1.0}


def test_ma200_raises_without_enough_history():
    prices = frame({EQUITIES_US: np.ones(MA_WINDOW - 1)})
    with pytest.raises(ValueError):
        ma_200(prices)


# --- dual_momentum truth table ----------------------------------------------

def _dm_frame(us_ret, intl_ret, cash_ret):
    """Build a frame where each asset's 253-day-ago->now return is exactly given."""
    n = MOMENTUM_LOOKBACK + 1
    def series(total_ret):
        return np.geomspace(100.0, 100.0 * (1 + total_ret), n)
    return frame({
        EQUITIES_US: series(us_ret),
        EQUITIES_INTL: series(intl_ret),
        CASH: series(cash_ret),
    })


def test_dm_picks_us_when_us_best():
    prices = _dm_frame(us_ret=0.30, intl_ret=0.10, cash_ret=0.02)
    assert dual_momentum(prices) == {EQUITIES_US: 1.0}


def test_dm_picks_intl_when_intl_best():
    prices = _dm_frame(us_ret=0.10, intl_ret=0.30, cash_ret=0.02)
    assert dual_momentum(prices) == {EQUITIES_INTL: 1.0}


def test_dm_goes_to_bonds_when_both_below_cash():
    prices = _dm_frame(us_ret=-0.10, intl_ret=-0.20, cash_ret=0.02)
    assert dual_momentum(prices) == {BONDS: 1.0}


def test_dm_raises_without_enough_history():
    prices = frame({EQUITIES_US: np.ones(MOMENTUM_LOOKBACK)})
    with pytest.raises(ValueError):
        dual_momentum(prices)


# --- 13612W strategies (accel_momentum, vaa) ----------------------------------

def _steady_frame(rets_by_role):
    """Constant-daily-rate series with the given total return over the frame.

    For such a series every lookback window's return has the same sign as the
    total, and a higher total return means a higher return over every window —
    so the sign and *ordering* of the 13612W scores are known by construction
    (flat series score exactly 0) without re-deriving the formula here.
    """
    n = MOMENTUM_LOOKBACK + 1
    return frame(
        {role: np.geomspace(100.0, 100.0 * (1 + r), n) for role, r in rets_by_role.items()}
    )


def test_accel_picks_the_stronger_equity_when_trending():
    up = _steady_frame({EQUITIES_US: 0.30, EQUITIES_INTL: 0.10})
    assert accel_momentum(up) == {EQUITIES_US: 1.0}
    flipped = _steady_frame({EQUITIES_US: 0.10, EQUITIES_INTL: 0.30})
    assert accel_momentum(flipped) == {EQUITIES_INTL: 1.0}


def test_accel_goes_defensive_when_equities_lag_cash():
    prices = _steady_frame({EQUITIES_US: -0.10, EQUITIES_INTL: -0.20, CASH: 0.02})
    assert accel_momentum(prices) == {BONDS: 1.0}


def test_accel_flees_a_fresh_crash_that_dual_momentum_ignores():
    # Both equities rise 40% over eleven months, then drop 15% in the final
    # month (last 21 trading days). The trailing 12-month return is still +19%,
    # so dual_momentum stays invested — but the 13612W blend annualizes the
    # fresh crash (12 x -15%), turning the score negative and forcing the exit.
    n = MOMENTUM_LOOKBACK + 1
    xp = [0, n - 1 - 21, n - 1]
    us = np.interp(np.arange(n), xp, [100.0, 140.0, 119.0])
    prices = frame({EQUITIES_US: us, EQUITIES_INTL: 0.5 * us})
    assert dual_momentum(prices) == {EQUITIES_US: 1.0}
    assert accel_momentum(prices) == {BONDS: 1.0}


def test_accel_raises_without_enough_history():
    prices = frame({EQUITIES_US: np.ones(MOMENTUM_LOOKBACK)})
    with pytest.raises(ValueError):
        accel_momentum(prices)


def test_vaa_holds_best_risk_asset_when_all_are_positive():
    # Gold is in the risk set: with every risk asset trending up, the single
    # best score wins even though it is not an equity.
    prices = _steady_frame(
        {EQUITIES_US: 0.20, EQUITIES_INTL: 0.10, GOLD: 0.40, BONDS: 0.03}
    )
    assert vaa(prices) == {GOLD: 1.0}


def test_vaa_goes_defensive_when_any_risk_asset_is_negative():
    # Equities are strong but gold is falling: VAA's canary trips and it holds
    # the better defensive asset (rising bonds beat flat cash).
    prices = _steady_frame(
        {EQUITIES_US: 0.30, EQUITIES_INTL: 0.10, GOLD: -0.05, BONDS: 0.05}
    )
    assert vaa(prices) == {BONDS: 1.0}


def test_vaa_defensive_leg_prefers_cash_when_bonds_are_falling():
    prices = _steady_frame({EQUITIES_US: 0.30, GOLD: -0.05, BONDS: -0.10})
    assert vaa(prices) == {CASH: 1.0}


def test_vaa_raises_without_enough_history():
    prices = frame({EQUITIES_US: np.ones(MOMENTUM_LOOKBACK)})
    with pytest.raises(ValueError):
        vaa(prices)
