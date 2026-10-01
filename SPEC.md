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

### Extended history (us_ext, us_long)

ETFs are young — GLD begins in 2004, IEF in 2002 — so an ETF-only backtest
never sees a bear market before 2008. Two further universes splice older
total-return **mutual funds** in behind each ETF (index tickers like `^GSPC`
are price-only and would understate returns by the dividend yield):

| Universe  | Roles | Reaches | Buys back |
|-----------|-------|---------|-----------|
| `us_ext`  | all five | ~2000 | the dot-com bear market |
| `us_long` | US equities, bonds, cash | 1980 | 1987, the early 90s, dot-com |

Chains: SPY←VFINX, EFA←VGTSX, IEF←FGOVX, GLD←GC=F. `us_long` deliberately drops
gold and international — neither has deep free daily total-return history — to
reach 1980; strategies needing those roles are skipped, not crashed
(`strategies.REQUIRED_ROLES`).

Splicing rescales the older series to meet the newer at their first overlapping
date. Only *levels* are rescaled, never returns, so no lookahead is introduced:
a constant factor cannot change a ratio of adjacent prices. Caveats: the funds
carry their own fees and tracking differences, so spliced returns approximate
the ETF's; and the cache records the earliest date each ticker was fetched
from, because a symbol first cached for a short universe would otherwise
silently cap a longer one.

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

## Robustness options (tranching and buffers)

Both are backtest options, off by default, so the effect of each is measurable
against the plain result rather than silently baked in.

- **`--tranches N`** splits capital across N equal slices rebalanced on monthly
  schedules offset by a fraction of a month, and averages them. This removes
  *rebalance timing luck*: the arbitrary dependence on trading at month-end
  rather than mid-month. It generally lowers volatility, and it can make a
  drawdown look *worse* — that is the point, not a bug. A single-schedule
  drawdown that improves under tranching was partly luck.
- **`--buffer-bps N`** requires a challenger to beat the current holding by N
  basis points of the strategy's own preference score before switching. Buffered
  strategies take `(prices, held)`; the engine threads the holding through in
  date order, which is not lookahead because the holding derives only from
  earlier decisions. Margins are in the strategy's own units: annualized return
  for the momentum strategies, distance from the moving average for `ma_200`.

Any margin chosen from a backtest must be checked for **parameter sensitivity**
before it is trusted: a result that is good at one margin and poor at the
margins either side is a fluke, not an effect. On US data `ma_200` at 200bps
looks outstanding and its neighbours at 100 and 300bps do not — that spike is
noise. `accel_momentum` improves smoothly across the whole range, which is more
credible, though partly confounded by a mostly-rising equity market over the
sample.

## Walk-forward analysis

`walkforward` answers the question a comparison table cannot: would *choosing*
as you went have worked? It selects the best candidate on a rolling in-sample
window (5 years by default, by Sharpe), applies that choice to the next
out-of-sample year, rolls forward, and stitches the out-of-sample years into
one curve. Candidates are either the strategies (`--select strategies`) or the
buffer margins for one strategy (`--select buffers --strategy NAME`).

Selection reads only the in-sample window; out-of-sample returns come from
decisions the strategy would have made live, which is why candidate curves can
be computed once over the full history and sliced (the no-lookahead property
test is what makes that valid).

The report prints the stitched result against each candidate held throughout
the same span, the number of times the selection changed, and the gap to the
hindsight best. Frequent churn plus a large gap means the in-sample winner was
noise. On US data, selecting `ma_200`'s buffer margin this way churns five
times and lands 2.74%/yr behind the hindsight-best 200bps — confirming that
spike could not have been captured in advance.

## Punt book (individual stock positions)

`punt add|close|list` logs discretionary single-stock bets and scores each one
against what the same money would have done in the strategy being followed over
exactly the same days. A thesis is required to open a position, so the reason
is recorded before the outcome is known rather than reconstructed afterwards.

Kept in `state/punts.json`, entirely separate from the strategy ledger: a good
punt is not evidence the system works and a bad one is not evidence it fails,
so punts never enter the ledger, the backtest, or any performance claim.

Entry prices are fetched **as of the purchase date**, not the current date, so
a backdated position shows its real gain or loss. Positions in the same ticker
stay separate rather than being averaged, and closing is first-in-first-out.

## Paper-trading lab (`lab`)

A dated contest: daily paper trading of several prediction methods from
2026-10-01 to an assessment on 2027-01-01. No real money, no broker access.
Separate from the ETF strategies — it shares nothing with the signal, ledger
or backtest except the `state/` directory.

Honesty rules (the point of the exercise):

1. **Far-touch fills.** Buys at the ask, sells at the bid, plus commission and
   slippage. Mid-price fills are how paper trading lies.
2. **Pre-registration.** A method's rule is fixed before it trades. Changing a
   rule means retiring it and registering a successor under a new name.
3. **Controls.** `random_walk` (coin flip) and `lunar` (moon phase, a placebo)
   trade with identical costs. No edge is claimed for anything that cannot
   beat them.
4. **Append-only journal** (`state/lab_journal.jsonl`); accounts are rebuilt by
   replaying it and the report is a pure function of it.
5. **Kill fast, promote slowly.** Three months can refute a method, never
   validate one — short-volatility methods least of all, since their losses
   are rare and large.

