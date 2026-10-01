"""Instruments and honest paper fills: buys at the ask, sells at the bid, costs in full."""

from __future__ import annotations

from dataclasses import dataclass

from .config import DEFAULT_SLIPPAGE_BPS, OPTION_COMMISSION, OPTION_MULTIPLIER, SLIPPAGE_BPS


@dataclass(frozen=True)
class Equity:
    symbol: str

    @property
    def key(self) -> str:
        return f"EQ:{self.symbol}"


@dataclass(frozen=True)
class Option:
    symbol: str
    expiry: str        # ISO date
    strike: float
    right: str         # 'P' or 'C'

    @property
    def key(self) -> str:
        return f"OPT:{self.symbol}:{self.expiry}:{self.strike:g}:{self.right}"


@dataclass(frozen=True)
class Order:
    instrument: Equity | Option
    qty: int           # signed: +buy, -sell
    at: str = "close"  # equities only: "close" or "open"


@dataclass(frozen=True)
class Fill:
    instrument_key: str
    qty: int
    price: float
    cash_delta: float      # effect on the account, costs included
    friction: float        # slippage + half-spread + commission, for reporting


class NoMarketError(Exception):
    """No tradable quote — the order is rejected rather than invented."""


def fill_equity(order: Order, bar) -> Fill:
    base = float(bar[order.at])
    bps = SLIPPAGE_BPS.get(order.instrument.symbol, DEFAULT_SLIPPAGE_BPS)
    slip = base * bps / 1e4
    price = base + slip if order.qty > 0 else base - slip
    return Fill(
        instrument_key=order.instrument.key,
        qty=order.qty,
        price=price,
        cash_delta=-price * order.qty,
        friction=abs(order.qty) * slip,
    )


def fill_option(order: Order, bid: float, ask: float) -> Fill:
    if bid <= 0 or ask <= 0 or ask < bid:
        raise NoMarketError(f"No two-sided market for {order.instrument.key}.")
    price = ask if order.qty > 0 else bid
    commission = OPTION_COMMISSION * abs(order.qty)
    mult = OPTION_MULTIPLIER
    return Fill(
        instrument_key=order.instrument.key,
        qty=order.qty,
        price=price,
        cash_delta=-price * order.qty * mult - commission,
        friction=abs(order.qty) * (ask - bid) / 2 * mult + commission,
    )
