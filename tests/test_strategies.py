"""Strategy logic tested against constructed price frames (truth tables)."""

from __future__ import annotations

import numpy as np
import pytest

from momentum.config import BONDS, CASH, EQUITIES_INTL, EQUITIES_US
from momentum.strategies import MA_WINDOW, MOMENTUM_LOOKBACK, dual_momentum, ma_200
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
