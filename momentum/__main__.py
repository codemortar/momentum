"""Command-line entry point: `python -m momentum <command>`.

Commands:
  fetch    --universe us|uk [--refresh]
  backtest --universe us [--strategy NAME | --all] [--cost-bps N] [--fee-bps N] [--plot]
  signal   --universe uk [--notify]
  confirm  --universe uk [--strategy NAME] [--yes | --no]
  ledger   --universe uk [--strategy NAME]
  screen   --universe uk|us [--top N] [--near-lows] [--by-sector] [--refresh]
  walkforward --universe us [--select strategies|buffers] [--strategy NAME]
              [--train-years N] [--test-years N] [--tranches N] [--metric sharpe|cagr]
  punt     add|close|list  (individual stock bets, tracked outside the strategy)
  lab      run|report|methods|dashboard [--open]  (paper-trading contest; no real money)
  sync     (mirror state/ into Postgres; needs MOMENTUM_DATABASE_URL)
"""

from __future__ import annotations

import argparse
import sys

from . import config, data


def _cmd_fetch(args: argparse.Namespace) -> int:
    universe = config.get_universe(args.universe)
    data._cli_fetch(universe, refresh=args.refresh)
    return 0


def _cmd_backtest(args: argparse.Namespace) -> int:
    # Imported lazily so `fetch` works before later milestones exist.
    from . import runner

    return runner.run_backtest(
        universe_name=args.universe,
        strategy=None if args.all else args.strategy,
        cost_bps=args.cost_bps,
        fee_bps=args.fee_bps,
        plot=args.plot,
        tranches=args.tranches,
        buffer_bps=args.buffer_bps,
    )


def _cmd_signal(args: argparse.Namespace) -> int:
    from . import signal as signal_mod

    return signal_mod.run_signal(universe_name=args.universe, notify=args.notify)


def _cmd_confirm(args: argparse.Namespace) -> int:
    from . import ledger

    answer = True if args.yes else False if args.no else None
    return ledger.run_confirm(
        universe_name=args.universe, strategy=args.strategy, answer=answer
    )


def _cmd_ledger(args: argparse.Namespace) -> int:
    from . import ledger

    return ledger.run_ledger(universe_name=args.universe, strategy=args.strategy)


def _cmd_screen(args: argparse.Namespace) -> int:
    from . import screen

    return screen.run_screen(
        universe_name=args.universe,
        top=args.top,
        near_lows=args.near_lows,
        refresh=args.refresh,
        by_sector=args.by_sector,
    )


def _cmd_walkforward(args: argparse.Namespace) -> int:
    from . import walkforward

    return walkforward.run_walkforward(
        universe_name=args.universe,
        select=args.select,
        strategy=args.strategy,
        train_years=args.train_years,
        test_years=args.test_years,
        cost_bps=args.cost_bps,
        fee_bps=args.fee_bps,
        tranches=args.tranches,
        metric=args.metric,
    )


def _cmd_sync(args: argparse.Namespace) -> int:
    from . import dbsync

    return dbsync.run_sync()


def _cmd_lab(args: argparse.Namespace) -> int:
    if args.action == "run":
        from .lab.runner import run

        return run(force=args.force)
    if args.action == "report":
        from .lab.report import run_report

        return run_report()
    if args.action == "dashboard":
        from .lab.dashboard import run_dashboard

        return run_dashboard(open_browser=args.open)
    from .lab.methods import REGISTRY

    for method in REGISTRY.values():
        print(f"{method.name:14s} {method.rule}")
    return 0


