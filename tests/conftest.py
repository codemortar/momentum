"""Synthetic price fixtures. Tests NEVER hit the network — correctness is checked
against series whose right answer is known by construction.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum.config import BONDS, CASH, EQUITIES_INTL, EQUITIES_US, GOLD

ROLES = [EQUITIES_US, EQUITIES_INTL, BONDS, GOLD, CASH]


def bdays(n: int, start: str = "2000-01-03") -> pd.DatetimeIndex:
    return pd.bdate_range(start=start, periods=n)


def frame(series_by_role: dict[str, np.ndarray], start: str = "2000-01-03") -> pd.DataFrame:
    n = len(next(iter(series_by_role.values())))
    df = pd.DataFrame(series_by_role, index=bdays(n, start))
    # Any unspecified role gets a flat 1.0 series so strategies can always index it.
    for role in ROLES:
        if role not in df.columns:
            df[role] = 1.0
    return df[ROLES]


@pytest.fixture
def flat_prices() -> pd.DataFrame:
    """All roles constant at 1.0 for 3 years — no drift, ideal for cost tests."""
    n = 756
    return frame({r: np.ones(n) for r in ROLES})


@pytest.fixture
def trending_prices() -> pd.DataFrame:
    """US equities rising steadily; intl slightly slower; bonds/gold/cash flat-ish.

    Long enough to exercise the momentum warmup and produce several rebalances.
    """
    n = 800
    t = np.arange(n)
    return frame(
        {
            EQUITIES_US: 100 * (1.0004 ** t),   # ~10.5%/yr
            EQUITIES_INTL: 100 * (1.0002 ** t),  # slower
            BONDS: 100 * (1.00005 ** t),
            GOLD: 100 * np.ones(n),
            CASH: 100 * (1.00002 ** t),
        }
    )
