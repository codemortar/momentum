"""Market snapshot (bars + option chain). Network lives here only, so rules test offline."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class Market:
    date: str                  # trading date of the latest bar (ISO)
    bars: pd.DataFrame         # daily open/close, ascending index
    chain: pd.DataFrame        # columns: expiry, strike, right, bid, ask
    symbol: str = "SPY"
    currency: str = "USD"

    @property
    def spot(self) -> float:
        return float(self.bars["close"].iloc[-1])

    def quote(self, expiry: str, strike: float, right: str) -> tuple[float, float] | None:
        rows = self.chain[
            (self.chain.expiry == expiry)
            & (self.chain.right == right)
            & (self.chain.strike == strike)
        ]
        if rows.empty:
            return None
        row = rows.iloc[0]
        return float(row.bid), float(row.ask)

    def mid(self, expiry: str, strike: float, right: str) -> float | None:
        q = self.quote(expiry, strike, right)
        if q is None or q[0] <= 0 or q[1] <= 0:
            return None
        return (q[0] + q[1]) / 2


def fetch_market(symbol: str, history_days: int = 320, max_dte: int = 55) -> Market:
    from datetime import date as _date

    import yfinance as yf

    ticker = yf.Ticker(symbol)
    bars = ticker.history(period=f"{history_days}d", auto_adjust=False)
    if bars.empty:
        raise RuntimeError(f"No price history for {symbol!r}.")
    bars = bars.rename(columns=str.lower)[["open", "close"]]
    currency = ticker.fast_info.get("currency") or "USD"
    if currency == "GBp":
        # London ETFs quote in pence; pounds keep accounts comparable to USD ones.
        bars = bars / 100.0
        currency = "GBP"

    # SPY has daily expiries, so select by horizon: counting the first N
    # expiries never reaches a 28-45 day window.
    today = bars.index[-1].date()
    expiries = [
        e for e in ticker.options
        if 0 <= (_date.fromisoformat(e) - today).days <= max_dte
    ]
    frames = []
    for expiry in expiries:
        try:
            oc = ticker.option_chain(expiry)
        except Exception:
            continue  # one bad expiry should not sink the snapshot
        for frame, right in ((oc.puts, "P"), (oc.calls, "C")):
            part = frame[["strike", "bid", "ask"]].copy()
            part["expiry"] = expiry
            part["right"] = right
            frames.append(part)
    chain = (
        pd.concat(frames, ignore_index=True)
        if frames
        else pd.DataFrame(columns=["strike", "bid", "ask", "expiry", "right"])
    )
    return Market(date=bars.index[-1].date().isoformat(), bars=bars, chain=chain,
                  symbol=symbol, currency=currency)
