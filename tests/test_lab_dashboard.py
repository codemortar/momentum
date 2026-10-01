"""Dashboard: data shaping and rendering from synthetic journals, no network."""

from __future__ import annotations

from datetime import date

import pytest

from momentum import config as momentum_config
from momentum.lab import dashboard
from momentum.lab.config import FTSE, START_CAPITAL


def mark(method, day, value):
    return {"kind": "mark", "date": day, "method": method, "value": value}


RECORDS = [
    mark("overnight", "2026-10-01", START_CAPITAL * 0.999),
    mark("overnight", "2026-10-02", START_CAPITAL * 1.010),
    mark("random_walk", "2026-10-01", START_CAPITAL),
    mark("random_walk", "2026-10-02", START_CAPITAL * 1.002),
    mark("sma_cross_ftse", "2026-10-02", START_CAPITAL * 0.995),
    {"kind": "fill", "date": "2026-10-01", "method": "overnight", "instrument": "EQ:SPY",
     "qty": 128, "price": 762.78, "cash_delta": -97635.84, "friction": 19.52},
]


def test_panels_split_by_market_and_index_returns_to_zero():
    spy, ftse = dashboard.panels(RECORDS)
    assert spy["market"] == "SPY" and ftse["market"] == FTSE
    overnight = next(s for s in spy["series"] if s["name"] == "overnight")
    assert overnight["values"] == [pytest.approx(-0.1), pytest.approx(1.0)]
    # Methods with no marks yet appear with gaps, not invented zeros.
    theta = next(s for s in spy["series"] if s["name"] == "theta_puts")
    assert theta["values"] == [None, None]


def test_controls_are_styled_as_controls():
    spy, _ = dashboard.panels(RECORDS)
    styles = {s["name"]: s["style"] for s in spy["series"]}
    assert styles["random_walk"] == "control" and styles["lunar"] == "placebo"


def test_stats_count_fills_and_costs():
    by_name = {r["name"]: r for r in dashboard.stats(RECORDS)}
    assert by_name["overnight"]["fills"] == 1
    assert by_name["overnight"]["friction"] == pytest.approx(19.52)
    assert by_name["overnight"]["ret"] == pytest.approx(0.01)


def test_render_includes_charts_tables_and_countdown():
    page = dashboard.render(RECORDS, "test", today=date(2026, 10, 2))
    assert page.count("<svg viewBox") == 2
    assert "overnight +1.00%" in page            # direct label at the line end
    assert "Scoreboard" in page and "Open positions" in page and "Recent fills" in page
    assert "91 days to assessment" in page


def test_render_handles_an_empty_journal():
    page = dashboard.render([], "test", today=date(2026, 10, 1))
    assert "No trading days recorded" in page


def test_read_url_prefers_the_environment_then_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(momentum_config, "ROOT", tmp_path)
    monkeypatch.delenv(dashboard.READ_URL_VAR, raising=False)
    assert dashboard.read_url() is None
    (tmp_path / ".env").write_text('MOMENTUM_READ_DATABASE_URL="postgresql://from-file"\n')
    assert dashboard.read_url() == "postgresql://from-file"
    monkeypatch.setenv(dashboard.READ_URL_VAR, "postgresql://from-env")
    assert dashboard.read_url() == "postgresql://from-env"


def test_dashboard_never_reads_the_sync_variable(tmp_path, monkeypatch):
    # The write credential alone must not turn a viewing machine into a database client.
    monkeypatch.setattr(momentum_config, "ROOT", tmp_path)
    monkeypatch.delenv(dashboard.READ_URL_VAR, raising=False)
    monkeypatch.setenv("MOMENTUM_DATABASE_URL", "postgresql://write-access")
    assert dashboard.read_url() is None


def test_run_dashboard_writes_the_page_from_the_local_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "read_url", lambda: None)
    monkeypatch.setattr(momentum_config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(dashboard.journal, "read_all", lambda: RECORDS)
    assert dashboard.run_dashboard(open_browser=False) == 0
    assert "Paper-trading lab" in (tmp_path / "lab_dashboard.html").read_text()


@pytest.mark.parametrize("lo,hi", [(-1.78, 8.70), (-1.29, 4.6), (0.0, 0.0), (-3.4, -0.2), (0.0, 0.3)])
def test_gridlines_always_bracket_the_data(lo, hi):
    ticks = dashboard._ticks(lo, hi)
    assert ticks[0] <= lo and ticks[-1] >= hi   # regression: the top line used to clip
    assert len(ticks) <= 8
