"""Metrics tested against curves whose answers are known by construction."""

from __future__ import annotations

import numpy as np
import pandas as pd

from momentum import metrics


def _curve(values, start="2000-01-03"):
    return pd.Series(values, index=pd.bdate_range(start=start, periods=len(values)))


def test_cagr_exact_doubling_over_four_years():
    # 252*4 daily steps geometrically doubling over ~4 calendar years.
    n = 252 * 4
    values = np.geomspace(1.0, 2.0, n)
    curve = _curve(values)
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    expected = 2.0 ** (1.0 / years) - 1.0
    assert np.isclose(metrics.cagr(curve), expected)


def test_max_drawdown_100_50_75():
    curve = _curve([100.0, 50.0, 75.0])
    # Peak 100 -> trough 50 = -50%.
    assert np.isclose(metrics.max_drawdown(curve), -0.5)


def test_max_drawdown_monotonic_up_is_zero():
    curve = _curve([1.0, 1.1, 1.2, 1.5])
    assert np.isclose(metrics.max_drawdown(curve), 0.0)


def test_annual_vol_of_constant_curve_is_zero():
    curve = _curve(np.ones(300))
    assert metrics.annual_vol(curve) == 0.0


def test_sharpe_zero_when_flat():
    curve = _curve(np.ones(300))
    cash = _curve(np.ones(300))
    assert metrics.sharpe(curve, cash) == 0.0
