"""Fundamental stock screener: rank shares on value, quality and balance sheet.

This is a *research funnel*, not a validated signal. It ranks the current
snapshot of fundamentals across a universe of large caps and tells you which
names look cheap and financially healthy relative to their peers right now.
Unlike the ETF strategies, it is not backtested — yfinance serves today's
figures, not the point-in-time figures that were known at each past date, so an
honest historical test is impossible without a paid data subscription. Treat the
output as a shortlist to research, not a buy list.

Scoring is a cross-sectional percentile rank per factor, averaged into a
composite (1.0 = best). Ranking is universe-wide by default, or within sector
(`--by-sector`) so that banks are not compared to miners on price-to-book.
Factors missing for a company are skipped; a company is only scored if enough
factors are present.

The cache stores the raw API response — both the `.info` fields and the handful
of financial-statement line items the Piotroski F-Score needs — and all
cleaning and derivation happens on read, so fixing a rule repairs old caches.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass

import pandas as pd

from . import config


@dataclass(frozen=True)
class Factor:
    key: str               # column in the prepared frame
    label: str             # short column header
    higher_is_better: bool
    group: str             # value | quality | safety


# The three things with real evidence behind them — cheapness, profitability and
# balance-sheet strength — plus Piotroski as a second, statement-based read on
# financial health (see piotroski_fscore).
FACTORS: tuple[Factor, ...] = (
    Factor("trailingPE", "P/E", False, "value"),
    Factor("priceToBook", "P/B", False, "value"),
    Factor("enterpriseToEbitda", "EV/EBITDA", False, "value"),
    Factor("returnOnEquity", "ROE", True, "quality"),
    Factor("profitMargins", "Margin", True, "quality"),
    Factor("fscore_pct", "F-Score", True, "quality"),
    Factor("currentRatio", "Current", True, "safety"),
    Factor("debtToEquity", "Debt/Eq", False, "safety"),
)

# Ratios that are meaningless when negative (a loss-making company is not
# "cheap" because its P/E is -4). These are dropped rather than ranked.
POSITIVE_ONLY = {"trailingPE", "priceToBook", "enterpriseToEbitda"}

MIN_FACTORS = 4      # factors a company needs present to be scored at all
MIN_FSCORE_SIGNALS = 5  # F-Score signals needed before it counts toward the score
MIN_SECTOR_SIZE = 4  # smaller sectors fall back to universe-wide ranking

SCREEN_UNIVERSES: dict[str, list[str]] = {
    # Broad FTSE 100 coverage. Tickers that no longer resolve are skipped at
    # fetch time rather than being fatal, so occasional index changes are fine.
    "uk": [
        "AAL.L", "ABF.L", "ADM.L", "AHT.L", "ANTO.L", "AUTO.L", "AV.L", "AZN.L",
        "BA.L", "BARC.L", "BATS.L", "BDEV.L", "BEZ.L", "BKG.L", "BNZL.L", "BP.L",
        "BRBY.L", "BT-A.L", "CCH.L", "CNA.L", "CPG.L", "CRDA.L", "DCC.L",
        "DGE.L", "DPLM.L", "EXPN.L", "FCIT.L", "FRAS.L", "FRES.L", "GAW.L",
        "GLEN.L", "GSK.L", "HIK.L", "HL.L", "HLMA.L", "HLN.L", "HSBA.L",
        "HWDN.L", "IAG.L", "ICG.L", "IHG.L", "III.L", "IMB.L", "IMI.L", "INF.L",
        "ITRK.L", "JD.L", "KGF.L", "LAND.L", "LGEN.L", "LLOY.L", "LSEG.L",
        "MKS.L", "MNDI.L", "MNG.L", "NG.L", "NWG.L", "NXT.L", "PHNX.L", "PRU.L",
        "PSN.L", "PSON.L", "REL.L", "RIO.L", "RKT.L", "RMV.L", "RR.L", "RTO.L",
        "SBRY.L", "SDR.L", "SGE.L", "SGRO.L", "SHEL.L", "SMIN.L", "SN.L",
        "SPX.L", "SSE.L", "STAN.L", "STJ.L", "SVT.L", "TSCO.L", "TW.L",
        "ULVR.L", "UU.L", "VOD.L", "WEIR.L", "WPP.L", "WTB.L",
    ],
    "us": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "JPM", "V", "MA",
        "JNJ", "WMT", "PG", "XOM", "CVX", "HD", "ABBV", "KO", "PEP", "MRK",
        "COST", "ADBE", "CSCO", "MCD", "CRM", "ACN", "LLY", "TMO", "ABT",
        "DHR", "INTC", "VZ", "T", "PFE", "NKE", "ORCL", "IBM", "QCOM", "TXN",
        "AMD", "HON", "UNP", "CAT", "GS", "MS", "BLK", "AXP", "LOW", "CVS",
        "UPS", "DE", "BA", "MMM", "GE", "F", "GM", "LIN", "PM", "RTX", "SPGI",
        "NEE", "AMGN", "ISRG", "NOW", "BKNG", "SYK", "ELV", "PLD", "MDT",
        "GILD", "ADP", "VRTX", "LRCX", "PANW", "MU", "KLAC", "SNPS", "CDNS",
        "REGN", "CI", "SO", "DUK", "ZTS", "MO", "BSX", "EQIX", "SLB", "APD",
        "ITW", "WM", "SHW", "MCK", "CME", "CL", "EOG", "MMC", "AON", "PGR",
        "USB", "PNC", "SCHW", "COF", "TRV", "ALL", "MET", "AIG", "NEM", "FCX",
        "VLO", "PSX", "MPC", "OXY", "KMI", "WMB", "D", "AEP", "EXC", "XEL",
    ],
}

# Extra descriptive columns pulled alongside the scored factors.
INFO_EXTRAS = (
    "shortName", "sector", "marketCap", "dividendYield",
    "currentPrice", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
)

# Financial-statement line items the F-Score needs, with the row-label variants
# yfinance has used. Fetched for the last two annual periods (_0 = most recent).
STATEMENT_ITEMS: dict[str, tuple[str, tuple[str, ...]]] = {
    "netIncome": ("income", (
        "Net Income", "Net Income Common Stockholders",
        "Net Income From Continuing Operation Net Minority Interest",
    )),
    "totalRevenue": ("income", ("Total Revenue", "Operating Revenue")),
    "grossProfit": ("income", ("Gross Profit",)),
    "totalAssets": ("balance", ("Total Assets",)),
    "currentAssets": ("balance", ("Current Assets", "Total Current Assets")),
    "currentLiabilities": ("balance", (
        "Current Liabilities", "Total Current Liabilities",
    )),
    "longTermDebt": ("balance", (
        "Long Term Debt", "Long Term Debt And Capital Lease Obligation",
    )),
    "sharesOutstanding": ("balance", (
        "Ordinary Shares Number", "Share Issued", "Common Stock Shares Outstanding",
    )),
    "operatingCashFlow": ("cash", (
        "Operating Cash Flow", "Total Cash From Operating Activities",
        "Cash Flow From Continuing Operating Activities",
    )),
}


def _cache_path(universe_name: str):
    return config.CACHE_DIR / f"screen_{universe_name}.csv"


# --- Fetch ---------------------------------------------------------------------

def _statement_row(frame, names: tuple[str, ...]):
    """Most recent two annual values of the first matching row, oldest last."""
    if frame is None or getattr(frame, "empty", True):
        return (None, None)
    for name in names:
        if name in frame.index:
            series = pd.to_numeric(frame.loc[name], errors="coerce")
            series = series.sort_index(ascending=False)  # newest first
            values = list(series.values[:2])
            while len(values) < 2:
                values.append(None)
            return tuple(values)
    return (None, None)


def _statement_items(ticker_obj) -> dict:
    """Flatten the F-Score inputs into `<item>_0` / `<item>_1` columns."""
    try:
        frames = {
            "income": ticker_obj.income_stmt,
            "balance": ticker_obj.balance_sheet,
            "cash": ticker_obj.cashflow,
        }
    except Exception:
        return {}
    out = {}
    for item, (which, names) in STATEMENT_ITEMS.items():
        latest, prior = _statement_row(frames.get(which), names)
        out[f"{item}_0"] = latest
        out[f"{item}_1"] = prior
    return out


def fetch_fundamentals(tickers: list[str], verbose: bool = True) -> pd.DataFrame:
    """One row per ticker of raw, untransformed fundamentals. Network, slow:
    four requests per ticker (info plus three statements).

    Deliberately returns exactly what the API gave — the cache stores this raw
    and `prepare` runs on read, so fixing a rule repairs old caches too.
    Individual failures are skipped rather than fatal: coverage varies a lot by
    ticker, and a partial screen is still useful.
    """
    import yfinance as yf

    wanted = [
        f.key for f in FACTORS if not f.key.startswith("fscore")
    ] + list(INFO_EXTRAS)
    rows: dict[str, dict] = {}
    for i, ticker in enumerate(tickers, 1):
        if verbose:
            print(f"  [{i:>3}/{len(tickers)}] {ticker:10s}", end="\r", flush=True)
        try:
            obj = yf.Ticker(ticker)
            info = obj.info or {}
        except Exception:
            continue
        if not info.get("shortName"):
            continue  # nothing came back for this one
        row = {k: info.get(k) for k in wanted}
        row.update(_statement_items(obj))
        rows[ticker] = row
    if verbose:
        print(" " * 40, end="\r")
    if not rows:
        raise RuntimeError("No fundamentals returned for any ticker.")
    df = pd.DataFrame.from_dict(rows, orient="index")
    df.index.name = "ticker"
    return df


# --- Cleaning and derived columns ------------------------------------------------

def _clean(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce numerics and drop values that would rank nonsensically."""
    out = df.copy()
    numeric = [f.key for f in FACTORS if not f.key.startswith("fscore")] + [
        "marketCap", "dividendYield", "currentPrice",
        "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    ] + [f"{item}_{n}" for item in STATEMENT_ITEMS for n in (0, 1)]
    for col in numeric:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in POSITIVE_ONLY:
        if col in out.columns:
            out.loc[out[col] <= 0, col] = pd.NA
    if "dividendYield" in out.columns:
        # yfinance has shipped this as both a fraction (0.048) and a percentage
        # number (4.8). Decide per column, not per row: a row-wise test misreads
        # any genuine sub-1% yield as a fraction and inflates it 100x.
        dy = out["dividendYield"]
        if dy.notna().any() and dy.median(skipna=True) > 1.0:
            out["dividendYield"] = dy / 100.0
    return out


def _ratio(numerator, denominator):
    """Safe divide: None unless both sides are present and the divisor is > 0."""
    if numerator is None or denominator is None:
        return None
    if pd.isna(numerator) or pd.isna(denominator) or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def piotroski_fscore(row) -> tuple[int, int]:
    """Piotroski's nine binary tests of financial health, from the last two
    annual reports. Returns (points scored, tests it was possible to evaluate).

    Signals not computable from the available data are skipped rather than
    counted as failures, so a bank with no gross-profit line scores out of
    fewer tests instead of being unfairly marked down.
    """
    def val(item, year):
        v = row.get(f"{item}_{year}")
        return None if v is None or pd.isna(v) else float(v)

    roa = [_ratio(val("netIncome", y), val("totalAssets", y)) for y in (0, 1)]
    cfo_assets = _ratio(val("operatingCashFlow", 0), val("totalAssets", 0))
    leverage = [_ratio(val("longTermDebt", y), val("totalAssets", y)) for y in (0, 1)]
    current = [
        _ratio(val("currentAssets", y), val("currentLiabilities", y)) for y in (0, 1)
    ]
    margin = [_ratio(val("grossProfit", y), val("totalRevenue", y)) for y in (0, 1)]
    turnover = [_ratio(val("totalRevenue", y), val("totalAssets", y)) for y in (0, 1)]
    cfo, shares = val("operatingCashFlow", 0), (val("sharesOutstanding", 0),
                                                val("sharesOutstanding", 1))

    tests = [
        roa[0] is not None and roa[0] > 0,                              # profitable
        cfo is not None and cfo > 0,                                    # cash positive
        None not in roa and roa[0] > roa[1],                            # improving ROA
        None not in (cfo_assets, roa[0]) and cfo_assets > roa[0],       # low accruals
        None not in leverage and leverage[0] < leverage[1],             # deleveraging
        None not in current and current[0] > current[1],                # more liquid
        None not in shares and shares[0] <= shares[1],                  # no dilution
        None not in margin and margin[0] > margin[1],                   # better margin
        None not in turnover and turnover[0] > turnover[1],             # more efficient
    ]
    available = [
        roa[0] is not None,
        cfo is not None,
        None not in roa,
        None not in (cfo_assets, roa[0]),
        None not in leverage,
        None not in current,
        None not in shares,
        None not in margin,
        None not in turnover,
    ]
    score = sum(1 for t, a in zip(tests, available) if a and t)
    return score, sum(available)


def add_fscore(df: pd.DataFrame) -> pd.DataFrame:
    """Add fscore / fscore_n / fscore_pct columns (pct only when meaningful)."""
    out = df.copy()
    scores, counts = [], []
    for _, row in out.iterrows():
        score, n = piotroski_fscore(row)
        scores.append(score if n else pd.NA)
        counts.append(n if n else pd.NA)
    out["fscore"] = scores
    out["fscore_n"] = counts
    pct = pd.to_numeric(out["fscore"], errors="coerce") / pd.to_numeric(
        out["fscore_n"], errors="coerce"
    )
    # Too few signals is not evidence of health, so don't let it sway the score.
    out["fscore_pct"] = pct.where(
        pd.to_numeric(out["fscore_n"], errors="coerce") >= MIN_FSCORE_SIGNALS
    )
    return out


def range_position(df: pd.DataFrame) -> pd.Series:
    """Where the price sits in its 52-week range: 0.0 = at the low, 1.0 = high."""
    low, high = df.get("fiftyTwoWeekLow"), df.get("fiftyTwoWeekHigh")
    price = df.get("currentPrice")
    if low is None or high is None or price is None:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    span = high - low
    pos = (price - low) / span.where(span > 0)
    return pos.clip(0.0, 1.0)


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Clean the raw cache and add every derived column the screen needs."""
    out = add_fscore(_clean(df))
    out["range_pos"] = range_position(out)
    return out


# --- Ranking ---------------------------------------------------------------------

def factor_ranks(
    df: pd.DataFrame,
    factors=FACTORS,
    by_sector: bool = False,
    min_sector_size: int = MIN_SECTOR_SIZE,
) -> pd.DataFrame:
    """Percentile rank per factor, oriented so 1.0 is always best.

    With by_sector, a company is ranked against its own sector — a bank's
    price-to-book means something very different from a miner's. Sectors too
    small to rank within fall back to the universe-wide ranking.
    """
    sectors = df["sector"] if (by_sector and "sector" in df.columns) else None
    sizes = sectors.map(sectors.value_counts()) if sectors is not None else None

    ranks = {}
    for f in factors:
        if f.key not in df.columns:
            continue
        col = pd.to_numeric(df[f.key], errors="coerce")
        if col.notna().sum() < 2:
            continue  # nothing meaningful to rank against
        overall = col.rank(pct=True, ascending=f.higher_is_better)
        if sectors is None:
            ranks[f.label] = overall
        else:
            within = col.groupby(sectors).rank(pct=True, ascending=f.higher_is_better)
            ranks[f.label] = within.where(sizes >= min_sector_size, overall)
    return pd.DataFrame(ranks, index=df.index)


def composite_score(
    df: pd.DataFrame,
    factors=FACTORS,
    min_factors: int = MIN_FACTORS,
    by_sector: bool = False,
) -> pd.Series:
    """Mean of available factor ranks; NaN for companies with too little data."""
    ranks = factor_ranks(df, factors, by_sector=by_sector)
    if ranks.empty:
        return pd.Series(pd.NA, index=df.index, dtype="Float64")
    score = ranks.mean(axis=1, skipna=True)
    return score.where(ranks.notna().sum(axis=1) >= min_factors)


def screen(
    df: pd.DataFrame,
    near_lows: bool = False,
    low_threshold: float = 0.33,
    by_sector: bool = False,
    prepared: bool = False,
) -> pd.DataFrame:
    """Score, annotate and sort. With near_lows, keep only names in the bottom
    third of their 52-week range (the 'is it beaten down?' filter)."""
    out = df if prepared else prepare(df)
    out = out.copy()
    out["score"] = composite_score(out, by_sector=by_sector)
    if near_lows:
        out = out[out["range_pos"].notna() & (out["range_pos"] <= low_threshold)]
    return out[out["score"].notna()].sort_values("score", ascending=False)


# --- Presentation -----------------------------------------------------------------

def _fmt(value, spec: str, scale: float = 1.0) -> str:
    if value is None or pd.isna(value):
        return "-"
    try:
        return format(float(value) * scale, spec)
    except (TypeError, ValueError):
        return "-"


def _fscore_cell(row) -> str:
    score, n = row.get("fscore"), row.get("fscore_n")
    if score is None or pd.isna(score) or n is None or pd.isna(n):
        return "-"
    return f"{int(score)}/{int(n)}"


def format_table(df: pd.DataFrame, top: int = 15) -> str:
    header = (
        f"{'ticker':10s}{'name':20s}{'score':>6s}{'F':>6s}{'P/E':>7s}{'P/B':>6s}"
        f"{'ROE':>7s}{'Margin':>8s}{'D/E':>7s}{'Yield':>7s}{'52w':>6s}"
    )
    lines = [header, "-" * len(header)]
    for ticker, row in df.head(top).iterrows():
        name = str(row.get("shortName") or "")[:18]
        lines.append(
            f"{ticker:10s}{name:20s}"
            f"{_fmt(row.get('score'), '.2f'):>6s}"
            f"{_fscore_cell(row):>6s}"
            f"{_fmt(row.get('trailingPE'), '.1f'):>7s}"
            f"{_fmt(row.get('priceToBook'), '.1f'):>6s}"
            f"{_fmt(row.get('returnOnEquity'), '.0%'):>7s}"
            f"{_fmt(row.get('profitMargins'), '.0%'):>8s}"
            f"{_fmt(row.get('debtToEquity'), '.0f'):>7s}"
            f"{_fmt(row.get('dividendYield'), '.1%'):>7s}"
            f"{_fmt(row.get('range_pos'), '.2f'):>6s}"
        )
    return "\n".join(lines)


# --- Cache / CLI --------------------------------------------------------------------

def load_cached(universe_name: str) -> tuple[pd.DataFrame, str]:
    """Prepared frame plus the date the cache was written. Raises if absent."""
    path = _cache_path(universe_name)
    if not path.exists():
        raise FileNotFoundError(
            f"No screen cache for {universe_name!r}. Run "
            f"`python -m momentum screen --universe {universe_name}` first."
        )
    raw = pd.read_csv(path, index_col="ticker")
    fetched = _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    return prepare(raw), fetched


def screen_section(universe_name: str, top: int = 5) -> list[str]:
    """Email lines for the top screened names. Cache-only by design: the
    delivery path must not depend on a slow multi-minute network fetch."""
    prepared, fetched = load_cached(universe_name)
    ranked = screen(prepared, prepared=True)
    lines = [
        f"SCREEN — cheapest/healthiest {universe_name.upper()} large caps "
        f"(data {fetched}).",
        "NOT backtested; a research shortlist, not a buy list.",
        "",
    ]
    for ticker, row in ranked.head(top).iterrows():
        name = str(row.get("shortName") or "")[:28]
        lines.append(
            f"  {ticker:10s}{name:30s}score {_fmt(row.get('score'), '.2f')}"
            f"  P/E {_fmt(row.get('trailingPE'), '.1f')}"
            f"  F {_fscore_cell(row)}"
        )
    return lines


def run_screen(
    universe_name: str, top: int, near_lows: bool, refresh: bool, by_sector: bool
) -> int:
    tickers = SCREEN_UNIVERSES.get(universe_name)
    if tickers is None:
        print(
            f"No screen universe {universe_name!r}. "
            f"Choose from {sorted(SCREEN_UNIVERSES)}."
        )
        return 2

    path = _cache_path(universe_name)
    if path.exists() and not refresh:
        raw = pd.read_csv(path, index_col="ticker")
        fetched = _dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
    else:
        print(
            f"Fetching fundamentals and financial statements for {len(tickers)} "
            f"{universe_name.upper()} names (4 requests each — this takes a few "
            "minutes; results are cached)..."
        )
        raw = fetch_fundamentals(tickers)
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        raw.to_csv(path)
        fetched = _dt.date.today().isoformat()

    ranked = screen(raw, near_lows=near_lows, by_sector=by_sector)
    if ranked.empty:
        print("Nothing scoreable (too little data, or the filter excluded everything).")
        return 0

    bits = [f"{len(ranked)} of {len(raw)} names scored"]
    if by_sector:
        bits.append("ranked within sector")
    if near_lows:
        bits.append("near 52w lows only")
    print(f"\nScreen: {universe_name.upper()} · " + " · ".join(bits) +
          f" · data fetched {fetched}\n")
    print(format_table(ranked, top))
    print(
        "\nScore = mean percentile rank across value (P/E, P/B, EV/EBITDA), quality\n"
        "(ROE, margin, F-Score) and safety (current ratio, debt/equity). "
        "1.00 = best.\n"
        "F = Piotroski F-Score: points scored / tests evaluable from the "
        "last two\nannual reports (9 max; fewer where a line item is missing).\n"
        "52w = position in the 52-week range (0.00 at the low, 1.00 at the high).\n"
        "\nNOT BACKTESTED: this ranks today's reported fundamentals only. "
        "Cheap often means\ntroubled — a high score is a research shortlist, "
        "not a buy list."
    )
    return 0
