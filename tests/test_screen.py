"""Screener scoring logic. Pure functions only — the yfinance fetch is never
tested (no network in the suite); fixtures are built so the ranking answer is
known by construction."""

from __future__ import annotations

import numpy as np
import pandas as pd

from momentum.screen import (
    composite_score,
    factor_ranks,
    piotroski_fscore,
    range_position,
    screen,
)


def _frame(rows: dict[str, dict]) -> pd.DataFrame:
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "ticker"
    return df


def _company(pe, pb, ev, roe, margin, current, debt, **extra):
    return {
        "trailingPE": pe, "priceToBook": pb, "enterpriseToEbitda": ev,
        "returnOnEquity": roe, "profitMargins": margin,
        "currentRatio": current, "debtToEquity": debt, **extra,
    }


# GOOD is cheapest on every value metric and strongest on every quality/safety
# metric; BAD is the reverse; MID sits between. So the ranking is unambiguous.
UNIVERSE = _frame({
    "GOOD": _company(8, 0.9, 5, 0.30, 0.25, 2.5, 20, shortName="Good plc"),
    "MID": _company(15, 2.0, 10, 0.15, 0.12, 1.5, 80, shortName="Mid plc"),
    "BAD": _company(40, 6.0, 25, 0.03, 0.02, 0.7, 250, shortName="Bad plc"),
})


def test_cheap_and_profitable_scores_highest():
    scored = screen(UNIVERSE)
    assert list(scored.index) == ["GOOD", "MID", "BAD"]
    assert scored.loc["GOOD", "score"] > scored.loc["BAD", "score"]


def test_lower_is_better_factors_are_inverted():
    ranks = factor_ranks(UNIVERSE)
    # Cheapest P/E must rank best (1.0), most expensive worst.
    assert ranks.loc["GOOD", "P/E"] == 1.0
    assert ranks.loc["BAD", "P/E"] < ranks.loc["GOOD", "P/E"]
    # Highest ROE must also rank best — opposite raw direction, same orientation.
    assert ranks.loc["GOOD", "ROE"] == 1.0


def test_company_with_too_few_metrics_is_not_scored():
    sparse = UNIVERSE.copy()
    for col in ["priceToBook", "enterpriseToEbitda", "returnOnEquity",
                "profitMargins", "currentRatio"]:
        sparse.loc["MID", col] = np.nan
    scores = composite_score(sparse)
    assert pd.isna(scores["MID"])          # only 2 factors left, below minimum
    assert not pd.isna(scores["GOOD"])


def test_loss_making_company_is_not_ranked_as_cheap():
    # A negative P/E is meaningless, not "cheaper than everything else".
    # _clean() strips it; here we assert the ranking never rewards it.
    from momentum.screen import _clean

    with_loss = UNIVERSE.copy()
    with_loss.loc["BAD", "trailingPE"] = -5.0
    ranks = factor_ranks(_clean(with_loss))
    assert pd.isna(ranks.loc["BAD", "P/E"])
    assert ranks.loc["GOOD", "P/E"] == 1.0


def test_dividend_yield_units_are_decided_per_column():
    from momentum.screen import _clean

    # yfinance percentage-number form: 4.8 means 4.8%. The sub-1% value must be
    # scaled the same way as the rest, not mistaken for an already-fractional
    # 68% yield (the bug this guards).
    pct_form = _clean(_frame({
        "A": {"dividendYield": 4.8}, "B": {"dividendYield": 3.5},
        "C": {"dividendYield": 0.68},
    }))
    assert np.isclose(pct_form.loc["C", "dividendYield"], 0.0068)
    assert np.isclose(pct_form.loc["A", "dividendYield"], 0.048)

    # Fractional form is already correct and must be left alone.
    frac_form = _clean(_frame({
        "A": {"dividendYield": 0.048}, "B": {"dividendYield": 0.035},
    }))
    assert np.isclose(frac_form.loc["A", "dividendYield"], 0.048)


def test_range_position_locates_price_in_the_52_week_band():
    df = _frame({
        "LOW": {"currentPrice": 100.0, "fiftyTwoWeekLow": 100.0, "fiftyTwoWeekHigh": 200.0},
        "MID": {"currentPrice": 150.0, "fiftyTwoWeekLow": 100.0, "fiftyTwoWeekHigh": 200.0},
        "HIGH": {"currentPrice": 200.0, "fiftyTwoWeekLow": 100.0, "fiftyTwoWeekHigh": 200.0},
    })
    pos = range_position(df)
    assert pos["LOW"] == 0.0
    assert pos["MID"] == 0.5
    assert pos["HIGH"] == 1.0


# --- Piotroski F-Score ----------------------------------------------------------

