"""Scoreboard from the journal, with the control and placebo labelled."""

from __future__ import annotations

from datetime import date

from . import journal
from .config import ASSESSMENT_END, START_CAPITAL
from .methods import REGISTRY


def curves(records: list[dict]) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {name: [] for name in REGISTRY}
    for r in records:
        if r.get("kind") == "mark" and r.get("method") in out:
            out[r["method"]].append((r["date"], r["value"]))
    return out


def max_drawdown(values: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for v in values:
        peak = max(peak, v)
        worst = min(worst, v / peak - 1)
    return worst


def run_report() -> int:
    records = journal.read_all()
    marked = curves(records)
    if not any(marked.values()):
        print("Journal is empty — run `python -m momentum lab run` on a trading day first.")
        return 0

    frictions: dict[str, float] = {name: 0.0 for name in REGISTRY}
    fills: dict[str, int] = {name: 0 for name in REGISTRY}
    for r in records:
        if r.get("kind") == "fill" and r.get("method") in frictions:
            frictions[r["method"]] += r["friction"]
            fills[r["method"]] += 1

    days = max((len(c) for c in marked.values()), default=0)
    end = date.fromisoformat(ASSESSMENT_END)
    remaining = (end - date.today()).days
    print(f"Paper contest — day {days}, {max(remaining, 0)} days to assessment "
          f"({ASSESSMENT_END}). {START_CAPITAL:,.0f} start per method, in the "
          "market's currency.\n")

    tags = {"random_walk": " <- control", "random_walk_ftse": " <- control",
            "lunar": " <- placebo"}
    header = (f"{'method':18s}{'market':>7s}{'value':>12s}{'return':>9s}"
              f"{'maxDD':>8s}{'fills':>7s}{'friction':>10s}")
    for underlying in sorted({m.underlying for m in REGISTRY.values()}, key=lambda u: u != "SPY"):
        group = [(n, c) for n, c in marked.items() if REGISTRY[n].underlying == underlying]
        print(header)
        print("-" * len(header))
        for name, curve in sorted(group, key=lambda kv: -(kv[1][-1][1] if kv[1] else START_CAPITAL)):
            value = curve[-1][1] if curve else START_CAPITAL
            values = [v for _, v in curve] or [START_CAPITAL]
            print(f"{name:18s}{underlying:>7s}{value:>12,.2f}{value / START_CAPITAL - 1:>9.2%}"
                  f"{max_drawdown(values):>8.2%}{fills[name]:>7d}{frictions[name]:>10.2f}"
                  f"{tags.get(name, '')}")
        print()

    print(
        "Read with care: judge each method against the control in its own market —\n"
        "below it means no edge shown; anything the placebo beats is noise so far.\n"
        "Three months can refute, not validate — theta_puts especially earns small\n"
        "and loses rare-and-big, so its win rate here says nothing about its tail."
    )
    return 0
