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
  * accel_momentum: dual momentum, but scored by Keller's 13612W blend (average of
                   annualized 1/3/6/12-month returns) so a fresh crash outweighs a
                   good year and the exit comes in weeks rather than months.
  * vaa          : Vigilant Asset Allocation (Keller). If every risk asset
                   (US/intl equities, gold) has a positive 13612W score, hold the
                   best one; otherwise hold the better of bonds and cash.
"""

from __future__ import annotations

import pandas as pd

from .config import BONDS, CASH, EQUITIES_INTL, EQUITIES_US, GOLD, Role

MA_WINDOW = 200
MOMENTUM_LOOKBACK = 253  # trading days ~= 12 months

# Keller's 13612W score: the average of the *annualized* 1-, 3-, 6- and 12-month
# returns. Keys are lookbacks in trading days, values the annualization factor —
# so the most recent month carries 12x the weight of the trailing year.
LOOKBACKS_13612W = {21: 12.0, 63: 4.0, 126: 2.0, MOMENTUM_LOOKBACK: 1.0}

VAA_RISK = (EQUITIES_US, EQUITIES_INTL, GOLD)
VAA_DEFENSIVE = (BONDS, CASH)


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


def score_13612w(prices: pd.DataFrame) -> pd.Series:
    """13612W momentum score for every column. Zero for a flat series."""
    now = prices.iloc[-1]
    terms = [
        ann * (now / prices.iloc[-(lb + 1)] - 1.0)
        for lb, ann in LOOKBACKS_13612W.items()
    ]
    return sum(terms) / len(terms)


def accel_momentum(prices: pd.DataFrame) -> dict[Role, float]:
    if len(prices) < MOMENTUM_LOOKBACK + 1:
        raise ValueError(
            f"accel_momentum needs >= {MOMENTUM_LOOKBACK + 1} rows, got {len(prices)}."
        )
    score = score_13612w(prices)
    winner = EQUITIES_US if score[EQUITIES_US] >= score[EQUITIES_INTL] else EQUITIES_INTL
    if score[winner] > score[CASH]:
        return {winner: 1.0}
    return {BONDS: 1.0}


def vaa(prices: pd.DataFrame) -> dict[Role, float]:
    if len(prices) < MOMENTUM_LOOKBACK + 1:
        raise ValueError(f"vaa needs >= {MOMENTUM_LOOKBACK + 1} rows, got {len(prices)}.")
    score = score_13612w(prices)
    if all(score[r] > 0.0 for r in VAA_RISK):
        return {max(VAA_RISK, key=lambda r: score[r]): 1.0}
    return {max(VAA_DEFENSIVE, key=lambda r: score[r]): 1.0}


STRATEGIES = {
    "buy_and_hold": buy_and_hold,
    "ma_200": ma_200,
    "dual_momentum": dual_momentum,
    "accel_momentum": accel_momentum,
    "vaa": vaa,
}