Mechanics: 100,000 paper per method in its market's currency (one
cash-secured SPY put needs ~$70k of collateral). Two markets: SPY, the only one
with free option chains via yfinance, and ISF.L (iShares FTSE 100, quoted in
pence by Yahoo and converted to pounds). Equities pay per-side slippage of 2bps
on SPY and 8bps on ISF.L, whose spreads are wider; options pay $0.65 per
contract plus the spread. A method only trades when its own market has a bar
newer than its last mark, so London data gaps and UK holidays cannot cause a
double trade on a stale bar. Runs at ~19:30 UTC on weekdays — inside US market
hours year-round, because Yahoo zeroes option quotes once the market closes
and dead quotes are rejected rather than guessed at. Short options reaching
expiry are cash-settled at intrinsic against the underlying, approximating
assignment. A run is idempotent per trading day.

| Method | Rule | Family |
|---|---|---|
| theta_puts | Sell 1 cash-secured SPY put ~5% OTM, 28–45 DTE; buy back at 80% profit or 7 DTE, re-sell | volatility risk premium |
| theta_spread | Sell 1 SPY put ~5% OTM, buy 1 ~$5 lower, 28–45 DTE; close both at 80% profit or 7 DTE. $1,300 (~£1,000) account | volatility risk premium, capped loss |
| overnight | Buy SPY at the close, sell at the next open | overnight anomaly |
| sma_cross | Long SPY while 20d SMA > 100d SMA, else cash | trend |
| random_walk | Long or flat by date-seeded coin flip | control |
| lunar | Long while the moon waxes | placebo |
| overnight_ftse | As overnight, on ISF.L | overnight anomaly (UK) |
| sma_cross_ftse | As sma_cross, on ISF.L | trend (UK) |
| random_walk_ftse | Coin flip on ISF.L, its own seed | control (UK) |

**Snapshots.** Each run saves every market's latest bar and (for SPY) its
option chain out to 95 days, under `state/lab_snapshots/<bar date>/`: once per
bar, never overwritten. Yahoo serves only the current chain, so these are the
only record of the quotes the contest traded against. They let a *new* method
be replayed immediately against real historical quotes instead of waiting
three months for a forward test. They must not be used to tune a method already
in the contest: fitting a rule to the same period it is scored on voids its
result. A method developed on snapshots still has to prove itself going
forward. Like the journal, snapshots live in `state/` because they cannot be
regenerated, so the server needs backups.

**Database mirror.** `python -m momentum sync` copies the irreplaceable
`state/` data into Postgres: the lab journal, the snapshots, and the JSON state
files (signal state, ledger, punt book). The files stay the source of truth;
the database is the off-server, backed-up, queryable copy. Every write is
idempotent (journal lines keyed by content hash, snapshots by symbol and date,
documents updated only when changed), so a failed or missed sync is caught up
by the next. Both scheduled jobs sync after their work and treat a sync failure
as non-fatal. Configured by `MOMENTUM_DATABASE_URL` in `~/momentum.env`; without
it, sync does nothing. The connection runs in autocommit mode, because a bare
read otherwise opens an implicit transaction and later blocks become savepoints
that are never committed.

**Dashboard.** `python -m momentum lab dashboard [--open]` writes
`output/lab_dashboard.html`: return curves per market (indexed to 0%, so USD
and GBP accounts share one axis), the scoreboard, open positions and recent
fills. It reads the database through its own variable,
`MOMENTUM_READ_DATABASE_URL` (environment or the repo's gitignored `.env`), on a
read-only connection, else the local journal. It deliberately ignores sync's
`MOMENTUM_DATABASE_URL`, so a machine set up only to view can never push its
own `state/` into the database.

FTSE methods are judged only against `random_walk_ftse`.

### Graduation criteria (pre-registered 2026-10-01, before any trade)

Fixed now, while the scoreboard is empty, so January's decision cannot be fitted
to January's results. A method earns a real-money pilot only by passing **all
four**, judged on the server's journal at the 2027-01-01 assessment:

1. **Beats its control** — finishes at least 3 percentage points ahead of its
   own market's `random_walk` control, net of all costs.
2. **Has a documented mechanism** — a published reason it should work.
   `theta_puts`, `overnight` and `sma_cross` (and their FTSE twins) qualify;
   `lunar` and the controls never can.
3. **Drawdown within limit** — maximum drawdown over the contest no worse than
   15%.
4. **Survives a longer test** — share-trading methods must beat buy-and-hold on
   risk-adjusted return over 20+ years of history after costs; option methods
   must keep a simulated sudden 20% index drop within the 15% limit.

Criteria 1–3 are filters, not proof: three months is too short to separate skill
from luck. Criterion 4 carries the weight.

**Pilot terms.** A passing method gets £1,000 of real money for three months,
tracked trade by trade against its paper twin. Hard stop: if the pilot falls to
£800 it closes and is reviewed — decided now, not mid-drop. Scaling up requires
real fills to match paper fills. A method that fails any criterion is retired
with its record intact. Options cannot be held in an ISA, and a cash-secured put
needs far more than £1,000 of collateral, so a put-selling pilot at this size
would need a capped-loss (spread) variant registered as its own method —
`theta_spread`, registered 2026-10-01 before the first run. It trades SPY's
chain as a proxy for the equivalent XSP spread, and its account is sized to the
pilot ($1,300) rather than 100,000, so its returns and drawdowns show what the
real £1,000 would experience; one maximum loss is roughly a third of it.

Multi-leg orders are all-or-nothing: if any leg lacks a live two-sided quote,
the runner trades none of them, since a lone short leg is an uncapped position. They were registered
on 2026-10-01 alongside the SPY roster, before any trade. Theta is US-only: no
free UK option data, and UK index options are thin and largely closed to
retail.

Backlog, added as new methods only: RSI-2 mean reversion, turn-of-month,
VIX-regime filter, covered calls, long calls on momentum.

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
