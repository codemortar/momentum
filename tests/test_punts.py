"""Punt picking: pure scoring logic only — the network fetch is never tested
(and the delivery layer guards it so a failure can't break the signal)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum.punts import pick_punt
from momentum.strategies import MOMENTUM_LOOKBACK


def _candidates(rets_by_ticker: dict[str, float]) -> pd.DataFrame:
    """Steady-growth series: 13612W score ordering follows total return."""
    n = MOMENTUM_LOOKBACK + 1
    idx = pd.bdate_range("2025-01-01", periods=n)
    return pd.DataFrame(
        {t: np.geomspace(100.0, 100.0 * (1 + r), n) for t, r in rets_by_ticker.items()},
        index=idx,
    )


def test_picks_the_strongest_momentum_candidate():
    prices = _candidates({"AAA.L": 0.10, "BBB.L": 0.60, "CCC.L": -0.20})
    ticker, score = pick_punt(prices)
    assert ticker == "BBB.L"
    assert score > 0


def test_ignores_candidates_with_insufficient_history():
    prices = _candidates({"AAA.L": 0.10, "BBB.L": 0.60})
    # BBB is the raw winner, but has no data for most of the lookback window.
    prices.loc[prices.index[: MOMENTUM_LOOKBACK // 2], "BBB.L"] = np.nan
    ticker, _ = pick_punt(prices)
    assert ticker == "AAA.L"


def test_raises_when_nothing_is_scorable():
    prices = _candidates({"AAA.L": 0.10}).iloc[-50:]
    with pytest.raises(ValueError):
        pick_punt(prices)
