"""Punt book: position maths, benchmark alignment, and the separation from the
strategy ledger. State goes to tmp_path; no network."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from momentum import config, punt_ledger
from momentum.punt_ledger import Punt, benchmark_return, position_return


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    return tmp_path


def _punt(**kw) -> Punt:
    base = dict(
        ticker="AZN.L", opened="2026-01-05", amount=500.0,
        entry_price=100.0, thesis="cheap and profitable",
    )
    base.update(kw)
    return Punt(**base)


# --- Storage ------------------------------------------------------------------

def test_punts_round_trip_through_disk(store):
    punt_ledger.add_punt(_punt())
    punt_ledger.add_punt(_punt(ticker="SHEL.L", entry_price=250.0))
    loaded = punt_ledger.load_punts()
    assert [p.ticker for p in loaded] == ["AZN.L", "SHEL.L"]
    assert loaded[0].thesis == "cheap and profitable"
    assert all(p.is_open for p in loaded)


def test_two_positions_in_one_ticker_stay_separate(store):
    # Averaging them would hide that they were two distinct decisions.
    punt_ledger.add_punt(_punt(opened="2026-01-05", entry_price=100.0))
    punt_ledger.add_punt(_punt(opened="2026-03-05", entry_price=120.0))
    assert len(punt_ledger.load_punts()) == 2


def test_closing_takes_the_oldest_open_position_first(store):
    punt_ledger.add_punt(_punt(opened="2026-01-05", entry_price=100.0))
    punt_ledger.add_punt(_punt(opened="2026-03-05", entry_price=120.0))
    punt_ledger.close_punt("AZN.L", exit_price=150.0, closed="2026-06-01")
    punts = punt_ledger.load_punts()
    assert punts[0].closed == "2026-06-01" and punts[0].exit_price == 150.0
    assert punts[1].is_open


def test_closing_an_unheld_ticker_is_an_error(store):
    with pytest.raises(ValueError, match="No open position"):
        punt_ledger.close_punt("NOPE.L", exit_price=1.0, closed="2026-06-01")


# --- Returns -------------------------------------------------------------------

def test_open_position_is_marked_to_the_current_price():
    assert position_return(_punt(entry_price=100.0), 125.0) == pytest.approx(0.25)


def test_closed_position_uses_its_exit_price_not_the_market():
    closed = _punt(entry_price=100.0, closed="2026-06-01", exit_price=80.0)
    # Even handed a current price, a sold position's return is what was realised.
    assert position_return(closed, 200.0) == pytest.approx(-0.20)


def test_unpriced_open_position_has_no_return():
    assert position_return(_punt(), None) is None


def test_backdated_entry_must_not_be_recorded_at_today_s_price(store, monkeypatch):
    """Regression: `punt add --date <past>` without --price once stored the
    *current* price as the entry, so a real loss displayed as +0.0%."""
    asked: list = []

    def fake_fetch(ticker, date):
        asked.append((ticker, date))
        return 4526.81 if date == "2026-05-01" else 3572.00  # then vs now

    monkeypatch.setattr(punt_ledger, "fetch_price_on", fake_fetch)
    punt_ledger.run_punt(
        action="add", universe_name="uk", strategy="accel_momentum",
        ticker="HLMA.L", amount=300.0, price=None, thesis="bought in May",
        trigger="", date="2026-05-01",
    )
    stored = punt_ledger.load_punts()[0]
    assert asked == [("HLMA.L", "2026-05-01")]   # priced on the purchase date
    assert stored.entry_price == pytest.approx(4526.81)
    # And the loss since is visible rather than washed out to zero.
    assert position_return(stored, 3572.00) == pytest.approx(-0.211, abs=1e-3)


# --- Benchmark alignment ---------------------------------------------------------

@pytest.fixture
def curve() -> pd.Series:
    # 1% per business day, so any window's return is known by construction.
    idx = pd.bdate_range("2026-01-01", periods=120)
    return pd.Series(1.01 ** np.arange(len(idx)), index=idx)


def test_benchmark_measures_exactly_the_holding_period(curve):
    start, end = curve.index[10], curve.index[30]
    got = benchmark_return(curve, start.isoformat(), end.isoformat())
    assert got == pytest.approx(1.01 ** 20 - 1)


def test_open_position_is_benchmarked_to_the_latest_data(curve):
    start = curve.index[10]
    got = benchmark_return(curve, start.isoformat(), None)
    assert got == pytest.approx(1.01 ** (len(curve) - 1 - 10) - 1)


def test_a_weekend_open_date_falls_back_to_the_prior_trading_day(curve):
    # 2026-01-10 is a Saturday; the comparison must start from Friday's close,
    # not silently skip forward to Monday and lose a day of the benchmark.
    saturday = "2026-01-10"
    friday = curve.loc[:saturday].index[-1]
    assert friday.dayofweek == 4
    got = benchmark_return(curve, saturday, None)
    expected = curve.iloc[-1] / curve.loc[friday] - 1.0
    assert got == pytest.approx(expected)


def test_no_benchmark_before_the_curve_starts(curve):
    assert benchmark_return(curve, "2020-01-01", None) is None


def test_no_benchmark_when_no_time_has_elapsed(curve):
    day = curve.index[5].isoformat()
    assert benchmark_return(curve, day, day) is None


# --- Report --------------------------------------------------------------------

@pytest.fixture
def flat_curve() -> pd.Series:
    """A benchmark that barely moves, so a pick's return is what decides."""
    idx = pd.bdate_range("2026-01-01", periods=120)
    return pd.Series(1.0001 ** np.arange(len(idx)), index=idx)


def test_report_shows_when_picks_beat_the_system(flat_curve):
    start = flat_curve.index[10]
    punts = [_punt(opened=start.isoformat(), entry_price=100.0, amount=1000.0)]
    text = punt_ledger.format_report(
        punts, {"AZN.L": 130.0}, flat_curve, "accel_momentum"
    )
    assert "+30.0%" in text
    assert "your picks are ahead" in text
    assert "thesis: cheap and profitable" in text


def test_report_says_so_when_the_system_wins(curve):
    # The benchmark compounds hard here; a +30% pick does not keep up, and the
    # summary must not flatter the punt.
    start = curve.index[10]
    punts = [_punt(opened=start.isoformat(), entry_price=100.0, amount=1000.0)]
    text = punt_ledger.format_report(punts, {"AZN.L": 130.0}, curve, "accel_momentum")
    assert "the system is ahead" in text


def test_empty_book_explains_how_to_add_one():
    text = punt_ledger.format_report([], {}, pd.Series(dtype=float), "accel_momentum")
    assert "No punts logged yet" in text
    assert "punt add" in text
