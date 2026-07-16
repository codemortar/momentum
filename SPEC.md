# SPEC — momentum

## Goal

Validate simple, monthly-rebalanced ETF momentum/trend strategies on long history
with realistic costs, then get a monthly signal telling me what to hold in a UK
Stocks & Shares ISA. I must be able to trust *why* I hold something, because the
signal I act on is produced by the exact same code the backtest validated.

## Universes (role → ticker)

Downstream code only ever sees semantic **roles** as columns; `config.py` maps
roles to tickers per universe.

| Role          | US (validation, ~2005–now) | UK (execution sanity check, ~2019–now) |
|---------------|----------------------------|----------------------------------------|
| equities_us   | SPY                        | VUSA.L                                 |
| equities_intl | EFA                        | VWRP.L                                 |
| bonds         | IEF                        | VGOV.L                                 |
| gold          | GLD                        | SGLN.L                                 |
| cash          | ^IRX (→ synthetic index)   | ERNS.L                                 |

The US universe has real depth (spans 2008 and 2020) and is what validates a
strategy. The UK universe is the actual instruments I would buy; its short
history makes it a **sanity check that the same code runs coherently**, not
validation.

## Strategies

- **buy_and_hold** — always 100% US equities. The baseline to beat (on a
  risk-adjusted basis, not necessarily on raw CAGR).
- **ma_200** — 100% equities if the last close is strictly above its 200-day
  moving average at month-end; otherwise 100% bonds.
- **dual_momentum** (Antonacci GEM) — compare US vs international equities by
  trailing 12-month (253 trading day) return; hold the winner, but only if it
  also beat cash over that window — otherwise hold bonds.

## Trading rules

- Decide on the **month-end close** (day t); execute at the **next trading day's
  close** (t+1). Never the same bar.
- Cost: `trade_cost_bps` (default 10) charged once on the reallocated notional per
  rebalance; optional `annual_fee_bps` daily drag. No leverage, no shorting;
  fractional shares assumed (ISA brokers support them).

## Metrics & what "good" looks like

CAGR, max drawdown, annualized vol, Sharpe (excess over cash), trade count, and
annual cost drag. Success is **materially shallower drawdown and better Sharpe**
than buy-and-hold — not maximizing CAGR. On US data, ma_200 roughly halves the
2008–09 drawdown for ~1% less CAGR and a higher Sharpe: the thesis holding up.

## Known limitations

- **Adjusted-close drift**: yfinance re-adjusts history on each dividend, so a
  `fetch --refresh` can shift old values slightly. The CSV cache is the source of
  truth so runs are reproducible between refreshes.
- **Survivorship bias**: all chosen tickers still exist.
- **Close-to-close execution** approximates real fills.
- **Short UK history** — sanity check only.
- **Monthly cadence lags fast crashes** (e.g. dual_momentum took ~-34% in the
  fast March 2020 drop before it could react). This is inherent to the approach.

## Out of scope

Live trading / broker APIs, crypto, intraday data, parameter optimization, web
UI. Future work: walk-forward analysis.
