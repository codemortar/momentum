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
- **accel_momentum** — dual momentum scored by Keller's **13612W** blend instead
  of a single 12-month return: the average of the annualized 1-, 3-, 6- and
  12-month returns (21/63/126/253 trading days). Same decision rule otherwise
  (winner of US vs intl equities if it also beats cash, else bonds), but the
  1-month term is weighted 12× so it reacts to turns in weeks rather than
  months — faster exits, more trades, more whipsaw.
- **vaa** — Vigilant Asset Allocation (Keller), adapted to this universe. Risk
  assets: US equities, intl equities, gold; defensive assets: bonds, cash. If
  *every* risk asset has a strictly positive 13612W score, hold the single
  best-scoring risk asset; if any is zero or negative, hold the best-scoring
  defensive asset. The aggressive, high-turnover option: concentrated in one
  asset and quick to flee to safety.

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

## Signal delivery

`signal --notify` delivers the result out-of-band. If `SENDGRID_API_KEY`,
`MOMENTUM_EMAIL_FROM` and `MOMENTUM_EMAIL_TO` are set in the environment, it
emails via the SendGrid v3 API (a stdlib HTTPS POST — no SDK dependency);
otherwise, or if sending fails, it falls back to a macOS notification (a
printed no-op on other platforms, so a Linux server run never crashes). The
email spells out the explicit action per changed strategy ("SELL X, BUY Y") —
or says explicitly that no action is needed — and explains each strategy's
decision with the indicator readings behind it (price vs 200-day average,
12-month returns, 13612W scores), plus a snapshot of trailing 1/3/12-month
returns per asset. Explanations recompute the same indicators on the same
truncated history the strategies saw. If `MOMENTUM_STRATEGY` is set to a
strategy name, the email is scoped to that strategy alone — ACTION means *your*
strategy changed, and the others are not shown (no monthly temptation to
cherry-pick); console output and the ledger still cover all strategies.
Delivery is presentation only: it must never affect the signal computation or
the state file.

## Recommendation ledger (single user)

Every `signal` run appends that month's recommendation per strategy to an
append-only ledger (`state/ledger_{universe}.json`). `confirm` asks whether the
recommended trades were actually made — it only asks when the recommendation
*changed* (a real trade) and is still unanswered. `ledger` reports the history
and compares two costless curves from the first recorded month: the **strategy
path** (every recommendation executed at t+1) and the **realized path** (the
first recommendation is assumed executed; after that, declined or unanswered
changes keep the previous holding). The difference is the behaviour gap. Curves
are costless by design — costs are the backtest's job; this measures behaviour.
The ledger is bookkeeping only: it must never influence signals or backtests.

## Punt of the month (opt-in novelty — explicitly unvalidated)

If `MOMENTUM_PUNT` is set in the environment, the signal email appends a
clearly-labelled "punt of the month": the single FTSE-100 share (from a fixed,
hardcoded candidate list) with the strongest 13612W momentum. This is
entertainment, not a signal: single names gap through trend rules, the pick is
not backtested and never will be, the candidate list has survivorship bias by
construction, and the section says all of this in the email itself. It is
fetched best-effort at delivery time (the only place outside `fetch` that may
touch the network) and any failure degrades to a one-line notice — it must
never break the signal. Punts are excluded from the ledger and all performance
claims. Crypto remains out of scope entirely.

## Stock screener (research tool, not a signal)

`screen` ranks a fixed universe of UK or US large caps on a composite of value
(P/E, P/B, EV/EBITDA), quality (ROE, profit margin, Piotroski F-Score) and
balance-sheet safety (current ratio, debt/equity), each as a cross-sectional
percentile rank so 1.0 is best. Position in the 52-week range is displayed, and
`--near-lows` filters to the bottom third of that range. Non-positive value
ratios are dropped rather than ranked (a loss-making company is not "cheap"),
and a company needs at least four factors present to be scored at all.

`--by-sector` ranks each company against its own sector rather than the whole
universe, since a bank's price-to-book is not comparable to a miner's. Sectors
with fewer than four members fall back to universe-wide ranking rather than
handing a lone company a free top rank.

The **F-Score** is Piotroski's nine binary tests (profitability, leverage,
liquidity, dilution, margin and asset-turnover trends) computed from the last
two annual reports. Tests whose inputs are missing are skipped, not failed, so
it reports points scored out of tests evaluable — a bank with no gross-profit
line scores out of seven rather than being marked down. It only contributes to
the composite when at least five signals were evaluable.

If `MOMENTUM_SCREEN` is set, the monthly signal email appends the top-scoring
names (the value may be a row count). The email section reads the cache only:
the delivery path must never depend on a multi-minute network fetch, so
`run_monthly.sh` refreshes the screen cache beforehand, best-effort.

This is deliberately outside the backtest's guarantees: yfinance serves current
fundamentals, not point-in-time figures as known at each past date, so an honest
historical test is impossible without paid data. The screener therefore makes no
performance claim, is excluded from the ledger, and prints that caveat with
every run. Its cache stores the raw API response; cleaning happens on read, so
fixing a cleaning rule also repairs existing caches.

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
