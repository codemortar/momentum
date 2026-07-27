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

import inspect
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


TRADING_DAYS_PER_MONTH = 21


def tranche_decision_days(
    index: pd.DatetimeIndex, n_tranches: int
) -> list[pd.DatetimeIndex]:
    """Decision dates for each of `n_tranches` interleaved monthly schedules.

    Tranche 0 rebalances on the month-end (the original behaviour); tranche k
    rebalances a fraction k/n of a month later. Running equal slices of capital
    on these offset schedules removes *rebalance timing luck* — the arbitrary
    fact that results depend on which day of the month you happen to trade on.
    """
    if n_tranches < 1:
        raise ValueError(f"n_tranches must be >= 1, got {n_tranches}.")
    base = [index.get_loc(d) for d in month_end_trading_days(index)]
    schedules = []
    for k in range(n_tranches):
        offset = round(k * TRADING_DAYS_PER_MONTH / n_tranches)
        shifted = [p + offset for p in base if p + offset < len(index)]
        schedules.append(pd.DatetimeIndex([index[p] for p in shifted]))
    return schedules


def _accepts_holding(strategy_fn: StrategyFn) -> bool:
    """Whether a strategy wants the current holding passed as a second argument.

    Buffered strategies need to know what they already hold to decide whether a
    challenger is worth the trade. That is *not* lookahead: the holding is
    itself derived only from decisions already made on past data.
    """
    try:
        params = inspect.signature(strategy_fn).parameters
    except (TypeError, ValueError):
        return False
    return len(params) >= 2


def _decision_schedule(
    prices: pd.DataFrame,
    strategy_fn: StrategyFn,
    decision_days: pd.DatetimeIndex | None = None,
) -> tuple[dict[pd.Timestamp, dict[Role, float]], dict[pd.Timestamp, dict[Role, float]]]:
    """Build the decision and execution schedules.

    Returns (weights_by_decision_date, target_by_execution_date). A decision is
    made on each decision day (month-ends by default) that has at least
    WARMUP_DAYS of history; it executes on the next trading day. A decision day
    with no following trading day is skipped.
    """
    index = prices.index
    if decision_days is None:
        decision_days = month_end_trading_days(index)
    stateful = _accepts_holding(strategy_fn)
    held: Role | None = None

    decisions: dict[pd.Timestamp, dict[Role, float]] = {}
    executions: dict[pd.Timestamp, dict[Role, float]] = {}
    for d in decision_days:
        pos = index.get_loc(d)
        if pos < WARMUP_DAYS - 1:          # not enough history yet
            continue
        if pos + 1 >= len(index):          # no next day to execute on
            continue
        history = prices.loc[:d]
        target = strategy_fn(history, held) if stateful else strategy_fn(history)
        held = max(target, key=target.get)
        decisions[d] = target
        executions[index[pos + 1]] = target
    return decisions, executions


def run(
    prices: pd.DataFrame,
    strategy_fn: StrategyFn,
    strategy_name: str,
    costs: CostModel,
    start_value: float = 1.0,
    decision_days: pd.DatetimeIndex | None = None,
) -> BacktestResult:
    cols = list(prices.columns)
    decisions, executions = _decision_schedule(prices, strategy_fn, decision_days)
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


def run_tranched(
    prices: pd.DataFrame,
    strategy_fn: StrategyFn,
    strategy_name: str,
    costs: CostModel,
    n_tranches: int,
    start_value: float = 1.0,
) -> BacktestResult:
    """Split capital across `n_tranches` equal slices rebalanced on offset
    monthly schedules, then combine.

    An equal-weighted basket of sub-portfolios is worth the average of their
    normalised curves, so the combined curve is that mean, measured from the
    first day on which every tranche is invested. Trade counts sum; cost is the
    mean, because each tranche's cost is expressed as a fraction of its own
    (1/n-sized) slice.
    """
    if n_tranches == 1:
        return run(prices, strategy_fn, strategy_name, costs, start_value)

    results = [
        run(prices, strategy_fn, strategy_name, costs, start_value, decision_days=days)
        for days in tranche_decision_days(prices.index, n_tranches)
    ]

    start = max(r.equity_curve.index[0] for r in results)
    normalized = []
    for r in results:
        curve = r.equity_curve.loc[start:]
        normalized.append(curve / curve.iloc[0] * start_value)
    combined = pd.concat(normalized, axis=1).mean(axis=1)

    # Portfolio allocation over time: each tranche holds its last decision until
    # its next one, so forward-fill each onto the shared dates before averaging.
    dates = sorted({d for r in results for d in r.weights_history.index})
    frames = [
        r.weights_history.reindex(dates).ffill().fillna(0.0) for r in results
    ]
    weights = sum(frames) / len(frames)
    weights.index.name = "decision_date"

    return BacktestResult(
        strategy=strategy_name,
        equity_curve=combined,
        weights_history=weights,
        n_trades=sum(r.n_trades for r in results),
        total_cost=sum(r.total_cost for r in results) / len(results),
    )
