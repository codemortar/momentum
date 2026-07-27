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


def _meta_path() -> "config.Path":
    return config.CACHE_DIR / "_fetch_meta.json"


def _load_meta() -> dict:
    """Earliest date each cached ticker was requested from.

    The cache is keyed by ticker alone, so without this a symbol first fetched
    for a short universe would silently cap a longer one — 'us' fetches ^IRX
    from 2000, and 'us_long' asking for 1980 would quietly get 2000 anyway.
    """
    import json

    path = _meta_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except ValueError:
        return {}


def _save_meta(meta: dict) -> None:
    import json

    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _meta_path().write_text(json.dumps(meta, indent=2, sort_keys=True))


def _cache_is_deep_enough(symbol: str, meta: dict, start: str) -> bool:
    """Whether the cached copy already reaches back as far as `start`."""
    return symbol in meta and meta[symbol] <= start


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
    meta = _load_meta()
    for role, ticker in universe.tickers.items():
        chain = (ticker,) + universe.history.get(role, ())
        for symbol in chain:
            path = _cache_path(symbol)
            label = role if symbol == ticker else f"{role} (older)"
            deep_enough = _cache_is_deep_enough(symbol, meta, universe.start)
            if path.exists() and deep_enough and not refresh:
                print(f"  cached   {label:22s} {symbol}")
                continue
            why = "" if not path.exists() or refresh else " (need more history)"
            print(f"  download {label:22s} {symbol}{why} ...", flush=True)
            series = _download(symbol, universe.start)
            series.to_csv(path, header=True)
            meta[symbol] = universe.start
    _save_meta(meta)


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


def splice_series(recent: pd.Series, older: pd.Series) -> pd.Series:
    """Extend `recent` backwards using `older`'s returns.

    Both are total-return series on arbitrary scales, so the older one is
    rescaled to meet the newer at their first overlapping date and the newer
    takes over from there. Only *levels* are rescaled — every daily return in
    the result is the return its own source actually recorded, and a constant
    scale factor cannot change a ratio of adjacent prices. So this introduces
    no lookahead: a strategy reading the spliced history on any past date sees
    the same returns it would have seen live.

    Returns `recent` unchanged when there is no overlap to anchor on, rather
    than joining two series at an arbitrary level and inventing a jump.
    """
    overlap = older.index.intersection(recent.index)
    if overlap.empty:
        return recent.sort_index()
    join = overlap.min()
    if older.loc[join] == 0:
        return recent.sort_index()
    factor = recent.loc[join] / older.loc[join]
    before = older.loc[older.index < join] * factor
    return pd.concat([before, recent]).sort_index()


def _role_series(universe: Universe, role: str, ticker: str) -> pd.Series:
    """One role's full history: the primary ticker, extended by its proxies."""
    series = _load_series(ticker)
    for older_ticker in universe.history.get(role, ()):
        series = splice_series(series, _load_series(older_ticker))
    return series


def load_prices(universe: Universe) -> pd.DataFrame:
    """Load all roles into one aligned DataFrame with role-named columns.

    Returns adjusted-close prices (and a synthetic index for cash), forward-filled
    across cross-exchange holiday gaps, then trimmed from the front so the returned
    frame begins on the first date where *every* role has real data. No bfill.
    """
    columns: dict[str, pd.Series] = {}
    for role, ticker in universe.tickers.items():
        raw = _role_series(universe, role, ticker)
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
    for ticker in universe.all_tickers():
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
