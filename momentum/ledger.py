"""Recommendation ledger: what did the signal say, did you actually trade, and
what did the difference cost?

Bookkeeping and presentation only — the ledger must never influence signals or
backtests. The performance curves here are deliberately costless: trading costs
are the backtest's job; this module measures *behaviour* (the gap between the
strategy's path and the path you actually took).

Realized-path rule: the first recorded recommendation is assumed executed (you
set the portfolio up when you started tracking); after that, a declined or
unanswered change keeps the previous holding.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from typing import TYPE_CHECKING

import pandas as pd

from . import config, data
from .config import Universe

if TYPE_CHECKING:
    from .signal import SignalReport


@dataclass(frozen=True)
class LedgerEntry:
    as_of: str               # month-end the recommendation was computed on (ISO)
    strategy: str
    role: str
    ticker: str
    confirmed: bool | None   # None = not answered yet


# --- Storage -------------------------------------------------------------------

def _ledger_path(universe: Universe):
    return config.STATE_DIR / f"ledger_{universe.name}.json"


def load_ledger(universe: Universe) -> list[LedgerEntry]:
    path = _ledger_path(universe)
    if not path.exists():
        return []
    return [LedgerEntry(**e) for e in json.loads(path.read_text())]


def save_ledger(universe: Universe, entries: list[LedgerEntry]) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _ledger_path(universe).write_text(
        json.dumps([asdict(e) for e in entries], indent=2)
    )


def record(universe: Universe, reports: list[SignalReport]) -> int:
    """Append this month's recommendations. Idempotent per (strategy, as_of),
    so re-running `signal` in the same month never duplicates entries."""
    entries = load_ledger(universe)
    seen = {(e.strategy, e.as_of) for e in entries}
    added = 0
    for r in reports:
        if (r.strategy, r.as_of) not in seen:
            entries.append(
                LedgerEntry(
                    as_of=r.as_of, strategy=r.strategy, role=r.role,
                    ticker=r.ticker, confirmed=None,
                )
            )
            added += 1
    if added:
        save_ledger(universe, entries)
    return added


# --- Confirmation --------------------------------------------------------------

def _history(entries: list[LedgerEntry], strategy: str) -> list[LedgerEntry]:
    return sorted((e for e in entries if e.strategy == strategy), key=lambda e: e.as_of)


def pending_changes(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    """Latest entry per strategy when it is an unanswered *change* — i.e. a trade
    was actually recommended. First-ever entries are assumed executed, and
    no-change months need no answer, so neither is pending."""
    out = []
    for strategy in sorted({e.strategy for e in entries}):
        history = _history(entries, strategy)
        last = history[-1]
        if last.confirmed is None and len(history) >= 2 and history[-2].role != last.role:
            out.append(last)
    return out


def set_confirmation(universe: Universe, strategy: str, confirmed: bool) -> LedgerEntry:
    """Record the answer on the latest ledger entry for `strategy`."""
    entries = load_ledger(universe)
    matches = [i for i, e in enumerate(entries) if e.strategy == strategy]
    if not matches:
        raise ValueError(f"No ledger entries for strategy {strategy!r}.")
    latest = max(matches, key=lambda i: entries[i].as_of)
    entries[latest] = replace(entries[latest], confirmed=confirmed)
    save_ledger(universe, entries)
    return entries[latest]


# --- Performance of recommendations ---------------------------------------------

def curve_from_holdings(
    prices: pd.DataFrame, holdings: list[tuple[pd.Timestamp, str]]
) -> pd.Series:
    """Daily equity curve for a fixed holding schedule, normalized to 1.0 at the
    first execution day. All value moves into the scheduled role at that day's
    close. Costless by design (see module docstring)."""
    schedule = dict(holdings)
    start = holdings[0][0]
    span = prices.index[prices.index >= start]
    curve = pd.Series(index=span, dtype=float)
    value, units, role = 1.0, 0.0, None
    for day in span:
        px = prices.loc[day]
        if role is not None:
            value = units * px[role]
        if day in schedule and schedule[day] != role:
            role = schedule[day]
            units = value / px[role]
        curve[day] = value
    return curve


def _execution_day(index: pd.DatetimeIndex, as_of: str) -> pd.Timestamp | None:
    """Next trading day after `as_of` — the backtest's own t+1 rule. None when
    the date is not in the data or has no next day yet."""
    ts = pd.Timestamp(as_of)
    if ts not in index:
        return None
    pos = index.get_loc(ts)
    return index[pos + 1] if pos + 1 < len(index) else None


def strategy_and_realized_curves(
    prices: pd.DataFrame, history: list[LedgerEntry]
) -> tuple[pd.Series, pd.Series] | None:
    """The two curves the report compares: every recommendation followed, versus
    what the confirmations say actually happened. None if nothing is executable
    yet (e.g. a single recommendation with no next trading day)."""
    ideal: list[tuple[pd.Timestamp, str]] = []
    realized: list[tuple[pd.Timestamp, str]] = []
    held: str | None = None
    for e in history:
        day = _execution_day(prices.index, e.as_of)
        if day is None:
            continue
        ideal.append((day, e.role))
        actual = e.role if (held is None or e.confirmed) else held
        realized.append((day, actual))
        held = actual
    if not ideal:
        return None
    return curve_from_holdings(prices, ideal), curve_from_holdings(prices, realized)


# --- CLI faces -------------------------------------------------------------------

def _ask(entry: LedgerEntry) -> bool:
    prompt = (
        f"{entry.strategy}: did you make the trade into {entry.ticker} "
        f"({entry.role}) recommended on {entry.as_of}? [y/n] "
    )
    while True:
        raw = input(prompt).strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


def run_confirm(universe_name: str, strategy: str | None, answer: bool | None) -> int:
    universe = config.get_universe(universe_name)
    entries = load_ledger(universe)
    if not entries:
        print("Ledger is empty — run `signal` first; it records recommendations.")
        return 0

    if strategy is not None:
        targets = [e for e in pending_changes(entries) if e.strategy == strategy]
        if not targets and strategy not in {e.strategy for e in entries}:
            print(f"No ledger entries for strategy {strategy!r}.")
            return 2
    else:
        targets = pending_changes(entries)

    if not targets:
        print("Nothing to confirm — no unanswered trade recommendations.")
        return 0

    for entry in targets:
        result = answer if answer is not None else _ask(entry)
        set_confirmation(universe, entry.strategy, result)
        verb = "recorded as traded" if result else "recorded as NOT traded"
        print(f"  {entry.strategy}: {entry.ticker} ({entry.as_of}) {verb}.")
    return 0


def _row_status(prev: LedgerEntry | None, entry: LedgerEntry) -> str:
    if prev is None:
        return "start (assumed)"
    if prev.role == entry.role:
        return "no trade needed"
    if entry.confirmed is True:
        return "yes"
    if entry.confirmed is False:
        return "NO"
    return "pending — run `confirm`"


def run_ledger(universe_name: str, strategy: str | None) -> int:
    universe = config.get_universe(universe_name)
    entries = load_ledger(universe)
    if not entries:
        print("Ledger is empty — run `signal` first; it records recommendations.")
        return 0
    prices = data.load_prices(universe)

    names = [strategy] if strategy else sorted({e.strategy for e in entries})
    print(f"Recommendation ledger for universe '{universe.name}'\n")
    for name in names:
        history = _history(entries, name)
        if not history:
            print(f"No ledger entries for strategy {name!r}.")
            return 2
        if strategy is not None:
            print(f"{name} — {len(history)} recommendation(s):")
            print(f"  {'as_of':12s}{'recommended':24s}traded?")
            prev = None
            for e in history:
                rec = f"{e.ticker} ({e.role})"
                print(f"  {e.as_of:12s}{rec:24s}{_row_status(prev, e)}")
                prev = e
            print()
        curves = strategy_and_realized_curves(prices, history)
        if curves is None:
            print(f"  {name:15s} nothing executable yet.")
            continue
        ideal, realized = curves
        ideal_ret = ideal.iloc[-1] - 1.0
        real_ret = realized.iloc[-1] - 1.0
        print(
            f"  {name:15s} since {history[0].as_of}: strategy {ideal_ret:+.2%}, "
            f"you {real_ret:+.2%}, behaviour gap {real_ret - ideal_ret:+.2%}"
        )
    print("\n(Costless curves: costs live in the backtest; this measures behaviour.)")
    return 0
