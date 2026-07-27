"""Configuration: universes (role -> ticker maps) and the cost model.

The central design idea: downstream code (strategies, engine, metrics) only ever
sees semantic *roles* as DataFrame columns, never ticker symbols. This module is
the single place that maps roles to real yfinance tickers, which is what lets one
strategy codebase run unchanged on both the US (validation) and UK (execution)
universes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
    """A named set of assets, mapping each semantic role to a yfinance ticker.

    `history` optionally names older proxies for a role, newest first. ETFs are
    young — GLD starts in 2004, IEF in 2002 — so a backtest limited to them
    never sees a real bear market beyond 2008. Splicing older total-return
    mutual funds on behind each ETF buys back decades (see data.splice_series).
    """

    name: str
    tickers: dict[Role, str]
    start: str  # earliest date to fetch, ISO format
    history: dict[Role, tuple[str, ...]] = field(default_factory=dict)

    def all_tickers(self) -> list[str]:
        """Every symbol this universe needs downloaded, primaries and proxies."""
        out = list(self.tickers.values())
        for chain in self.history.values():
            out.extend(chain)
        return out


@dataclass(frozen=True)
class CostModel:
    """Trading friction. Defaults model a cheap UK ISA broker."""

    trade_cost_bps: float = 10.0  # charged on traded value, per rebalance
    annual_fee_bps: float = 0.0   # optional fund fee drag, applied daily


# Warmup: the number of rows a strategy needs before its first valid decision.
# Dual momentum compares today against 253 trading days ago, so it needs 254
# rows — the lookback plus the starting bar. The engine makes no decision until
# at least this many are available. (Off-by-one here is easy to miss: a universe
# only trips it when a month-end falls on exactly the boundary day.)
WARMUP_DAYS = 254


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
    # Extended US: the same roles, with older total-return mutual funds spliced
    # in behind each ETF. Gold is the binding constraint (no free daily
    # total-return series before the 2000 futures contract), so this starts
    # around 2000 — which is enough to include the dot-com bear market that the
    # plain 'us' universe misses entirely.
    "us_ext": Universe(
        name="us_ext",
        tickers={
            EQUITIES_US: "SPY",
            EQUITIES_INTL: "EFA",
            BONDS: "IEF",
            GOLD: "GLD",
            CASH: "^IRX",
        },
        start="1980-01-01",
        history={
            EQUITIES_US: ("VFINX",),    # Vanguard 500 Index fund, 1980
            EQUITIES_INTL: ("VGTSX",),  # Vanguard Total International, 1996
            BONDS: ("FGOVX",),          # Fidelity Government Income, 1980
            GOLD: ("GC=F",),            # gold futures, 2000
        },
    ),
    # Long US: drops gold and international, the two roles with no deep free
    # history, in exchange for reaching back to 1980 — covering 1987, the early
    # 1990s and the dot-com bust. Only strategies that need just US equities,
    # bonds and cash can run here (see strategies.REQUIRED_ROLES).
    "us_long": Universe(
        name="us_long",
        tickers={
            EQUITIES_US: "SPY",
            BONDS: "IEF",
            CASH: "^IRX",
        },
        start="1980-01-01",
        history={
            EQUITIES_US: ("VFINX",),
            BONDS: ("FGOVX",),
        },
    ),
}


def get_universe(name: str) -> Universe:
    if name not in UNIVERSES:
        raise ValueError(f"Unknown universe {name!r}. Choose from {sorted(UNIVERSES)}.")
    return UNIVERSES[name]
