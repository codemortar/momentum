"""Data layer: fetch daily prices via yfinance, cache to CSV, map to roles.

Policy (see CLAUDE.md — these are correctness invariants, not preferences):
  * Adjusted close only (total return, so dividends are reflected).
  * Forward-fill only. Never bfill — that would pull future prices into the past.
  * The CSV cache is the source of truth. Backtests run offline against it and are
    byte-reproducible; only `fetch --refresh` re-downloads and mutates the cache.
  * The '^IRX' cash rate is converted here into a synthetic total-return index.
"""

from __future__ import annotations

import sys

import pandas as pd

from . import config
from .config import Universe


def _cache_path(ticker: str) -> "config.Path":
    # File-system-safe: '^IRX' -> 'IRX', 'VUSA.L' -> 'VUSA.L'.
    safe = ticker.replace("^", "").replace("/", "_")
    return config.CACHE_DIR / f"{safe}.csv"


def _download(ticker: str, start: str) -> pd.Series:
    """Download one ticker's adjusted-close series from yfinance."""
    import yfinance as yf

    df = yf.download(
        ticker,
        start=start,
        auto_adjust=True,       # 'Close' becomes the adjusted (total-return) close
        progress=False,
        actions=False,
    )
    if df.empty:
        raise RuntimeError(f"yfinance returned no data for {ticker!r}.")
    # yfinance may return a MultiIndex column frame for a single ticker.
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.name = ticker
    return close


def fetch(universe: Universe, refresh: bool = False) -> None:
    """Ensure every ticker in the universe is cached to CSV.

    With refresh=False, only missing tickers are downloaded. With refresh=True,
    all are re-downloaded (and historical values may shift slightly as yfinance
    re-applies dividend adjustments — an accepted, documented property).
    """
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for role, ticker in universe.tickers.items():
        path = _cache_path(ticker)
        if path.exists() and not refresh:
            print(f"  cached   {role:14s} {ticker}")
            continue
        print(f"  download {role:14s} {ticker} ...", flush=True)
        series = _download(ticker, universe.start)
        series.to_csv(path, header=True)


def _load_series(ticker: str) -> pd.Series:
    path = _cache_path(ticker)
    if not path.exists():
        raise FileNotFoundError(
            f"No cache for {ticker!r} at {path}. Run `python -m momentum fetch` first."
        )
    s = pd.read_csv(path, index_col=0, parse_dates=True).iloc[:, 0]
    s.index = pd.DatetimeIndex(s.index)
    return s.astype(float)


def _cash_index_from_rate(rate_pct: pd.Series) -> pd.Series:
    """Convert an annualized T-bill yield (percent) into a total-return index.

    Each trading day earns 1/252 of the then-current annual rate, compounded.
    A constant 5% rate therefore produces a series growing at ~5% per year.
    """
    daily = rate_pct / 100.0 / 252.0
    return (1.0 + daily).cumprod()


def load_prices(universe: Universe) -> pd.DataFrame:
    """Load all roles into one aligned DataFrame with role-named columns.

    Returns adjusted-close prices (and a synthetic index for cash), forward-filled
    across cross-exchange holiday gaps, then trimmed from the front so the returned
    frame begins on the first date where *every* role has real data. No bfill.
    """
    columns: dict[str, pd.Series] = {}
    for role, ticker in universe.tickers.items():
        raw = _load_series(ticker)
        if ticker.startswith("^"):
            # '^IRX'-style yield series (a rate, not a price) -> synthetic index.
            # Only the caret-prefixed indices are rates; real cash ETFs (e.g.
            # CSH2.L) are ordinary prices and pass through unchanged.
            columns[role] = _cash_index_from_rate(raw)
        else:
            columns[role] = raw

    prices = pd.DataFrame(columns)
    prices = prices.sort_index()
    prices = prices.ffill()          # fill isolated holiday gaps only
    prices = prices.dropna()         # trim leading rows until all roles exist
    if prices.empty:
        raise RuntimeError(
            "No overlapping dates across roles after alignment — check tickers/start."
        )
    return prices


def cache_date(universe: Universe) -> str:
    """Modification date of the freshest cached file, for report headers."""
    import datetime as _dt

    times = []
    for ticker in universe.tickers.values():
        path = _cache_path(ticker)
        if path.exists():
            times.append(path.stat().st_mtime)
    if not times:
        return "no cache"
    return _dt.datetime.fromtimestamp(max(times)).strftime("%Y-%m-%d")


def _cli_fetch(universe: Universe, refresh: bool) -> None:
    print(f"Fetching universe '{universe.name}' (refresh={refresh}):")
    fetch(universe, refresh=refresh)
    prices = load_prices(universe)
    print(
        f"OK: {len(prices)} rows, {prices.index[0].date()} -> {prices.index[-1].date()}, "
        f"roles={list(prices.columns)}",
        file=sys.stderr,
    )
