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
pytest                        # 19 tests, no network required
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
```

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
  <key>StartCalendarInterval</key>
  <dict><key>Day</key><integer>1</integer><key>Hour</key><integer>9</integer></dict>
</dict></plist>
```

Then `launchctl load ~/Library/LaunchAgents/co.codemortar.momentum.plist`. It
fires on the 1st of each month at 09:00. (It fetches cached data; add a `fetch
--refresh` step if you want fresh prices first.)

## Layout

```
momentum/config.py       universes (role->ticker) + cost model
momentum/data.py         yfinance fetch, CSV cache, role mapping, cash index
momentum/strategies.py   pure signal functions — the trust core
momentum/backtest.py     engine: decide month-end, execute next day, costs
momentum/metrics.py      CAGR, max drawdown, vol, Sharpe
momentum/report.py       comparison table + equity/drawdown PNG
momentum/runner.py       CLI orchestration for backtests
momentum/signal.py       live signal, state file, macOS notification
tests/                   synthetic-data tests (no network)
```

See `CLAUDE.md` for the iron rules that keep it trustworthy.
