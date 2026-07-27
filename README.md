# momentum

A backtester and monthly signal tool for simple ETF momentum strategies, built
for a UK Stocks and Shares ISA. It works out what to buy/hold/sell each month, emails me
the result, and I place the trades.

I built this to see if simple trend-following rules beat buy-and-hold.

Every decision uses only data up to the decision date and trades the next day,
prices are never backfilled, and the live signal runs the same code the backtest
was validated on.

## Stack and how it's built

Python 3, pandas, matplotlib and yfinance. No web framework, no database and no
install step: state is a handful of CSV and JSON files, and everything runs
through `python -m momentum`. It runs live for me each month on a small
DigitalOcean droplet via cron, which emails the signal through SendGrid.

The core is a set of pure `prices -> weights` functions in `strategies.py` that
both the backtest engine and the live signal call. 

Features:

- No lookahead. A property test feeds each strategy truncated history and checks
  that past decisions never change when later data is added.
- Decisions are made on the month-end close and executed the next trading day.
- Real costs. A per-rebalance trading cost and optional annual fee, so the
  comparison between strategies isn't a fantasy.
- 112 fast, offline tests covering the strategy logic, backtest engine and signal
  timing, including the property-based no-lookahead test above. No network, runs
  in under a second.
- The monthly job runs the whole suite first (`run_monthly.sh`) and only acts on
  the market if it passes — a broken test aborts the run rather than trading on it.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest
```

## Usage

```bash
# 1. Cache price data (needs network; only this step does)
python -m momentum fetch --universe us
python -m momentum fetch --universe uk

# 2. Backtest and compare strategies
python -m momentum backtest --universe us --all --plot
python -m momentum backtest --universe us --all --tranches 4 --buffer-bps 300

# 2b. Would choosing a strategy as you went have worked? (walk-forward)
python -m momentum walkforward --universe us --select strategies

# 3. See what to buy/hold this month
python -m momentum signal --universe uk --notify

# 4. After trading (or deciding not to), tell the ledger what you did
python -m momentum confirm --universe uk

# 5. How have the recommendations done — and did following them pay?
python -m momentum ledger --universe uk
```

### Punt book

Individual stock bets, kept completely separate from the ETF strategy and
scored against it, so I find out whether my own picks are actually any good:

```bash
python -m momentum punt add --ticker PRU.L --amount 500 \
    --thesis "top of the value+quality screen" --trigger "F-score drops below 4"
python -m momentum punt list
python -m momentum punt close --ticker PRU.L
```

```
  ticker    opened         amount   return accel_moment       vs
  PRU.L     2026-04-01        500    +0.6%        +3.4%    -2.9%
      thesis: top of the value+quality screen
  HLMA.L    2026-05-01        300   -21.1%        +4.4%   -25.5%

  Punt pot: 800 in, worth 739 (-7.6%).
  Same money in accel_momentum: 830 (+3.8%) — the system is ahead.
```

A thesis is required, so the reason is on record before the outcome is known.
Punts never touch the strategy ledger or any backtest figure.

### Stock screener

Separate from the ETF strategies, `screen` ranks UK or US large caps on value,
quality and balance-sheet strength to give me a shortlist worth researching:

```bash
python -m momentum screen --universe uk               # top 15 by composite score
python -m momentum screen --universe us --top 25
python -m momentum screen --universe uk --near-lows   # only names near 52-week lows
python -m momentum screen --universe uk --by-sector   # rank within sector, not overall
```

```
ticker    name                   score    P/E   P/B    ROE  Margin    D/E  Yield   52w
--------------------------------------------------------------------------------------
PRU.L     PRUDENTIAL PLC ORD 5    0.87    9.5   1.8    21%     28%     28   1.8%  0.55
NWG.L     NATWEST GROUP PLC OR    0.82    9.7   1.4    14%     37%      -   4.8%  0.88
```

The score is the mean percentile rank across the factors, so 1.00 is best. **F**
is the Piotroski F-Score: nine binary tests of financial health (profitability,
cash generation, leverage, dilution, margin and efficiency trends) from the last
two annual reports, shown as points scored over tests that could be evaluated —
a bank with no gross-profit line scores out of seven rather than being marked
down for it. `--by-sector` ranks each company against its own sector, which
matters because a bank's price-to-book means something different from a miner's.

Set `MOMENTUM_SCREEN=5` alongside the other environment variables to append the
top five names to the monthly signal email.

Unlike the strategies, this is **not backtested and makes no performance
claim**: yfinance gives current fundamentals, not the point-in-time figures
known at each past date, so an honest historical test would need a paid data
subscription. It's a research funnel, not a buy list.

Every `signal` run records its recommendations. `confirm` asks about the ones
where a trade was actually recommended and you haven't answered. `ledger` then
compares the strategy's path against the path you actually took, so you can see
what hesitating or skipping a trade actually cost:

```
  ma_200          since 2026-06-30: strategy +4.20%, you +2.90%, behaviour gap -1.30%
