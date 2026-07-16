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
truncated history the strategies saw. Delivery is presentation only: it must
never affect the signal computation or the state file.

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
