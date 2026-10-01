"""Capped-loss put selling (a bull put spread), sized to the £1,000 pilot.

Same short leg as theta_puts, plus a cheaper put ~$5 lower that caps the worst
loss at the strike gap minus the credit. Paper-traded on SPY's chain as a proxy
for the equivalent XSP spread a UK broker would offer (same scale, $5 wide).
"""

from __future__ import annotations

from ..broker import Option, Order
from ..config import OPTION_COMMISSION, OPTION_MULTIPLIER, PILOT_CAPITAL, UNDERLYING
from .base import Context, Method
from .theta_puts import MAX_DTE, MIN_DTE, PROFIT_TAKE, ROLL_DTE, TARGET_OTM, _dte

WIDTH = 5.0


def _held_legs(ctx: Context) -> tuple[Option, Option] | None:
    short = long = None
    for key, pos in ctx.account.positions.items():
        if not key.startswith("OPT:"):
            continue
        _, symbol, expiry, strike, right = key.split(":")
        opt = Option(symbol, expiry, float(strike), right)
        if pos.qty < 0:
            short = opt
        else:
            long = opt
    return (short, long) if short and long else None


def _manage(ctx: Context, short: Option, long: Option) -> list[Order]:
    market, positions = ctx.market, ctx.account.positions
    credit = positions[short.key].avg_price - positions[long.key].avg_price
    s_mid = market.mid(short.expiry, short.strike, "P")
    l_mid = market.mid(long.expiry, long.strike, "P")
    near_expiry = _dte(short.expiry, market.date) <= ROLL_DTE
    profit_taken = (s_mid is not None and l_mid is not None
                    and s_mid - l_mid <= credit * (1 - PROFIT_TAKE))
    if not (near_expiry or profit_taken):
        return []
    return [Order(short, qty=1), Order(long, qty=-1)]


def decide(ctx: Context) -> list[Order]:
    held = _held_legs(ctx)
    if held is not None:
        return _manage(ctx, *held)
    if any(k.startswith("OPT:") for k in ctx.account.positions):
        return []  # a lone leg (e.g. one expired first) is left to settle, never re-paired

    market = ctx.market
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
    same = window[window.expiry == expiry]
    shorts = same[same.strike <= market.spot * TARGET_OTM]
    if shorts.empty:
        return []
    short_row = shorts.loc[shorts.strike.idxmax()]
    longs = same[same.strike <= short_row.strike - WIDTH]
    if longs.empty:
        return []
    long_row = longs.loc[longs.strike.idxmax()]

    # Far-touch credit, and the worst case it buys: strike gap minus credit, plus fees.
    credit = float(short_row.bid) - float(long_row.ask)
    if credit <= 0:
        return []
    width = float(short_row.strike) - float(long_row.strike)
    max_loss = (width - credit) * OPTION_MULTIPLIER + 4 * OPTION_COMMISSION
    if max_loss > ctx.account.cash:
        return []
    return [
        Order(Option(UNDERLYING, expiry, float(short_row.strike), "P"), qty=-1),
        Order(Option(UNDERLYING, expiry, float(long_row.strike), "P"), qty=1),
    ]


METHOD = Method(
    name="theta_spread",
    rule="Sell 1 SPY put ~5% OTM and buy 1 ~$5 lower, 28-45 DTE, max loss "
    "covered by cash; close both at 80% profit or 7 DTE. $1,300 (~£1,000) account.",
    decide=decide,
    capital=PILOT_CAPITAL,
)
