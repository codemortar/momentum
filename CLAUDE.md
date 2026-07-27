# CLAUDE.md — momentum

Personal ISA momentum backtester + monthly signal notifier. **Correctness and
readability beat sophistication.** If a feature isn't in `SPEC.md`, don't build it.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest                                             # all tests, no network

python -m momentum fetch    --universe us|uk [--refresh]
python -m momentum backtest --universe us [--strategy NAME | --all] [--cost-bps N] [--fee-bps N] [--plot]
python -m momentum signal   --universe uk [--notify]
python -m momentum confirm  --universe uk [--strategy NAME] [--yes | --no]
python -m momentum ledger   --universe uk [--strategy NAME]
python -m momentum screen   --universe uk|us [--top N] [--near-lows] [--by-sector] [--refresh]
```

## Architecture in five lines

`data.py` loads prices into a DataFrame with **role-named** columns (never ticker
names) → a pure **strategy** function maps that history to target weights → the
**backtest** engine calls the strategy once per month-end on *truncated* history
and executes the next day → `report.py`/`signal.py` present results. The backtest
and the live signal call the **same strategy functions**, so what you validate is
what you act on.

## Iron rules (do not relax — these are correctness invariants, not preferences)

1. **Decide at month-end close t, execute at t+1.** Never execute on the decision
   bar. Enforced in `backtest.py`, asserted by `test_execution_happens_the_day_after_the_decision`.
2. **Strategies are pure** `prices -> weights`, using only data up to the last
   row. They must not import `backtest`/`data`, and must never look past the last
   row (that's lookahead).
3. **No `bfill`, ever.** `data.py` uses `ffill` then `dropna` from the front only.
   Backfilling pulls future prices into the past.
4. **Tests use synthetic data only — no network.** Fixtures live in
   `tests/conftest.py` with answers known by construction.
5. **Never weaken or skip the no-lookahead property test**
   (`test_future_data_does_not_change_past_decisions`). It is the trust anchor.
6. The **live signal acts on the last *completed* month-end**, never the
   in-progress month (`signal.last_completed_month_end`).

## Conventions

- stdlib `venv` + `pip`; no `pyproject.toml`. Flat package so `python -m momentum`
  works with zero install. No new dependencies without discussion.
- Roles are the vocabulary: `equities_us`, `equities_intl`, `bonds`, `gold`,
  `cash` (see `config.py`).
- Type hints on public functions; dataclasses for config/results.
- `data/`, `output/`, `state/` are gitignored (regenerable).

## Out of scope

Live trading, broker APIs, crypto, intraday, optimization, web UI. Future work:
walk-forward analysis.
