"""Signal timing: the live signal must act on the last *completed* month-end,
never the in-progress month (which would be a rule the backtest never validated).
"""

from __future__ import annotations

import pandas as pd

from momentum.signal import last_completed_month_end


def test_midmonth_uses_previous_month_end():
    # Data runs through mid-July; the last actionable month-end is 30 Jun.
    index = pd.bdate_range("2026-01-01", "2026-07-16")
    got = last_completed_month_end(index)
    assert got == pd.Timestamp("2026-06-30")


def test_full_month_of_data_still_waits_for_next_day():
    # Data ends exactly on a month-end trading day (31 Mar 2026, a Tuesday).
    # With no following day yet, we may not act on it — the last actionable
    # month-end is the prior one (27 Feb 2026).
    index = pd.bdate_range("2025-06-02", "2026-03-31")
    got = last_completed_month_end(index)
    assert got == pd.Timestamp("2026-02-27")