def _statements(**overrides):
    """Two years of statements for a company passing all nine tests.

    Year 0 (latest) is better than year 1 on every improving measure, so the
    expected score is 9/9 unless a test deliberately breaks one.
    """
    base = {
        "netIncome_0": 120.0, "netIncome_1": 80.0,
        "totalAssets_0": 1000.0, "totalAssets_1": 1000.0,
        "operatingCashFlow_0": 200.0, "operatingCashFlow_1": 150.0,
        "longTermDebt_0": 100.0, "longTermDebt_1": 200.0,      # deleveraging
        "currentAssets_0": 300.0, "currentLiabilities_0": 100.0,   # ratio 3.0
        "currentAssets_1": 200.0, "currentLiabilities_1": 100.0,   # ratio 2.0
        "sharesOutstanding_0": 100.0, "sharesOutstanding_1": 100.0,  # no dilution
        "grossProfit_0": 400.0, "totalRevenue_0": 800.0,       # margin 0.50
        "grossProfit_1": 300.0, "totalRevenue_1": 750.0,       # margin 0.40
    }
    base.update(overrides)
    return base


def test_fscore_awards_all_nine_to_a_healthy_improving_company():
    # Turnover: rev/assets 800/1000 = 0.80 vs 750/1000 = 0.75, so improving too.
    score, n = piotroski_fscore(_statements())
    assert (score, n) == (9, 9)


def test_fscore_penalises_losses_dilution_and_rising_debt():
    row = _statements(
        netIncome_0=-50.0,          # unprofitable, and ROA fell
        longTermDebt_0=300.0,       # leverage rose
        sharesOutstanding_0=120.0,  # diluted
    )
    score, n = piotroski_fscore(row)
    assert n == 9
    assert score == 5  # the four broken signals are exactly those above


def test_fscore_skips_tests_it_cannot_evaluate_rather_than_failing_them():
    # A bank-shaped company: no gross profit or current asset/liability lines.
    row = _statements()
    for key in ("grossProfit_0", "grossProfit_1", "currentAssets_0",
                "currentAssets_1", "currentLiabilities_0", "currentLiabilities_1"):
        row[key] = None
    score, n = piotroski_fscore(row)
    assert n == 7            # margin and liquidity tests dropped, not failed
    assert score == 7        # everything evaluable still passes


def test_fscore_returns_nothing_when_there_are_no_statements():
    assert piotroski_fscore({}) == (0, 0)


# --- Sector-relative ranking ------------------------------------------------------

def test_by_sector_ranks_within_sector_not_across_the_universe():
    # Banks trade at low P/B, miners at high. Universe-wide, every bank beats
    # every miner on P/B; within sector, the best of each group ties at 1.0.
    rows = {}
    for i, pb in enumerate([0.8, 1.0, 1.2, 1.4]):
        rows[f"BANK{i}"] = _company(10, pb, 8, 0.12, 0.20, 1.2, 90,
                                    sector="Financial Services")
    for i, pb in enumerate([2.0, 2.4, 2.8, 3.2]):
        rows[f"MINE{i}"] = _company(12, pb, 9, 0.14, 0.22, 1.6, 60,
                                    sector="Basic Materials")
    df = _frame(rows)

    universe_wide = factor_ranks(df)["P/B"]
    assert universe_wide["MINE0"] < universe_wide["BANK3"]  # cheapest bank wins

    within = factor_ranks(df, by_sector=True)["P/B"]
    assert within["BANK0"] == 1.0 and within["MINE0"] == 1.0  # each sector's best
    assert within["BANK3"] == within["MINE3"]                 # each sector's worst


def test_small_sectors_fall_back_to_universe_ranking():
    rows = {f"BANK{i}": _company(10, 1.0 + i, 8, 0.12, 0.20, 1.2, 90,
                                 sector="Financial Services") for i in range(4)}
    rows["LONE"] = _company(50, 9.0, 30, 0.01, 0.01, 0.5, 400, sector="Utilities")
    ranks = factor_ranks(_frame(rows), by_sector=True)
    # Alone in its sector, LONE cannot be ranked within it — and must not be
    # handed a free 1.0 for being the only utility.
    assert ranks.loc["LONE", "P/B"] < 1.0


def test_near_lows_filter_keeps_only_beaten_down_names():
    df = UNIVERSE.copy()
    df["currentPrice"] = [110.0, 150.0, 195.0]      # GOOD, MID, BAD
    df["fiftyTwoWeekLow"] = 100.0
    df["fiftyTwoWeekHigh"] = 200.0
    kept = screen(df, near_lows=True)
    assert list(kept.index) == ["GOOD"]             # only one in the bottom third
