# momentum

A small, honest backtester and monthly signal tool for simple ETF trend/momentum
strategies, built for a UK Stocks & Shares ISA (tax-free, monthly rebalance,
zero-commission broker).

**This is a decision-support tool, not an auto-trader.** It tells you what to
hold; you place the (roughly two-minutes-a-month) trades yourself. It does not
touch a broker or move money. Nothing here is financial advice.

## Why it exists

Momentum/trend-following is one of the most evidence-backed anomalies in finance,
but only worth doing if it survives realistic costs and you can sit through its
lean years. This project lets you check that on decades of data *before* risking
anything — and its whole design is built to not lie to you about it (no lookahead
bias, real costs, the live signal runs the exact code the backtest validated).

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                        # 40 tests, no network required
```

## Usage

```bash
# 1. Cache price data (needs network; only this step does)
python -m momentum fetch --universe us
python -m momentum fetch --universe uk

# 2. Backtest and compare strategies
python -m momentum backtest --universe us --all --plot
#    -> comparison table + output/us_all.png (equity curves + drawdowns)

# 3. See what to hold this month
python -m momentum signal --universe uk --notify

# 4. After trading (or deciding not to), tell the ledger what you did
python -m momentum confirm --universe uk

# 5. How have the recommendations done — and did following them pay?
python -m momentum ledger --universe uk
```

Every `signal` run records its recommendations. `confirm` asks about the ones
where a trade was actually recommended and you haven't answered; `ledger` then
compares the strategy's path against the path you actually took (the
**behaviour gap** — what hesitation cost you):

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
`ma_200: SELL VUSA.L, BUY VGOV.L` — plus the current holding for every strategy.
Sending uses SendGrid's plain HTTPS API via the stdlib (no extra dependency).

### Example (US, 2005–2026)

```
strategy             CAGR    MaxDD     Vol  Sharpe  Trades  CostDrag
------------------------------------------------------------------
buy_and_hold       11.06%  -55.19%  19.23%    0.56       0    0.00%/yr
ma_200              9.94%  -31.05%  13.16%    0.66      30    0.16%/yr
dual_momentum       8.21%  -33.72%  16.00%    0.47      34    0.18%/yr
```

The headline: trend-following gives up ~1% of CAGR to roughly **halve** the worst
drawdown (dodging most of 2008–09) and improve risk-adjusted return (Sharpe). Read
`SPEC.md` for the rules and the honest limitations (short crashes, drift, etc.).

## Universes

- **us** — long-history US ETFs (SPY/EFA/IEF/GLD + a T-bill cash index). Deep
  enough to span 2008 and 2020: this is what *validates* a strategy.
- **uk** — the actual London-listed UCITS ETFs you'd buy in an ISA
  (VUSA/VWRP/VGOV/SGLN + ERNS cash). Short history — a **sanity check** that the
  same code runs coherently on the real instruments, not validation.

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
    <string>/Users/christopherpolly/github/momentum/.venv/bin/python</string>
    <string>-m</string><string>momentum</string>
    <string>signal</string><string>--universe</string><string>uk</string>
    <string>--notify</string>
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
fires on the 1st of each month at 09:00. (It fetches cached data; add a `fetch
--refresh` step if you want fresh prices first.)

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
EOF
chmod 600 ~/momentum.env
```

Then `crontab -e` and schedule the monthly run — refresh prices first, then
signal (times are UTC on a stock droplet):

```cron
0 9 1 * * . $HOME/momentum.env && cd $HOME/momentum && .venv/bin/python -m momentum fetch --universe uk --refresh && .venv/bin/python -m momentum signal --universe uk --notify >> $HOME/momentum-cron.log 2>&1
```

The `state/` directory lives on the droplet, so change detection ("CHANGED
from X -> Y") keeps working month to month. Check `momentum-cron.log` if an
email ever fails to arrive — on Linux there is no notification fallback, the
log is the record.

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
tests/                   synthetic-data tests (no network)
```

See `CLAUDE.md` for the iron rules that keep it trustworthy.
