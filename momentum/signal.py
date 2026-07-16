"""Live signal: what should the portfolio hold right now?

Correctness note (see CLAUDE.md): the signal is evaluated at the last *completed*
month-end, using the exact same strategy functions the backtest validated, called
on price history truncated to that month-end. Running mid-month must NOT invent a
new rule — we truncate to the last month-end and, separately and clearly labelled,
show an informational "if today were month-end" preview that is not tradable.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pandas as pd

from . import config, data
from .backtest import month_end_trading_days
from .config import Universe
from .strategies import STRATEGIES


@dataclass
class SignalReport:
    strategy: str
    role: str            # the winning role, e.g. "equities_intl"
    ticker: str          # the real instrument to hold, e.g. "VWRP.L"
    as_of: str           # month-end the signal was computed on (ISO date)


def _weights_to_role(weights: dict[str, float]) -> str:
    """Our strategies always allocate 100% to one role; return that role."""
    return max(weights, key=weights.get)


def last_completed_month_end(index: pd.DatetimeIndex) -> pd.Timestamp:
    """The last month-end we may act on: a genuine month-end that already has a
    following trading day. This deliberately reuses the backtest's own rule (a
    decision at t executes at t+1), so the live signal can never act on the
    in-progress month — whose latest day is not a real month-end — and always
    matches a decision the backtest would have made. Mid-July, this is 30 Jun.
    """
    month_ends = month_end_trading_days(index)
    actionable = [m for m in month_ends if index.get_loc(m) + 1 < len(index)]
    if not actionable:
        raise ValueError("Not enough history to produce a completed month-end signal.")
    return actionable[-1]


def evaluate(universe: Universe) -> tuple[list[SignalReport], pd.Timestamp]:
    prices = data.load_prices(universe)
    as_of = last_completed_month_end(prices.index)
    truncated = prices.loc[:as_of]

    reports = []
    for name, fn in STRATEGIES.items():
        weights = fn(truncated)
        role = _weights_to_role(weights)
        reports.append(
            SignalReport(
                strategy=name,
                role=role,
                ticker=universe.tickers[role],
                as_of=as_of.date().isoformat(),
            )
        )
    return reports, as_of


def _state_path(universe: Universe):
    return config.STATE_DIR / f"last_signal_{universe.name}.json"


def load_state(universe: Universe) -> dict:
    path = _state_path(universe)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(universe: Universe, reports: list[SignalReport], as_of: pd.Timestamp) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "as_of": as_of.date().isoformat(),
        "signals": {r.strategy: r.role for r in reports},
    }
    _state_path(universe).write_text(json.dumps(state, indent=2))


def notify_macos(title: str, body: str) -> None:
    script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
    subprocess.run(["osascript", "-e", script], check=False)


def run_signal(universe_name: str, notify: bool) -> int:
    universe = config.get_universe(universe_name)
    reports, as_of = evaluate(universe)
    prev = load_state(universe)
    prev_signals = prev.get("signals", {})
    prev_as_of = prev.get("as_of")

    print(f"Signals for universe '{universe.name}' as of month-end {as_of.date()}")
    print(f"(data cached: {data.cache_date(universe)})\n")

    changed_lines = []
    for r in reports:
        old_role = prev_signals.get(r.strategy)
        if old_role is None:
            change = "no previous state"
        elif old_role == r.role:
            change = f"no change (held since {prev_as_of})"
        else:
            old_ticker = universe.tickers.get(old_role, old_role)
            change = f"CHANGED from {old_ticker} -> {r.ticker}"
            changed_lines.append(f"{r.strategy}: {old_ticker} -> {r.ticker}")
        print(f"  {r.strategy:14s} HOLD {r.ticker:8s} ({r.role})   [{change}]")

    save_state(universe, reports, as_of)

    if notify:
        if changed_lines:
            notify_macos("Momentum: signal changed", "; ".join(changed_lines))
        else:
            holds = ", ".join(f"{r.strategy}={r.ticker}" for r in reports)
            notify_macos("Momentum: no change", holds)

    return 0
