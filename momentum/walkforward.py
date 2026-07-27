"""Walk-forward analysis: would choosing as you went have worked?

A backtest comparison table answers "which strategy was best over this whole
period?" — a question you could only answer at the end, and therefore not one
you could have acted on. Walk-forward answers the useful version instead: pick
the best candidate using only the past N years, run it for the next year,
repeat, and stitch those out-of-sample years together. That curve is what
selecting-as-you-go would actually have earned.

It is the tool that catches a lucky parameter. A candidate that wins in-sample
because of one fortunate trade rarely wins the following year, so the choices
churn and the stitched result falls well short of the hindsight best. A genuine
edge shows up as stable choices and an out-of-sample result close to it.

No lookahead: selection at each step reads only the in-sample window, and the
out-of-sample returns come from decisions the strategy would have made live —
guaranteed by the same property test that protects the backtest, which is why
candidate curves can be computed once over the full history and sliced.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from pandas.tseries.offsets import DateOffset

from . import buffers, config, data, metrics
from .backtest import run_tranched
from .config import CostModel
from .strategies import STRATEGIES


@dataclass(frozen=True)
class Fold:
    is_start: pd.Timestamp     # in-sample (selection) window
    is_end: pd.Timestamp
    oos_end: pd.Timestamp      # out-of-sample runs from just after is_end
    chosen: str
    is_metric: float
    oos_return: float


@dataclass
class WalkForwardResult:
    equity_curve: pd.Series           # stitched out-of-sample only
    folds: list[Fold]
    candidate_curves: dict[str, pd.Series]

    @property
    def churn(self) -> int:
        """How many times the selection changed between consecutive folds."""
        picks = [f.chosen for f in self.folds]
        return sum(1 for a, b in zip(picks, picks[1:]) if a != b)


def make_folds(
    index: pd.DatetimeIndex, train_years: int, test_years: int
) -> list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]]:
    """Rolling (is_start, is_end, oos_end) windows covering the index."""
    if train_years < 1 or test_years < 1:
        raise ValueError("train_years and test_years must both be >= 1.")
    first, last = index[0], index[-1]
    folds = []
    is_start = first
    while True:
        is_end = is_start + DateOffset(years=train_years)
        if is_end >= last:
            break
        oos_end = min(is_end + DateOffset(years=test_years), last)
        if oos_end <= is_end:
            break
        folds.append((is_start, is_end, oos_end))
        is_start = is_start + DateOffset(years=test_years)
    return folds


def strategy_candidates() -> dict:
    """Every strategy, as a selection problem: which one should I be running?"""
    return dict(STRATEGIES)


def buffer_candidates(strategy_name: str, margins_bps=(0, 100, 200, 300, 500, 800)) -> dict:
    """One candidate per buffer margin for a single strategy — the setting most
    at risk of being tuned to noise, and so most worth walking forward."""
    fn = STRATEGIES[strategy_name]
    out = {}
    for bps in margins_bps:
        label = f"{strategy_name}@{bps}bps"
        out[label] = (
            fn if bps == 0 else buffers.buffered_strategy(strategy_name, fn, bps / 1e4)
        )
    return out


def walk_forward(
    prices: pd.DataFrame,
    candidates: dict,
    costs: CostModel,
    train_years: int = 5,
    test_years: int = 1,
    tranches: int = 1,
    metric: str = "sharpe",
) -> WalkForwardResult:
    """Select on each in-sample window, apply to the next out-of-sample window."""
    cash = prices["cash"]

    curves = {
        label: run_tranched(prices, fn, label, costs, tranches).equity_curve
        for label, fn in candidates.items()
    }
    returns = {label: curve.pct_change() for label, curve in curves.items()}
    common = min(curve.index[0] for curve in curves.values())

    def score(curve_slice: pd.Series) -> float:
        if len(curve_slice) < 2:
            return float("-inf")
        if metric == "cagr":
            return metrics.cagr(curve_slice)
        return metrics.sharpe(curve_slice, cash.loc[curve_slice.index])

    folds: list[Fold] = []
    pieces: list[pd.Series] = []
    for is_start, is_end, oos_end in make_folds(
        prices.loc[common:].index, train_years, test_years
    ):
        scored = {
            label: score(curve.loc[is_start:is_end]) for label, curve in curves.items()
        }
        best = max(scored, key=lambda k: scored[k])

        # Out-of-sample is strictly after the selection window.
        oos = returns[best].loc[is_end:oos_end].iloc[1:]
        if oos.empty:
            continue
        pieces.append(oos)
        folds.append(
            Fold(
                is_start=is_start, is_end=is_end, oos_end=oos_end, chosen=best,
                is_metric=scored[best],
                oos_return=float((1.0 + oos).prod() - 1.0),
            )
        )

    if not pieces:
        raise ValueError(
            "No complete walk-forward folds — the history is shorter than "
            f"train_years ({train_years}) + test_years ({test_years})."
        )
    stitched = pd.concat(pieces)
    curve = (1.0 + stitched).cumprod()
    return WalkForwardResult(
        equity_curve=curve, folds=folds, candidate_curves=curves
    )


def format_report(result: WalkForwardResult, prices: pd.DataFrame) -> str:
    cash = prices["cash"]
    curve = result.equity_curve
    span = curve.index

    lines = [
        f"Out-of-sample period: {span[0].date()} -> {span[-1].date()} "
        f"({len(result.folds)} folds)",
        "",
        f"  {'in-sample window':26s}{'chose':24s}{'OOS return':>11s}",
        "  " + "-" * 61,
    ]
    for f in result.folds:
        window = f"{f.is_start.date()} - {f.is_end.date()}"
        lines.append(f"  {window:26s}{f.chosen:24s}{f.oos_return:>11.2%}")

    def row(label: str, c: pd.Series) -> str:
        aligned = cash.loc[c.index]
        return (
            f"  {label:24s}{metrics.cagr(c):>9.2%}{metrics.max_drawdown(c):>10.2%}"
            f"{metrics.sharpe(c, aligned):>9.2f}"
        )

    lines += [
        "",
        f"  {'':24s}{'CAGR':>9s}{'MaxDD':>10s}{'Sharpe':>9s}",
        "  " + "-" * 52,
        row("WALK-FORWARD", curve),
        "",
    ]

    # Each candidate measured over the same out-of-sample span, so the
    # comparison is like-for-like: this is what simply committing to one
    # candidate up front would have produced.
    held = {}
    for label, c in result.candidate_curves.items():
        segment = c.loc[span[0]:span[-1]]
        if len(segment) > 1:
            held[label] = segment / segment.iloc[0]
    for label, c in sorted(held.items(), key=lambda kv: -metrics.cagr(kv[1])):
        lines.append(row(f"always {label}", c))

    best_label = max(held, key=lambda k: metrics.cagr(held[k]))
    wf_cagr = metrics.cagr(curve)
    gap = wf_cagr - metrics.cagr(held[best_label])
    lines += [
        "",
        f"  Selection changed {result.churn} time(s) across "
        f"{len(result.folds)} folds.",
        f"  Best in hindsight was '{best_label}'; walk-forward gave up "
        f"{-gap:.2%}/yr of CAGR against it.",
        "",
        "  Hindsight is not available in advance — the walk-forward row is the",
        "  honest estimate. Frequent churn plus a large gap means the in-sample",
        "  winner was noise; stable choices and a small gap mean a real edge.",
    ]
    return "\n".join(lines)


def run_walkforward(
    universe_name: str,
    select: str,
    strategy: str | None,
    train_years: int,
    test_years: int,
    cost_bps: float,
    fee_bps: float,
    tranches: int,
    metric: str,
) -> int:
    universe = config.get_universe(universe_name)
    prices = data.load_prices(universe)
    costs = CostModel(trade_cost_bps=cost_bps, annual_fee_bps=fee_bps)

    if select == "buffers":
        name = strategy or "accel_momentum"
        if name not in STRATEGIES:
            print(f"Unknown strategy {name!r}. Choose from {sorted(STRATEGIES)}.")
            return 2
        candidates = buffer_candidates(name)
        what = f"buffer margin for {name}"
    else:
        candidates = strategy_candidates()
        what = "strategy"

    try:
        result = walk_forward(
            prices, candidates, costs, train_years, test_years, tranches, metric
        )
    except ValueError as exc:
        print(str(exc))
        return 2

    print(
        f"\nWalk-forward: choosing {what} on {train_years}y of history, "
        f"applying for {test_years}y, by {metric}."
    )
    print(
        f"Universe: {universe.name}   Cost: {cost_bps:.0f}bps/trade"
        + (f"   Tranches: {tranches}" if tranches > 1 else "")
        + "\n"
    )
    print(format_report(result, prices))
    return 0