def _cmd_punt(args: argparse.Namespace) -> int:
    from . import punt_ledger

    return punt_ledger.run_punt(
        action=args.action,
        universe_name=args.universe,
        strategy=args.strategy,
        ticker=args.ticker,
        amount=args.amount,
        price=args.price,
        thesis=args.thesis,
        trigger=args.trigger,
        date=args.date,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="momentum", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="download & cache price data")
    p_fetch.add_argument("--universe", default="us", choices=sorted(config.UNIVERSES))
    p_fetch.add_argument("--refresh", action="store_true", help="re-download even if cached")
    p_fetch.set_defaults(func=_cmd_fetch)

    p_bt = sub.add_parser("backtest", help="run a historical backtest")
    p_bt.add_argument("--universe", default="us", choices=sorted(config.UNIVERSES))
    grp = p_bt.add_mutually_exclusive_group()
    grp.add_argument("--strategy", help="single strategy name")
    grp.add_argument("--all", action="store_true", help="compare all strategies")
    p_bt.add_argument("--cost-bps", type=float, default=10.0)
    p_bt.add_argument("--fee-bps", type=float, default=0.0)
    p_bt.add_argument("--plot", action="store_true", help="save equity/drawdown PNG")
    p_bt.add_argument(
        "--tranches",
        type=int,
        default=1,
        help="split capital across N offset monthly schedules (removes timing luck)",
    )
    p_bt.add_argument(
        "--buffer-bps",
        type=float,
        default=0.0,
        help="only switch when the challenger beats the holding by this margin",
    )
    p_bt.set_defaults(func=_cmd_backtest)

    p_sig = sub.add_parser("signal", help="show current month's holdings signal")
    p_sig.add_argument("--universe", default="uk", choices=sorted(config.UNIVERSES))
    p_sig.add_argument(
        "--notify",
        action="store_true",
        help="also deliver the signal: email via SendGrid if configured "
        "(SENDGRID_API_KEY, MOMENTUM_EMAIL_FROM, MOMENTUM_EMAIL_TO), "
        "else a macOS notification",
    )
    p_sig.set_defaults(func=_cmd_signal)

    p_cf = sub.add_parser(
        "confirm", help="record whether you made the recommended trade(s)"
    )
    p_cf.add_argument("--universe", default="uk", choices=sorted(config.UNIVERSES))
    p_cf.add_argument("--strategy", help="only this strategy (default: all pending)")
    grp_cf = p_cf.add_mutually_exclusive_group()
    grp_cf.add_argument("--yes", action="store_true", help="record as traded, no prompt")
    grp_cf.add_argument("--no", action="store_true", help="record as not traded, no prompt")
    p_cf.set_defaults(func=_cmd_confirm)

    p_lg = sub.add_parser(
        "ledger", help="show recommendation history and strategy-vs-you performance"
    )
    p_lg.add_argument("--universe", default="uk", choices=sorted(config.UNIVERSES))
    p_lg.add_argument("--strategy", help="detailed table for one strategy")
    p_lg.set_defaults(func=_cmd_ledger)

    p_sc = sub.add_parser(
        "screen", help="rank shares on value, quality and balance sheet (not backtested)"
    )
    p_sc.add_argument("--universe", default="uk", choices=["uk", "us"])
    p_sc.add_argument("--top", type=int, default=15, help="rows to show")
    p_sc.add_argument(
        "--near-lows",
        action="store_true",
        help="only names in the bottom third of their 52-week range",
    )
    p_sc.add_argument(
        "--by-sector",
        action="store_true",
        help="rank each company within its own sector, not the whole universe",
    )
    p_sc.add_argument("--refresh", action="store_true", help="re-download fundamentals")
    p_sc.set_defaults(func=_cmd_screen)

    p_wf = sub.add_parser(
        "walkforward",
        help="test whether choosing a strategy/parameter as you go would have worked",
    )
    p_wf.add_argument("--universe", default="us", choices=sorted(config.UNIVERSES))
    p_wf.add_argument(
        "--select",
        default="strategies",
        choices=["strategies", "buffers"],
        help="what to choose between at each step",
    )
    p_wf.add_argument(
        "--strategy", help="strategy whose buffer margin to select (--select buffers)"
    )
    p_wf.add_argument("--train-years", type=int, default=5)
    p_wf.add_argument("--test-years", type=int, default=1)
    p_wf.add_argument("--tranches", type=int, default=1)
    p_wf.add_argument("--cost-bps", type=float, default=10.0)
    p_wf.add_argument("--fee-bps", type=float, default=0.0)
    p_wf.add_argument("--metric", default="sharpe", choices=["sharpe", "cagr"])
    p_wf.set_defaults(func=_cmd_walkforward)

    p_pt = sub.add_parser(
        "punt", help="log individual stock bets and score them against your strategy"
    )
    p_pt.add_argument("action", choices=["add", "close", "list"])
    p_pt.add_argument("--universe", default="uk", choices=sorted(config.UNIVERSES))
    p_pt.add_argument("--strategy", help="benchmark strategy (default: MOMENTUM_STRATEGY)")
    p_pt.add_argument("--ticker")
    p_pt.add_argument("--amount", type=float, help="money committed")
    p_pt.add_argument("--price", type=float, help="your actual fill (default: fetch)")
    p_pt.add_argument("--thesis", help="why you are buying — required to add")
    p_pt.add_argument("--trigger", default="", help="what would make you sell")
    p_pt.add_argument("--date", help="ISO date (default: today)")
    p_pt.set_defaults(func=_cmd_punt)

    p_lab = sub.add_parser("lab", help="paper-trading contest between prediction methods")
    p_lab.add_argument("action", choices=["run", "report", "methods", "dashboard"])
    p_lab.add_argument("--force", action="store_true", help="re-run even if today is marked")
    p_lab.add_argument("--open", action="store_true", help="dashboard: open it in the browser")
    p_lab.set_defaults(func=_cmd_lab)

    p_sync = sub.add_parser("sync", help="mirror state/ into Postgres (MOMENTUM_DATABASE_URL)")
    p_sync.set_defaults(func=_cmd_sync)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
