"""Signal timing: the live signal must act on the last *completed* month-end,
never the in-progress month (which would be a rule the backtest never validated).
Plus delivery plumbing: email composition and configuration (never the network).
"""

from __future__ import annotations

import pandas as pd

from momentum.signal import (
    SignalReport,
    compose_email,
    email_config_from_env,
    last_completed_month_end,
)


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


# --- Email delivery -----------------------------------------------------------

REPORTS = [
    SignalReport(strategy="ma_200", role="bonds", ticker="VGOV.L", as_of="2026-06-30"),
    SignalReport(strategy="vaa", role="equities_us", ticker="VUSA.L", as_of="2026-06-30"),
]


def test_email_with_changes_says_what_to_sell_and_buy():
    changes = [("ma_200", "VUSA.L", "VGOV.L")]
    subject, body = compose_email("uk", REPORTS, changes, "2026-06-30")
    assert subject == "Momentum (uk): ACTION — 1 signal(s) changed"
    assert "ma_200: SELL VUSA.L, BUY VGOV.L" in body
    assert "month-end 2026-06-30" in body


def test_email_without_changes_lists_holdings():
    subject, body = compose_email("uk", REPORTS, [], "2026-06-30")
    assert subject == "Momentum (uk): no change"
    assert "SELL" not in body
    assert "HOLD VGOV.L (bonds)" in body
    assert "HOLD VUSA.L (equities_us)" in body


def test_email_config_requires_all_three_env_vars(monkeypatch):
    for var in ("SENDGRID_API_KEY", "MOMENTUM_EMAIL_FROM", "MOMENTUM_EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    assert email_config_from_env() is None

    monkeypatch.setenv("SENDGRID_API_KEY", "SG.test")
    monkeypatch.setenv("MOMENTUM_EMAIL_FROM", "momentum@example.com")
    assert email_config_from_env() is None  # to-address still missing

    monkeypatch.setenv("MOMENTUM_EMAIL_TO", "me@example.com")
    cfg = email_config_from_env()
    assert cfg is not None
    assert cfg.to_addr == "me@example.com"
