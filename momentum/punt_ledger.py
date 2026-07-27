"""Punt book: individual stock positions, logged and honestly scored.

Separate from `ledger.py` on purpose. That one tracks whether you followed the
ETF strategy; this one tracks the discretionary bets you make outside it, and
the two must never be mixed — a good punt is not evidence the system works, and
a bad one is not evidence it doesn't.

The point is the comparison, not the bookkeeping. Every position is measured
against what the same money would have done in the strategy you actually
follow over exactly the same days, so after a year you have an evidence-based
answer to "am I any good at picking stocks?" rather than a memory of the
winners. A thesis is required when opening a position, so future-you can see
what past-you actually believed rather than reconstructing it favourably.

Nothing here influences signals, backtests or the strategy ledger.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace

import pandas as pd

from . import config, data
from .backtest import run
from .config import CostModel, Universe
from .strategies import STRATEGIES


@dataclass(frozen=True)
class Punt:
    ticker: str
    opened: str                 # ISO date
    amount: float               # money committed, in account currency
    entry_price: float
    thesis: str                 # why you bought — required
    trigger: str = ""           # what would make you sell
    closed: str | None = None   # ISO date once sold
    exit_price: float | None = None

    @property
    def is_open(self) -> bool:
        return self.closed is None


# --- Storage -------------------------------------------------------------------

def _path():
    return config.STATE_DIR / "punts.json"


def load_punts() -> list[Punt]:
    path = _path()
    if not path.exists():
        return []
    return [Punt(**p) for p in json.loads(path.read_text())]


def save_punts(punts: list[Punt]) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps([asdict(p) for p in punts], indent=2))


def add_punt(punt: Punt) -> list[Punt]:
    """Append a position. Several open positions in one ticker are allowed —
    averaging them would hide that they were separate decisions."""
    punts = load_punts()
    punts.append(punt)
    save_punts(punts)
    return punts


def close_punt(ticker: str, exit_price: float, closed: str) -> Punt:
    """Close the oldest open position in `ticker` (first in, first out)."""
    punts = load_punts()
    open_idx = [
        i for i, p in enumerate(punts) if p.ticker == ticker and p.is_open
    ]
    if not open_idx:
        raise ValueError(f"No open position in {ticker!r}.")
    i = open_idx[0]
    punts[i] = replace(punts[i], closed=closed, exit_price=exit_price)
    save_punts(punts)
    return punts[i]


# --- Scoring ---------------------------------------------------------------------

def position_return(punt: Punt, current_price: float | None) -> float | None:
    """Fractional return: realised if closed, marked to `current_price` if open."""
    end = punt.exit_price if not punt.is_open else current_price
    if end is None or not punt.entry_price:
        return None
    return end / punt.entry_price - 1.0


def benchmark_return(
    curve: pd.Series, start: str, end: str | None
) -> float | None:
    """What the strategy did over the same days.

    Uses the last curve value on or before each date, so a position opened on a
    weekend or holiday is compared from the previous trading day rather than
    silently skipping to a later one.
    """
    if curve.empty:
        return None
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end) if end else curve.index[-1]
    before_start = curve.loc[:start_ts]
    before_end = curve.loc[:end_ts]
    if before_start.empty or before_end.empty:
        return None
    if before_end.index[-1] <= before_start.index[-1]:
        return None  # no elapsed time yet
    return float(before_end.iloc[-1] / before_start.iloc[-1] - 1.0)


def strategy_curve(universe: Universe, strategy_name: str) -> pd.Series:
    """The equity curve of the strategy being used as the benchmark."""
    prices = data.load_prices(universe)
    result = run(prices, STRATEGIES[strategy_name], strategy_name, CostModel())
    return result.equity_curve


def fetch_price_on(ticker: str, date: str | None) -> float | None:
    """Close on or before `date` (latest close when `date` is None).

    Backdating a position must not silently record *today's* price as the entry
    — that would show a real gain or loss as zero and quietly ruin the very
    comparison this module exists for.
    """
    import yfinance as yf

    try:
        obj = yf.Ticker(ticker)
        if date is None:
            hist = obj.history(period="5d")
        else:
            asked = pd.Timestamp(date)
            hist = obj.history(
                start=(asked - pd.Timedelta(days=10)).date().isoformat(),
                end=(asked + pd.Timedelta(days=1)).date().isoformat(),
            )
    except Exception:
        return None
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def fetch_current_prices(tickers: list[str]) -> dict[str, float]:
    """Latest close per ticker. Network, best-effort: a failure just means the
    position shows as unpriced rather than the whole report failing."""
    if not tickers:
        return {}
    import yfinance as yf

    out: dict[str, float] = {}
    for ticker in tickers:
        try:
            hist = yf.Ticker(ticker).history(period="5d")
            if not hist.empty:
                out[ticker] = float(hist["Close"].iloc[-1])
        except Exception:
            continue
    return out


# --- Presentation -------------------------------------------------------------------

def _pct(value) -> str:
    return "-" if value is None else f"{value:+.1%}"


def format_report(
    punts: list[Punt],
    prices_now: dict[str, float],
    curve: pd.Series,
    strategy_name: str,
) -> str:
    if not punts:
        return (
            "No punts logged yet.\n"
            "  python -m momentum punt add --ticker AZN.L --amount 250 "
            '--thesis "why you are buying"'
        )

    header = (
        f"  {'ticker':10s}{'opened':12s}{'amount':>9s}{'return':>9s}"
        f"{strategy_name[:12]:>13s}{'vs':>9s}"
    )
    lines: list[str] = []
    totals = {"amount": 0.0, "value": 0.0, "bench_value": 0.0}

    for state, title in ((True, "OPEN"), (False, "CLOSED")):
        group = [p for p in punts if p.is_open == state]
        if not group:
            continue
        lines += [f"{title} POSITIONS", header, "  " + "-" * (len(header) - 2)]
        for p in group:
            ret = position_return(p, prices_now.get(p.ticker))
            bench = benchmark_return(curve, p.opened, p.closed)
            diff = None if (ret is None or bench is None) else ret - bench
            lines.append(
                f"  {p.ticker:10s}{p.opened:12s}{p.amount:>9,.0f}"
                f"{_pct(ret):>9s}{_pct(bench):>13s}{_pct(diff):>9s}"
            )
            lines.append(f"      thesis: {p.thesis}")
            if p.trigger:
                lines.append(f"      sell if: {p.trigger}")
            if ret is not None:
                totals["amount"] += p.amount
                totals["value"] += p.amount * (1.0 + ret)
                totals["bench_value"] += p.amount * (1.0 + (bench or 0.0))
        lines.append("")

    if totals["amount"] > 0:
        punt_ret = totals["value"] / totals["amount"] - 1.0
        bench_ret = totals["bench_value"] / totals["amount"] - 1.0
        verdict = (
            "your picks are ahead" if punt_ret > bench_ret else "the system is ahead"
        )
        lines += [
            f"  Punt pot: {totals['amount']:,.0f} in, worth {totals['value']:,.0f} "
            f"({_pct(punt_ret)}).",
            f"  Same money in {strategy_name}: {totals['bench_value']:,.0f} "
            f"({_pct(bench_ret)}) — {verdict}.",
            "",
            "  Punts are excluded from the strategy ledger and from every",
            "  performance claim the backtest makes.",
        ]
    return "\n".join(lines)


# --- CLI --------------------------------------------------------------------------

def _today() -> str:
    import datetime as _dt

    return _dt.date.today().isoformat()


def run_punt(
    action: str,
    universe_name: str,
    strategy: str | None,
    ticker: str | None,
    amount: float | None,
    price: float | None,
    thesis: str | None,
    trigger: str,
    date: str | None,
) -> int:
    universe = config.get_universe(universe_name)
    import os

    strategy_name = strategy or os.environ.get("MOMENTUM_STRATEGY") or "accel_momentum"
    if strategy_name not in STRATEGIES:
        print(f"Unknown strategy {strategy_name!r}. Choose from {sorted(STRATEGIES)}.")
        return 2

    if action == "add":
        if not ticker or amount is None or not thesis:
            print("`punt add` needs --ticker, --amount and --thesis.")
            return 2
        opened = date or _today()
        entry = price
        if entry is None:
            entry = fetch_price_on(ticker, date)  # None date -> latest close
            if entry is None:
                print(
                    f"Could not fetch a price for {ticker!r} on {opened}. "
                    "Pass --price with your actual fill."
                )
                return 2
        punt = Punt(
            ticker=ticker, opened=opened, amount=amount,
            entry_price=entry, thesis=thesis, trigger=trigger,
        )
        add_punt(punt)
        print(
            f"Logged {ticker} @ {entry:,.2f}, {amount:,.0f} committed "
            f"on {punt.opened}."
        )
        return 0

    if action == "close":
        if not ticker:
            print("`punt close` needs --ticker.")
            return 2
        exit_price = price
        if exit_price is None:
            exit_price = fetch_price_on(ticker, date)
            if exit_price is None:
                print(f"Could not fetch a price for {ticker!r}. Pass --price.")
                return 2
        try:
            closed = close_punt(ticker, exit_price, date or _today())
        except ValueError as exc:
            print(str(exc))
            return 2
        ret = position_return(closed, None)
        print(f"Closed {ticker} @ {exit_price:,.2f} ({_pct(ret)}).")
        return 0

    punts = load_punts()
    open_tickers = sorted({p.ticker for p in punts if p.is_open})
    prices_now = fetch_current_prices(open_tickers) if open_tickers else {}
    try:
        curve = strategy_curve(universe, strategy_name)
    except Exception as exc:
        print(f"(Benchmark unavailable: {exc})")
        curve = pd.Series(dtype=float)
    print(f"\nPunt book — benchmarked against {strategy_name} ({universe.name})\n")
    print(format_report(punts, prices_now, curve, strategy_name))
    return 0
