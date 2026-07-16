"""Performance metrics computed from a daily equity curve (a pd.Series).

Every function takes an equity curve indexed by trading day and starting near 1.0.
Kept deliberately simple and testable against series with known-by-construction
answers (see tests/test_metrics.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def cagr(curve: pd.Series) -> float:
    """Compound annual growth rate, using actual calendar span."""
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return (curve.iloc[-1] / curve.iloc[0]) ** (1.0 / years) - 1.0


def max_drawdown(curve: pd.Series) -> float:
    """Largest peak-to-trough decline. Returned as a negative fraction."""
    running_peak = curve.cummax()
    drawdown = curve / running_peak - 1.0
    return float(drawdown.min())


def annual_vol(curve: pd.Series) -> float:
    """Annualized volatility of daily returns."""
    daily = curve.pct_change().dropna()
    return float(daily.std(ddof=0) * np.sqrt(TRADING_DAYS))


def sharpe(curve: pd.Series, cash_curve: pd.Series) -> float:
    """Annualized Sharpe ratio, using the cash curve as the risk-free benchmark."""
    port = curve.pct_change().dropna()
    cash = cash_curve.reindex(curve.index).pct_change().reindex(port.index).fillna(0.0)
    excess = port - cash
    sd = excess.std(ddof=0)
    if sd == 0:
        return 0.0
    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS))


def compute(curve: pd.Series, cash_curve: pd.Series) -> dict[str, float]:
    """All headline metrics for one equity curve, as a dict."""
    return {
        "CAGR": cagr(curve),
        "MaxDD": max_drawdown(curve),
        "Vol": annual_vol(curve),
        "Sharpe": sharpe(curve, cash_curve),
    }
