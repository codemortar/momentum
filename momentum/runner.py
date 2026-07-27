"""Orchestration glue between the CLI and the engine/report modules.

Kept separate from __main__ (argument parsing) and backtest (pure engine) so each
piece stays small and independently testable.
"""

from __future__ import annotations

from . import buffers, config, data, metrics, report
from .backtest import BacktestResult, run_tranched
from .config import CostModel
from .strategies import REQUIRED_ROLES, STRATEGIES, runnable_strategies


def run_backtest(
    universe_name: str,
    strategy: str | None,
    cost_bps: float,
    fee_bps: float,
    plot: bool,
    tranches: int = 1,
    buffer_bps: float = 0.0,
) -> int:
    universe = config.get_universe(universe_name)
    prices = data.load_prices(universe)
    costs = CostModel(trade_cost_bps=cost_bps, annual_fee_bps=fee_bps)

    available = runnable_strategies(prices.columns)
    if strategy is None:
        names = available
        skipped = [n for n in STRATEGIES if n not in available]
        if skipped:
            missing = sorted(
                set().union(*(REQUIRED_ROLES[n] for n in skipped)) - set(prices.columns)
            )
            print(
                f"Skipping {', '.join(skipped)}: universe '{universe.name}' has no "
                f"{', '.join(missing)}.\n"
            )
    else:
        if strategy not in STRATEGIES:
            print(f"Unknown strategy {strategy!r}. Choose from {sorted(STRATEGIES)}.")
            return 2
        if strategy not in available:
            missing = sorted(REQUIRED_ROLES[strategy] - set(prices.columns))
            print(
                f"{strategy!r} needs {', '.join(missing)}, which universe "
                f"'{universe.name}' does not have."
            )
            return 2
        names = [strategy]

    margin = buffer_bps / 1e4

    def strategy_fn(name: str):
        if margin <= 0:
            return STRATEGIES[name]
        return buffers.buffered_strategy(name, STRATEGIES[name], margin)

    def backtest(name: str, cost_model: CostModel) -> BacktestResult:
        return run_tranched(prices, strategy_fn(name), name, cost_model, tranches)

    results: list[BacktestResult] = [backtest(name, costs) for name in names]

    # Zero-cost counterfactual, only to report the honest annual cost drag on CAGR.
    free = CostModel(trade_cost_bps=0.0, annual_fee_bps=0.0)
    cost_drag = {
        name: metrics.cagr(backtest(name, free).equity_curve)
        - metrics.cagr(res.equity_curve)
        for name, res in zip(names, results)
    }

    span = results[0].equity_curve.index
    extras = ""
    if tranches > 1:
        extras += f"   Tranches: {tranches}"
    if margin > 0:
        extras += f"   Buffer: {buffer_bps:.0f}bps"
    header = (
        f"Universe: {universe.name}   Period: {span[0].date()} -> {span[-1].date()}   "
        f"Cost: {cost_bps:.0f}bps/trade, {fee_bps:.0f}bps/yr fee   "
        f"Data cached: {data.cache_date(universe)}{extras}"
    )
    print(report.comparison_table(results, prices["cash"], header, cost_drag))

    if plot:
        config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        suffix = "all" if strategy is None else strategy
        if tranches > 1:
            suffix += f"_t{tranches}"
        if margin > 0:
            suffix += f"_b{buffer_bps:.0f}"
        out = config.OUTPUT_DIR / f"{universe.name}_{suffix}.png"
        report.save_plot(results, out, title=f"{universe.name} — {suffix}")
        print(f"\nSaved plot: {out}")
    return 0
