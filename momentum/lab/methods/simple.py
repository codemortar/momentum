"""Equity-only methods: an anomaly, a trend rule, and the two controls."""

from __future__ import annotations

import hashlib
from datetime import date

from ..broker import Equity, Order
from ..config import FTSE
from .base import Context, Method


def _fund(ctx: Context) -> Equity:
    return Equity(ctx.market.symbol)


def _held(ctx: Context) -> int:
    pos = ctx.account.positions.get(_fund(ctx).key)
    return pos.qty if pos else 0


def _target(ctx: Context, want_long: bool, at: str = "close") -> list[Order]:
    held = _held(ctx)
    if want_long and held == 0:
        price = ctx.market.spot
        qty = int(ctx.account.cash * 0.98 // price)
        return [Order(_fund(ctx), qty=qty, at=at)] if qty > 0 else []
    if not want_long and held != 0:
        return [Order(_fund(ctx), qty=-held, at=at)]
    return []


# --- overnight: buy every close, sell at the next open -------------------------

def overnight(ctx: Context) -> list[Order]:
    orders = []
    held = _held(ctx)
    if held:
        orders.append(Order(_fund(ctx), qty=-held, at="open"))  # today's open is already history
        cash_after = ctx.account.cash + held * float(ctx.market.bars["open"].iloc[-1])
    else:
        cash_after = ctx.account.cash
    qty = int(cash_after * 0.98 // ctx.market.spot)
    if qty > 0:
        orders.append(Order(_fund(ctx), qty=qty, at="close"))
    return orders


# --- sma_cross: 20 over 100 ----------------------------------------------------

def sma_cross(ctx: Context) -> list[Order]:
    closes = ctx.market.bars["close"]
    if len(closes) < 100:
        return []
    want_long = closes.iloc[-20:].mean() > closes.iloc[-100:].mean()
    return _target(ctx, want_long)


# --- random_walk: the control --------------------------------------------------

def random_walk(ctx: Context) -> list[Order]:
    # Seeded by name and date: reproducible, unre-rollable, and a separate coin per market.
    digest = hashlib.sha256(f"{ctx.name}:{ctx.market.date}".encode()).digest()
    return _target(ctx, want_long=digest[0] % 2 == 0)


# --- lunar: the placebo ---------------------------------------------------------

SYNODIC_DAYS = 29.53059
NEW_MOON_EPOCH = date(2000, 1, 6)


def moon_is_waxing(d: date) -> bool:
    phase = ((d - NEW_MOON_EPOCH).days % SYNODIC_DAYS) / SYNODIC_DAYS
    return phase < 0.5


def lunar(ctx: Context) -> list[Order]:
    return _target(ctx, want_long=moon_is_waxing(date.fromisoformat(ctx.market.date)))


METHODS = [
    Method("overnight", "Buy SPY at every close, sell at the next open.", overnight),
    Method("sma_cross", "Long SPY while 20d SMA > 100d SMA, else cash.", sma_cross),
    Method("random_walk", "CONTROL: long or flat by date-seeded coin flip.", random_walk),
    Method("lunar", "PLACEBO: long while the moon waxes, flat while it wanes.", lunar),
    Method("overnight_ftse", "Buy ISF.L (FTSE 100) at every close, sell at the next open.",
           overnight, underlying=FTSE),
    Method("sma_cross_ftse", "Long ISF.L while 20d SMA > 100d SMA, else cash.",
           sma_cross, underlying=FTSE),
    Method("random_walk_ftse", "CONTROL (FTSE): long or flat ISF.L by date-seeded coin flip.",
           random_walk, underlying=FTSE),
]
