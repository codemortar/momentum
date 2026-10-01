"""Cash-secured put selling. High win rate by construction; the risk is the rare gap down."""

from __future__ import annotations

from datetime import date

from ..broker import Option, Order
from ..config import UNDERLYING
from .base import Context, Method

TARGET_OTM = 0.95      # strike ~5% below spot, roughly a 30-delta put
MIN_DTE, MAX_DTE = 28, 45
ROLL_DTE = 7           # close this near expiry rather than ride assignment
PROFIT_TAKE = 0.80     # buy back once 80% of the premium is earned


def _dte(expiry: str, today: str) -> int:
    return (date.fromisoformat(expiry) - date.fromisoformat(today)).days


def _open_put(ctx: Context) -> tuple[str, Option] | None:
    for key, pos in ctx.account.positions.items():
        if key.startswith("OPT:") and pos.qty < 0:
            _, symbol, expiry, strike, right = key.split(":")
            return key, Option(symbol, expiry, float(strike), right)
    return None


def decide(ctx: Context) -> list[Order]:
    market = ctx.market
    held = _open_put(ctx)

    if held is not None:
        key, opt = held
        pos = ctx.account.positions[key]
        mark = market.mid(opt.expiry, opt.strike, opt.right)
        worth_closing = (
            _dte(opt.expiry, market.date) <= ROLL_DTE
            or (mark is not None and mark <= pos.avg_price * (1 - PROFIT_TAKE))
        )
        if not worth_closing:
            return []
        return [Order(opt, qty=-pos.qty)]  # buy back the short

    puts = market.chain[
        (market.chain.right == "P") & (market.chain.bid > 0) & (market.chain.ask > 0)
    ].copy()
    if puts.empty:
        return []
    puts["dte"] = puts.expiry.map(lambda e: _dte(e, market.date))
    window = puts[(puts.dte >= MIN_DTE) & (puts.dte <= MAX_DTE)]
    if window.empty:
        return []
    expiry = window.loc[(window.dte - 35).abs().idxmin(), "expiry"]
    candidates = window[(window.expiry == expiry) & (window.strike <= market.spot * TARGET_OTM)]
    if candidates.empty:
        return []
    strike = float(candidates.strike.max())
    if strike * 100 > ctx.account.cash:
        return []  # cash-secured or nothing
    return [Order(Option(UNDERLYING, expiry, strike, "P"), qty=-1)]


METHOD = Method(
    name="theta_puts",
    rule="Sell 1 cash-secured SPY put ~5% OTM at 28-45 DTE; buy back at 80% "
    "profit or 7 DTE, then re-sell.",
    decide=decide,
)
