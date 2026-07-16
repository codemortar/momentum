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
    filter_to_followed,
    last_completed_month_end,
    strategy_explanations,
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


def test_email_without_changes_says_do_nothing():
    _, body = compose_email("uk", REPORTS, [], "2026-06-30")
    assert "do nothing" in body


def test_email_includes_explanations_and_snapshot_when_given():
    explanations = {"ma_200": "US equities close is 4.2% ABOVE their 200-day average."}
    snapshot = ["Market snapshot (total returns to the month-end above):", "  rows..."]
    _, body = compose_email(
        "uk", REPORTS, [], "2026-06-30", explanations=explanations, snapshot=snapshot
    )
    assert "4.2% ABOVE their 200-day average" in body
    assert "Market snapshot" in body


def test_explanations_carry_the_readings_behind_each_decision(trending_prices):
    # trending_prices: US equities rising above trend and outpacing intl/cash,
    # so the wording is known by construction.
    got = strategy_explanations(trending_prices)
    assert set(got) == set(
        ["buy_and_hold", "ma_200", "dual_momentum", "accel_momentum", "vaa"]
    )
    assert "ABOVE" in got["ma_200"] and "hold equities" in got["ma_200"]
    assert "12-month returns:" in got["dual_momentum"]
    assert "beats cash -> hold it" in got["dual_momentum"]
    assert "13612W" in got["accel_momentum"]
    # Gold is flat in the fixture: its score is not positive, so vaa plays defence.
    assert "not positive -> play defence" in got["vaa"]


def test_followed_strategy_scopes_email_to_that_strategy_only():
    changes = [("ma_200", "VUSA.L", "VGOV.L")]
    # The user follows vaa: ma_200's change is not their business — the email
    # must read as "no change" so the ACTION subject only ever means *them*.
    reports, kept = filter_to_followed(REPORTS, changes, "vaa")
    assert [r.strategy for r in reports] == ["vaa"]
    assert kept == []
    subject, body = compose_email("uk", reports, kept, "2026-06-30")
    assert subject == "Momentum (uk): no change"
    assert "Your strategy:" in body
    assert "ma_200" not in body


def test_unset_or_unknown_followed_strategy_keeps_everything():
    changes = [("ma_200", "VUSA.L", "VGOV.L")]
    assert filter_to_followed(REPORTS, changes, None) == (REPORTS, changes)
    assert filter_to_followed(REPORTS, changes, "not_a_strategy") == (REPORTS, changes)


def test_punt_section_is_appended_after_a_separator_when_given():
    punt = ["PUNT OF THE MONTH — for fun only, NOT a validated signal:", "  RR.L ..."]
    _, body = compose_email("uk", REPORTS, [], "2026-06-30", punt=punt)
    assert "PUNT OF THE MONTH" in body
    # Separated from the real signal, and the disclaimer still closes the email.
    assert body.index("----") < body.index("PUNT OF THE MONTH")
    assert body.index("PUNT OF THE MONTH") < body.index("Not financial advice")


def test_email_has_no_punt_section_by_default():
    _, body = compose_email("uk", REPORTS, [], "2026-06-30")
    assert "PUNT" not in body


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
