"""Trade buffers: don't switch unless the challenger is clearly better.

Without a buffer a strategy will swap its entire portfolio because one asset
edged ahead by a hundredth of a percent — paying full cost for a coin-flip
distinction, then often swapping straight back. A buffer requires the
challenger to beat the *incumbent* by a margin before the trade happens, which
cuts turnover and the cost drag that comes with it.

This needs to know what is currently held, so buffered strategies take
`(prices, held)` rather than just `prices`. That is not lookahead: `held` is
itself the product of earlier decisions made on earlier data, and the engine
threads it through in chronological order (see backtest._decision_schedule).

Each strategy exposes a *preference score* per role — higher means the strategy
would rather hold it — reconstructed from the same numbers the strategy itself
uses, so the buffer margin is expressed in the strategy's own units (annualized
return terms for the momentum strategies, distance above the moving average for
ma_200).
"""

from __future__ import annotations

import pandas as pd

from .config import BONDS, CASH, EQUITIES_INTL, EQUITIES_US, Role
from .strategies import MA_WINDOW, MOMENTUM_LOOKBACK, score_13612w


def scores_buy_and_hold(prices: pd.DataFrame) -> pd.Series:
    """Always prefers US equities, by an unbridgeable margin."""
    return pd.Series(
        {r: (1.0 if r == EQUITIES_US else 0.0) for r in prices.columns}, dtype=float
    )


def scores_ma_200(prices: pd.DataFrame) -> pd.Series:
    """Equities' distance above their own 200-day average; bonds neutral at 0.

    A margin therefore becomes a band around the moving average: price must be
    that far above the average to hold equities, or that far below to bail out.
    """
    p = prices[EQUITIES_US]
    above = p.iloc[-1] / p.iloc[-MA_WINDOW:].mean() - 1.0
    scores = {r: 0.0 for r in prices.columns}
    scores[EQUITIES_US] = float(above)
    return pd.Series(scores, dtype=float)


def _defensive_scores(scores: dict, hurdle: float, columns) -> dict:
    """Non-equity roles sit at the cash hurdle: that is the bar equities must
    clear, so a switch into or out of bonds is measured against it."""
    for role in columns:
        scores.setdefault(role, hurdle)
    return scores


def scores_dual_momentum(prices: pd.DataFrame) -> pd.Series:
    r12 = prices.iloc[-1] / prices.iloc[-(MOMENTUM_LOOKBACK + 1)] - 1.0
    scores = {
        EQUITIES_US: float(r12[EQUITIES_US]),
        EQUITIES_INTL: float(r12[EQUITIES_INTL]),
    }
    return pd.Series(
        _defensive_scores(scores, float(r12[CASH]), prices.columns), dtype=float
    )


def scores_accel_momentum(prices: pd.DataFrame) -> pd.Series:
    score = score_13612w(prices)
    scores = {
        EQUITIES_US: float(score[EQUITIES_US]),
        EQUITIES_INTL: float(score[EQUITIES_INTL]),
    }
    return pd.Series(
        _defensive_scores(scores, float(score[CASH]), prices.columns), dtype=float
    )


def scores_vaa(prices: pd.DataFrame) -> pd.Series:
    """VAA ranks everything it might hold by the same 13612W score."""
    return score_13612w(prices).astype(float)


SCORES = {
    "buy_and_hold": scores_buy_and_hold,
    "ma_200": scores_ma_200,
    "dual_momentum": scores_dual_momentum,
    "accel_momentum": scores_accel_momentum,
    "vaa": scores_vaa,
}


def with_buffer(strategy_fn, score_fn, margin: float):
    """Wrap a strategy so it only switches when the gain clears `margin`.

    `margin` is in score units — for the momentum strategies that is annualized
    return (0.02 = two percentage points); for ma_200 it is distance from the
    moving average. The returned strategy takes `(prices, held)`.
    """
    if margin < 0:
        raise ValueError(f"margin must be >= 0, got {margin}.")

    def buffered(prices: pd.DataFrame, held: Role | None = None) -> dict[Role, float]:
        target = strategy_fn(prices)
        if held is None:
            return target                      # first decision: nothing to hold on to
        challenger = max(target, key=target.get)
        if challenger == held:
            return target
        scores = score_fn(prices)
        if held not in scores.index or challenger not in scores.index:
            return target                      # can't compare; take the signal
        if float(scores[challenger]) - float(scores[held]) < margin:
            return {held: 1.0}                 # not enough in it — stay put
        return target

    buffered.__name__ = f"buffered_{getattr(strategy_fn, '__name__', 'strategy')}"
    return buffered


def buffered_strategy(name: str, strategy_fn, margin: float):
    """Buffered version of a named strategy, using its own preference scores."""
    if name not in SCORES:
        raise ValueError(f"No preference scores defined for strategy {name!r}.")
    return with_buffer(strategy_fn, SCORES[name], margin)