```

### Email notifications (SendGrid)

With `--notify`, the signal is emailed if these three environment variables are
set (otherwise it falls back to a macOS notification):

```bash
export SENDGRID_API_KEY="SG...."             # keep out of the repo!
export MOMENTUM_EMAIL_FROM="you@yourdomain"  # must be a verified sender in SendGrid
export MOMENTUM_EMAIL_TO="you@yourdomain"
```

The email spells out the action per changed strategy — e.g.
`ma_200: SELL VUSA.L, BUY VGOV.L` — plus each strategy's holding with the
indicator readings behind it and a market snapshot of trailing returns.
Sending uses SendGrid's plain HTTPS API via the stdlib (no extra dependency).

Once you've committed to one strategy, scope the email to it:

```bash
export MOMENTUM_STRATEGY="accel_momentum"   # optional: email only this strategy
```

Then ACTION in the subject always means *you* need to trade, and the other
strategies aren't dangled as monthly temptation. The console and ledger still
track all strategies.

Optionally, for entertainment only, the email can append a "punt of the month"
(the strongest-momentum share from a fixed FTSE-100 list — explicitly
unvalidated, see SPEC.md, and clearly labelled as such in the email):

```bash
export MOMENTUM_PUNT=1                      # optional: opt into the fun section
```

### Example (US, 2005–2026)

```
strategy             CAGR    MaxDD     Vol  Sharpe  Trades  CostDrag
------------------------------------------------------------------
buy_and_hold       11.06%  -55.19%  19.23%    0.56       0    0.00%/yr
ma_200              9.94%  -31.05%  13.16%    0.66      30    0.16%/yr
dual_momentum       8.21%  -33.72%  16.00%    0.47      34    0.18%/yr
```

The takeaway: trend-following gives up about 1% of annual return but roughly
halves the worst drawdown, mostly by sidestepping 2008–09, and improves the
risk-adjusted return (Sharpe). SPEC.md has the full rules and the limitations I
know about, such as lag in fast crashes and adjusted-price drift.

## Universes

- **us**: long-history US ETFs (SPY/EFA/IEF/GLD plus a T-bill cash index). Deep
  enough to span 2008 and 2020, so this is the universe I use to validate a
  strategy.
- **uk**: the London-listed UCITS ETFs you'd actually buy in an ISA
  (VUSA/VWRP/VGOV/SGLN plus ERNS for cash). The history is short, so it's really
  a check that the same code runs sensibly on the real instruments rather than a
  validation in its own right.
- **us_ext** and **us_long**: the same roles with older total-return mutual
  funds spliced in behind each ETF (SPY←VFINX, IEF←FGOVX and so on), reaching
  back to ~2000 and 1980 respectively. ETFs are too young to have lived through
  much: without this, no backtest here sees a bear market before 2008.
  `us_long` drops gold and international, which have no deep free history, to
  get back to 1980; strategies needing those roles are skipped rather than run
  on data that doesn't exist.

Longer history changes the conclusions materially. Over 2005–2026 `ma_200`
looks like it costs about 1% of annual return for its lower drawdown; measured
from 1981 it gave up nothing at all:

```
us_long, 1981-2026     CAGR    MaxDD     Vol  Sharpe
buy_and_hold         11.04%  -55.19%  18.07%    0.46
ma_200               11.11%  -33.08%  13.63%    0.56
```

## Running the signal automatically (optional, manual setup)

The signal is a monthly check. On macOS, `launchd` is preferred over `cron`
because it runs jobs missed while the laptop was asleep. Create
`~/Library/LaunchAgents/co.codemortar.momentum.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>co.codemortar.momentum</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/christopherpolly/github/momentum/run_monthly.sh</string>
    <string>uk</string>
  </array>
  <key>WorkingDirectory</key><string>/Users/christopherpolly/github/momentum</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>SENDGRID_API_KEY</key><string>SG....</string>
    <key>MOMENTUM_EMAIL_FROM</key><string>you@yourdomain</string>
    <key>MOMENTUM_EMAIL_TO</key><string>you@yourdomain</string>
  </dict>
  <key>StartCalendarInterval</key>
  <dict><key>Day</key><integer>1</integer><key>Hour</key><integer>9</integer></dict>
