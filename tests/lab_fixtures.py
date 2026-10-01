"""Synthetic markets for the paper lab, answers known by construction."""

from __future__ import annotations

import pandas as pd

from momentum.lab.data import Market


def make_market(
    date: str = "2026-10-01",
    closes: list[float] | None = None,
    opens: list[float] | None = None,
    chain_rows: list[dict] | None = None,
    symbol: str = "SPY",
) -> Market:
    closes = closes or [100.0] * 120
    opens = opens or closes
    index = pd.bdate_range(end=date, periods=len(closes))
    bars = pd.DataFrame({"open": opens, "close": closes}, index=index)
    chain = pd.DataFrame(
        chain_rows or [], columns=["strike", "bid", "ask", "expiry", "right"]
    )
    return Market(date=date, bars=bars, chain=chain, symbol=symbol)


def put_row(strike: float, bid: float, ask: float, expiry: str) -> dict:
    return {"strike": strike, "bid": bid, "ask": ask, "expiry": expiry, "right": "P"}
