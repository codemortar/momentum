"""Reporting: a plain-text comparison table and an equity/drawdown PNG.

No tabulate/seaborn dependency — str.format columns and bare matplotlib.
"""

from __future__ import annotations

import pandas as pd

from . import metrics
from .backtest import BacktestResult


def comparison_table(
    results: list[BacktestResult],
    cash_curve: pd.Series,
    header: str,
    cost_drag: dict[str, float] | None = None,
) -> str:
    """Render the comparison table.

    `cost_drag` maps strategy name -> annualized CAGR reduction caused by trading
    costs (gross CAGR minus net CAGR). This is the honest "how much do fees hurt
    my return per year" figure — far more meaningful than a raw cumulative-cost
    sum, which is dominated by late trades on a by-then much larger portfolio.
    """
    cost_drag = cost_drag or {}
    lines = [header, ""]
    lines.append(
        f"{'strategy':16s} {'CAGR':>8s} {'MaxDD':>8s} {'Vol':>7s} {'Sharpe':>7s} "
        f"{'Trades':>7s} {'CostDrag':>9s}"
    )
    lines.append("-" * 66)
    for res in results:
        m = metrics.compute(res.equity_curve, cash_curve)
        drag = cost_drag.get(res.strategy, 0.0)
        lines.append(
            f"{res.strategy:16s} "
            f"{m['CAGR'] * 100:7.2f}% "
            f"{m['MaxDD'] * 100:7.2f}% "
            f"{m['Vol'] * 100:6.2f}% "
            f"{m['Sharpe']:7.2f} "
            f"{res.n_trades:7d} "
            f"{drag * 100:7.2f}%/yr"
        )
    return "\n".join(lines)


def save_plot(results: list[BacktestResult], path, title: str) -> None:
    """Two stacked axes: log-scale equity curves (top) and drawdowns (bottom)."""
    import matplotlib

    matplotlib.use("Agg")  # headless — no display needed
    import matplotlib.pyplot as plt

    fig, (ax_eq, ax_dd) = plt.subplots(
        2, 1, figsize=(11, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )
    for res in results:
        curve = res.equity_curve
        ax_eq.plot(curve.index, curve.values, label=res.strategy, linewidth=1.2)
        dd = curve / curve.cummax() - 1.0
        ax_dd.plot(dd.index, dd.values * 100, linewidth=1.0)

    ax_eq.set_yscale("log")
    ax_eq.set_ylabel("Growth of 1 (log)")
    ax_eq.set_title(title)
    ax_eq.legend(loc="upper left", fontsize=9)
    ax_eq.grid(True, which="both", alpha=0.25)

    ax_dd.set_ylabel("Drawdown %")
    ax_dd.set_xlabel("Date")
    ax_dd.grid(True, alpha=0.25)

    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)