</dict></plist>
```

Then `launchctl load ~/Library/LaunchAgents/co.codemortar.momentum.plist`. It
fires on the 1st of each month at 09:00 and runs `run_monthly.sh`, which tests,
then refreshes prices, then signals.

## Running on a server (e.g. a DigitalOcean droplet)

A cheap always-on Linux box beats the laptop for reliability (no missed runs),
and email is the natural notification channel there. One-time setup on a basic
droplet:

```bash
sudo apt install -y python3-venv git
git clone https://github.com/codemortar/momentum.git ~/momentum
cd ~/momentum && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Put the secrets in an env file **outside the repo** so they can never be
committed, and lock it down:

```bash
cat > ~/momentum.env <<'EOF'
export SENDGRID_API_KEY="SG...."
export MOMENTUM_EMAIL_FROM="you@yourdomain"
export MOMENTUM_EMAIL_TO="you@yourdomain"
export MOMENTUM_STRATEGY="accel_momentum"   # optional: email only your strategy
EOF
chmod 600 ~/momentum.env
```

Then `crontab -e` and schedule the monthly run. It calls `run_monthly.sh`, which
runs the test suite first and only fetches prices and sends the signal if the
suite passes (times are UTC on a stock droplet):

```cron
0 9 1 * * . $HOME/momentum.env && $HOME/momentum/run_monthly.sh uk >> $HOME/momentum-cron.log 2>&1
```

`run_monthly.sh` is the pre-flight gate: because the signal drives real money,
a broken test suite aborts the run rather than acting on it. If the tests ever
fail, no email arrives and the reason is in `momentum-cron.log` — on Linux there
is no notification fallback, so the log is the record. The `state/` directory
lives on the droplet, so change detection ("CHANGED from X -> Y") keeps working
month to month.

## Layout

```
momentum/config.py       universes (role->ticker) + cost model
momentum/data.py         yfinance fetch, CSV cache, role mapping, cash index
momentum/strategies.py   pure signal functions — the trust core
momentum/backtest.py     engine: decide month-end, execute next day, costs
momentum/metrics.py      CAGR, max drawdown, vol, Sharpe
momentum/report.py       comparison table + equity/drawdown PNG
momentum/runner.py       CLI orchestration for backtests
momentum/signal.py       live signal, state file, email/macOS delivery
momentum/ledger.py       did-you-trade ledger + strategy-vs-you performance
momentum/buffers.py      trade buffers: don't switch unless clearly better
momentum/walkforward.py  would choosing as you went have worked?
momentum/screen.py       fundamental stock screener (research tool, not a signal)
momentum/punt_ledger.py  individual stock bets, scored against the strategy
tests/                   synthetic-data tests (no network)
```
