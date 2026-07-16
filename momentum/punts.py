"""Punt of the month: an explicitly-unvalidated bit of fun (see SPEC).

Picks the single strongest-13612W-momentum share from a fixed list of liquid
FTSE-100 names. The pick is mechanical and transparent, but it is NOT a
validated signal: single names gap through trend rules, this is never
backtested, and the candidate list (chosen in 2026) has survivorship bias by
construction. The email section says so out loud. Opt-in via MOMENTUM_PUNT;
failures must degrade to a notice line and never break the signal.
"""

from __future__ import annotations

import pandas as pd

from .strategies import MOMENTUM_LOOKBACK, score_13612w

# A fixed list of large, liquid London-listed shares. Hardcoded on purpose:
# deterministic, no index-membership lookups, and honest about its bias — these
# are 2026's survivors, which is part of why the punt is unvalidated fun.
PUNT_CANDIDATES = [
    "AZN.L", "SHEL.L", "HSBA.L", "ULVR.L", "BP.L",
    "GSK.L", "RIO.L", "REL.L", "LSEG.L", "DGE.L",
    "BATS.L", "BA.L", "RR.L", "GLEN.L", "NG.L",
    "VOD.L", "TSCO.L", "BARC.L", "LLOY.L", "PRU.L",
]


def pick_punt(prices: pd.DataFrame) -> tuple[str, float]:
    """The candidate with the strongest 13612W momentum. Pure, so testable.

    Columns without enough history for the 12-month lookback are ignored;
    raises if none qualify.
    """
    usable = prices.dropna(axis="columns")
    if len(usable) < MOMENTUM_LOOKBACK + 1 or usable.empty:
        raise ValueError(
            f"Punt scoring needs >= {MOMENTUM_LOOKBACK + 1} rows for at least "
            f"one candidate; got {len(usable)} rows x {len(usable.columns)} usable."
        )
    scores = score_13612w(usable)
    winner = scores.idxmax()
    return winner, float(scores[winner])


def fetch_candidate_prices() -> pd.DataFrame:
    """Adjusted closes for the candidates, ~2 years. Network, delivery-time,
    best-effort — callers must guard (see SPEC: never break the signal)."""
    import yfinance as yf

    df = yf.download(
        PUNT_CANDIDATES,
        period="2y",
        auto_adjust=True,
        progress=False,
        actions=False,
    )["Close"]
    if isinstance(df, pd.Series):  # single-ticker shape, defensively
        df = df.to_frame()
    return df.sort_index().ffill()


def punt_section() -> list[str]:
    """Email lines for the punt of the month. Raises on any failure; the
    caller degrades gracefully."""
    prices = fetch_candidate_prices()
    ticker, score = pick_punt(prices)
    scored = len(prices.dropna(axis="columns").columns)
    return [
        "PUNT OF THE MONTH — for fun only, NOT a validated signal:",
        f"  {ticker} has the strongest 13612W momentum ({score:+.1%}) of the",
        f"  {scored} scorable names on a fixed list of {len(PUNT_CANDIDATES)} "
        "FTSE-100 shares.",
        "  Single shares gap through trend rules and this pick is not backtested:",
        "  treat it as a lottery ticket from your capped punt pot, never ISA-core",
        "  money. It is excluded from the ledger.",
    ]
