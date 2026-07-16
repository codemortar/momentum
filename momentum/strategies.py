"""Strategies: pure functions `prices -> target weights`.

Each strategy takes a price DataFrame (role-named columns) and returns the target
allocation *as of the last row*. It has no concept of "backtest" versus "live":
the backtest calls it with history truncated to each decision date, and the live
signal calls it with history truncated to the latest completed month-end. Same
function, same computation — which is exactly why a validated backtest and the
signal you act on are guaranteed to agree.

Rules (must not be peeked past the last row — that would be lookahead):
  * buy_and_hold : always 100% US equities (the baseline).
  * ma_200       : 100% equities if the last close is strictly above its 200-day
                   moving average, else 100% bonds.
  * dual_momentum: Antonacci GEM. Compare US vs international equities by trailing
                   12-month (253 trading day) return; hold the winner, but only if
                   it also beat cash over the same window — otherwise hold bonds.
"""

from __future__ import annotations

import pandas as pd

from .config import BONDS, CASH, EQUITIES_INTL, EQUITIES_US, Role

MA_WINDOW = 200
MOMENTUM_LOOKBACK = 253  # trading days ~= 12 months


def buy_and_hold(prices: pd.DataFrame) -> dict[Role, float]:
    return {EQUITIES_US: 1.0}


def ma_200(prices: pd.DataFrame) -> dict[Role, float]:
    p = prices[EQUITIES_US]
    if len(p) < MA_WINDOW:
        raise ValueError(f"ma_200 needs >= {MA_WINDOW} rows, got {len(p)}.")
    ma = p.iloc[-MA_WINDOW:].mean()
    return {EQUITIES_US: 1.0} if p.iloc[-1] > ma else {BONDS: 1.0}


def dual_momentum(prices: pd.DataFrame) -> dict[Role, float]:
    if len(prices) < MOMENTUM_LOOKBACK + 1:
        raise ValueError(
            f"dual_momentum needs >= {MOMENTUM_LOOKBACK + 1} rows, got {len(prices)}."
        )
    now = prices.iloc[-1]
    then = prices.iloc[-(MOMENTUM_LOOKBACK + 1)]
    r12 = now / then - 1.0
    winner = EQUITIES_US if r12[EQUITIES_US] >= r12[EQUITIES_INTL] else EQUITIES_INTL
    if r12[winner] > r12[CASH]:
        return {winner: 1.0}
    return {BONDS: 1.0}


STRATEGIES = {
    "buy_and_hold": buy_and_hold,
    "ma_200": ma_200,
    "dual_momentum": dual_momentum,
}
