"""The backtest engine.

Two invariants define this file and must never be relaxed (see CLAUDE.md):

  1. DECIDE at a month-end close (day t); EXECUTE at the next trading day's
     close (day t+1). A strategy therefore never trades on information from the
     same bar it acted on. This is the primary defence against lookahead bias.

  2. A strategy is only ever shown price history truncated to its decision date:
     `strategy_fn(prices.loc[:t])`. It is structurally unable to see the future.

Accounting is units-based: we hold a number of "shares" per role and mark them to
market daily, so intra-month price drift between rebalances is real rather than
assumed away.

Cost convention: on each rebalance we charge `trade_cost_bps` once on the
*reallocated notional* — the sum of position increases (equivalently, the amount
of money that moves). A full switch of the whole portfolio from one role to
another costs the bps once, matching how a retail investor thinks about "0.1%
per trade". An optional `annual_fee_bps` erodes holdings a little each day.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from .config import CostModel, Role, WARMUP_DAYS

# A strategy: full-history-so-far -> target weights (summing to 1.0).
StrategyFn = Callable[[pd.DataFrame], dict[Role, float]]


@dataclass
class BacktestResult:
    strategy: str
    equity_curve: pd.Series          # daily portfolio value, starts at start_value
    weights_history: pd.DataFrame    # index = decision dates, columns = roles
    n_trades: int                    # rebalances that actually traded
    total_cost: float                # cumulative cost, in start-value units


def month_end_trading_days(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The last actual trading day of each calendar month present in `index`."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).last().values)


def _decision_schedule(
    prices: pd.DataFrame, strategy_fn: StrategyFn
) -> tuple[dict[pd.Timestamp, dict[Role, float]], dict[pd.Timestamp, dict[Role, float]]]:
    """Build the decision and execution schedules.

    Returns (weights_by_decision_date, target_by_execution_date). A decision is
    made on each month-end that has at least WARMUP_DAYS of history; it executes
    on the next trading day. The final month-end is skipped if it has no next day.
    """
    index = prices.index
    decisions: dict[pd.Timestamp, dict[Role, float]] = {}
    executions: dict[pd.Timestamp, dict[Role, float]] = {}
    for d in month_end_trading_days(index):
        pos = index.get_loc(d)
        if pos < WARMUP_DAYS - 1:          # not enough history yet
            continue
        if pos + 1 >= len(index):          # no next day to execute on
            continue
        target = strategy_fn(prices.loc[:d])
        decisions[d] = target
        executions[index[pos + 1]] = target
    return decisions, executions


def run(
    prices: pd.DataFrame,
    strategy_fn: StrategyFn,
    strategy_name: str,
    costs: CostModel,
    start_value: float = 1.0,
) -> BacktestResult:
    cols = list(prices.columns)
    decisions, executions = _decision_schedule(prices, strategy_fn)
    if not executions:
        raise ValueError(
            f"No executable decisions for {strategy_name!r} — need > {WARMUP_DAYS} "
            "days of history plus one execution day."
        )

    index = prices.index
    first_exec = min(executions)
    start_pos = index.get_loc(first_exec)
    fee_daily = costs.annual_fee_bps / 1e4 / 252.0

    units: dict[Role, float] = {r: 0.0 for r in cols}
    n_trades = 0
    total_cost = 0.0
    curve = pd.Series(index=index[start_pos:], dtype=float)

    for i in range(start_pos, len(index)):
        day = index[i]
        px = prices.iloc[i]

        if i == start_pos:
            # Initial deployment into the first signal. Not a "trade": the curve
            # begins already invested, so buy-and-hold with zero costs exactly
            # reproduces the normalized price series.
            target = executions[day]
            units = {r: target.get(r, 0.0) * start_value / px[r] for r in cols}
        else:
            if fee_daily:
                units = {r: u * (1.0 - fee_daily) for r, u in units.items()}
            if day in executions:
                value = sum(units[r] * px[r] for r in cols)
                target = executions[day]
                current = {r: units[r] * px[r] for r in cols}
                # Reallocated notional = sum of position *increases*.
                buys = sum(max(target.get(r, 0.0) * value - current[r], 0.0) for r in cols)
                if buys > value * 1e-9:
                    cost = buys * costs.trade_cost_bps / 1e4
                    value -= cost
                    total_cost += cost
                    n_trades += 1
                    units = {r: target.get(r, 0.0) * value / px[r] for r in cols}

        curve.iloc[i - start_pos] = sum(units[r] * px[r] for r in cols)

    weights_history = pd.DataFrame.from_dict(decisions, orient="index").reindex(columns=cols).fillna(0.0)
    weights_history.index.name = "decision_date"
    return BacktestResult(
        strategy=strategy_name,
        equity_curve=curve,
        weights_history=weights_history,
        n_trades=n_trades,
        total_cost=total_cost,
    )
