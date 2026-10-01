"""Daily market snapshots, so new methods can be replayed against the quotes the
contest actually saw. Irreplaceable: Yahoo serves only today's option chain."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config
from .data import Market


def _dir(bar_date: str) -> Path:
    return config.SNAPSHOT_DIR / bar_date


def save(market: Market) -> bool:
    """Write this market's bar and chain once per bar date; never overwrite."""
    folder = _dir(market.date)
    bar_path = folder / f"{market.symbol}_bar.csv"
    if bar_path.exists():
        return False
    folder.mkdir(parents=True, exist_ok=True)
    if not market.chain.empty:
        market.chain.to_csv(folder / f"{market.symbol}_chain.csv.gz", index=False)
    bar = market.bars.iloc[[-1]].copy()
    bar.index = [market.date]
    bar["currency"] = market.currency
    bar.to_csv(bar_path, index_label="date")  # written last: its presence marks a complete snapshot
    return True


def load(symbol: str, bar_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(bar, chain) for one market on one date; chain is empty if none was saved."""
    folder = _dir(bar_date)
    bar = pd.read_csv(folder / f"{symbol}_bar.csv", index_col="date")
    chain_path = folder / f"{symbol}_chain.csv.gz"
    chain = (
        pd.read_csv(chain_path, dtype={"expiry": str})
        if chain_path.exists()
        else pd.DataFrame(columns=["strike", "bid", "ask", "expiry", "right"])
    )
    return bar, chain


def saved_dates(symbol: str) -> list[str]:
    root = config.SNAPSHOT_DIR
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / f"{symbol}_bar.csv").exists())
