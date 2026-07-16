"""Configuration: universes (role -> ticker maps) and the cost model.

The central design idea: downstream code (strategies, engine, metrics) only ever
sees semantic *roles* as DataFrame columns, never ticker symbols. This module is
the single place that maps roles to real yfinance tickers, which is what lets one
strategy codebase run unchanged on both the US (validation) and UK (execution)
universes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# --- Paths (repo-relative, so runs are reproducible from anywhere) -----------
ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT / "data" / "cache"
OUTPUT_DIR = ROOT / "output"
STATE_DIR = ROOT / "state"

# --- Roles -------------------------------------------------------------------
# A Role is just a string, but these are the only valid values. Strategies refer
# to assets by role; config maps role -> ticker per universe.
Role = str
EQUITIES_US = "equities_us"
EQUITIES_INTL = "equities_intl"
BONDS = "bonds"
GOLD = "gold"
CASH = "cash"


@dataclass(frozen=True)
class Universe:
    """A named set of assets, mapping each semantic role to a yfinance ticker."""

    name: str
    tickers: dict[Role, str]
    start: str  # earliest date to fetch, ISO format


@dataclass(frozen=True)
class CostModel:
    """Trading friction. Defaults model a cheap UK ISA broker."""

    trade_cost_bps: float = 10.0  # charged on traded value, per rebalance
    annual_fee_bps: float = 0.0   # optional fund fee drag, applied daily


# Warmup: strategies need up to ~12 months of history before their first valid
# decision (dual momentum uses a 253-trading-day lookback). The engine will not
# make decisions until at least this many rows are available.
WARMUP_DAYS = 253


UNIVERSES: dict[str, Universe] = {
    # US universe: long history for genuine validation (~25 years).
    # '^IRX' is the 13-week T-bill *yield* (a rate, not a price); data.py converts
    # it into a synthetic total-return cash index. It reaches back to the 1990s,
    # where a price-based cash ETF like BIL (2007) cannot.
    "us": Universe(
        name="us",
        tickers={
            EQUITIES_US: "SPY",
            EQUITIES_INTL: "EFA",
            BONDS: "IEF",
            GOLD: "GLD",
            CASH: "^IRX",
        },
        start="2000-01-01",
    ),
    # UK universe: the actual London-listed UCITS ETFs you would buy in an ISA.
    # Short history (~10 years) — used as a sanity check that the exact same code
    # produces coherent results on the real instruments, NOT as validation.
    "uk": Universe(
        name="uk",
        tickers={
            EQUITIES_US: "VUSA.L",
            EQUITIES_INTL: "VWRP.L",
            BONDS: "VGOV.L",
            GOLD: "SGLN.L",
            CASH: "ERNS.L",  # iShares £ Ultrashort Bond — GBP cash-like, history to 2013
        },
        start="2012-01-01",
    ),
}


def get_universe(name: str) -> Universe:
    if name not in UNIVERSES:
        raise ValueError(f"Unknown universe {name!r}. Choose from {sorted(UNIVERSES)}.")
    return UNIVERSES[name]
