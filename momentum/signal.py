"""Live signal: what should the portfolio hold right now?

Correctness note (see CLAUDE.md): the signal is evaluated at the last *completed*
month-end, using the exact same strategy functions the backtest validated, called
on price history truncated to that month-end. Running mid-month must NOT invent a
new rule — we truncate to the last month-end and, separately and clearly labelled,
show an informational "if today were month-end" preview that is not tradable.
"""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

import pandas as pd

from . import config, data, ledger
from .backtest import month_end_trading_days
from .config import CASH, EQUITIES_INTL, EQUITIES_US, Universe
from .strategies import (
    MA_WINDOW,
    MOMENTUM_LOOKBACK,
    STRATEGIES,
    VAA_DEFENSIVE,
    VAA_RISK,
    score_13612w,
)


@dataclass
class SignalReport:
    strategy: str
    role: str            # the winning role, e.g. "equities_intl"
    ticker: str          # the real instrument to hold, e.g. "VWRP.L"
    as_of: str           # month-end the signal was computed on (ISO date)


def _weights_to_role(weights: dict[str, float]) -> str:
    """Our strategies always allocate 100% to one role; return that role."""
    return max(weights, key=weights.get)


def last_completed_month_end(index: pd.DatetimeIndex) -> pd.Timestamp:
    """The last month-end we may act on: a genuine month-end that already has a
    following trading day. This deliberately reuses the backtest's own rule (a
    decision at t executes at t+1), so the live signal can never act on the
    in-progress month — whose latest day is not a real month-end — and always
    matches a decision the backtest would have made. Mid-July, this is 30 Jun.
    """
    month_ends = month_end_trading_days(index)
    actionable = [m for m in month_ends if index.get_loc(m) + 1 < len(index)]
    if not actionable:
        raise ValueError("Not enough history to produce a completed month-end signal.")
    return actionable[-1]


def evaluate(universe: Universe) -> tuple[list[SignalReport], pd.Timestamp, pd.DataFrame]:
    prices = data.load_prices(universe)
    as_of = last_completed_month_end(prices.index)
    truncated = prices.loc[:as_of]

    reports = []
    for name, fn in STRATEGIES.items():
        weights = fn(truncated)
        role = _weights_to_role(weights)
        reports.append(
            SignalReport(
                strategy=name,
                role=role,
                ticker=universe.tickers[role],
                as_of=as_of.date().isoformat(),
            )
        )
    return reports, as_of, truncated


def strategy_explanations(prices: pd.DataFrame) -> dict[str, str]:
    """One plain-English line per strategy: the reading behind its decision.
    Presentation only — recomputes the same indicators the strategies use on the
    same truncated history, so the numbers always match the decision."""
    p_us = prices[EQUITIES_US]
    ma = p_us.iloc[-MA_WINDOW:].mean()
    vs_ma = p_us.iloc[-1] / ma - 1.0
    r12 = prices.iloc[-1] / prices.iloc[-(MOMENTUM_LOOKBACK + 1)] - 1.0
    score = score_13612w(prices)

    dm_lead = EQUITIES_US if r12[EQUITIES_US] >= r12[EQUITIES_INTL] else EQUITIES_INTL
    am_lead = EQUITIES_US if score[EQUITIES_US] >= score[EQUITIES_INTL] else EQUITIES_INTL
    weak = [r for r in VAA_RISK if score[r] <= 0]
    risk_txt = ", ".join(f"{r} {score[r]:+.1%}" for r in VAA_RISK)
    defensive_txt = ", ".join(f"{r} {score[r]:+.1%}" for r in VAA_DEFENSIVE)

    return {
        "buy_and_hold": "Baseline for comparison: always 100% US equities, never trades.",
        "ma_200": (
            f"US equities close is {abs(vs_ma):.1%} "
            f"{'ABOVE' if vs_ma > 0 else 'BELOW'} their 200-day average -> "
            f"{'trend up: hold equities' if vs_ma > 0 else 'trend down: hold bonds'}."
        ),
        "dual_momentum": (
            f"12-month returns: US {r12[EQUITIES_US]:+.1%}, "
            f"intl {r12[EQUITIES_INTL]:+.1%}, cash {r12[CASH]:+.1%}. "
            f"{dm_lead} leads"
            + (
                " and beats cash -> hold it."
                if r12[dm_lead] > r12[CASH]
                else " but does not beat cash -> hold bonds."
            )
        ),
        "accel_momentum": (
            f"13612W scores (annualized 1/3/6/12-month blend; recent months "
            f"weigh most): US {score[EQUITIES_US]:+.1%}, "
            f"intl {score[EQUITIES_INTL]:+.1%}, cash {score[CASH]:+.1%}. "
            f"{am_lead} leads"
            + (
                " and beats cash -> hold it."
                if score[am_lead] > score[CASH]
                else " but does not beat cash -> hold bonds."
            )
        ),
        "vaa": (
            f"Risk-asset 13612W scores: {risk_txt}. "
            + (
                f"{' and '.join(weak)} not positive -> play defence: "
                f"best of ({defensive_txt})."
                if weak
                else "All positive -> hold the best risk asset."
            )
        ),
    }


def market_snapshot(prices: pd.DataFrame, universe: Universe) -> list[str]:
    """Trailing total returns per asset — context for the strategy readings."""
    lines = [
        "Market snapshot (total returns to the month-end above):",
        f"  {'role':16s}{'ticker':10s}{'1m':>8s}{'3m':>8s}{'12m':>8s}",
    ]
    for role in prices.columns:
        cells = []
        for lookback in (21, 63, 253):
            if len(prices) > lookback:
                r = prices[role].iloc[-1] / prices[role].iloc[-(lookback + 1)] - 1.0
                cells.append(f"{r:+8.1%}")
            else:
                cells.append(f"{'n/a':>8}")
        lines.append(f"  {role:16s}{universe.tickers[role]:10s}" + "".join(cells))
    return lines


def _state_path(universe: Universe):
    return config.STATE_DIR / f"last_signal_{universe.name}.json"


def load_state(universe: Universe) -> dict:
    path = _state_path(universe)
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def save_state(universe: Universe, reports: list[SignalReport], as_of: pd.Timestamp) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "as_of": as_of.date().isoformat(),
        "signals": {r.strategy: r.role for r in reports},
    }
    _state_path(universe).write_text(json.dumps(state, indent=2))


def notify_macos(title: str, body: str) -> None:
    script = f'display notification {json.dumps(body)} with title {json.dumps(title)}'
    try:
        subprocess.run(["osascript", "-e", script], check=False)
    except OSError:
        # Not macOS (e.g. a Linux server): the printed output above is the
        # delivery of last resort; never let notification plumbing crash the run.
        print("(macOS notification unavailable on this platform)")


# --- Email delivery (SendGrid) -------------------------------------------------

@dataclass(frozen=True)
class EmailConfig:
    api_key: str
    from_addr: str  # must be a SendGrid-verified sender
    to_addr: str


def email_config_from_env() -> EmailConfig | None:
    """Email is configured entirely by environment (the API key must never be
    committed). Returns None unless all three variables are set."""
    api_key = os.environ.get("SENDGRID_API_KEY")
    from_addr = os.environ.get("MOMENTUM_EMAIL_FROM")
    to_addr = os.environ.get("MOMENTUM_EMAIL_TO")
    if api_key and from_addr and to_addr:
        return EmailConfig(api_key=api_key, from_addr=from_addr, to_addr=to_addr)
    return None


def compose_email(
    universe_name: str,
    reports: list[SignalReport],
    changes: list[tuple[str, str, str]],  # (strategy, old_ticker, new_ticker)
    as_of: str,
    explanations: dict[str, str] | None = None,
    snapshot: list[str] | None = None,
) -> tuple[str, str]:
    """Build (subject, plain-text body). Pure, so tests can pin the wording."""
    if changes:
        subject = f"Momentum ({universe_name}): ACTION — {len(changes)} signal(s) changed"
    else:
        subject = f"Momentum ({universe_name}): no change"

    lines = [f"Signals as of month-end {as_of}.", ""]
    if changes:
        lines.append("ACTIONS — if the changed strategy is the one you follow:")
        for strategy, old_ticker, new_ticker in changes:
            lines.append(f"  * {strategy}: SELL {old_ticker}, BUY {new_ticker}")
    else:
        lines.append(
            "No changes this month. If you already hold your strategy's position "
            "below, do nothing."
        )
    section_header = (
        "Your strategy:"
        if len(reports) == 1
        else "What each strategy says (follow ONE; the rest are context):"
    )
    lines += ["", section_header, ""]
    for r in reports:
        lines.append(f"  {r.strategy:15s} HOLD {r.ticker} ({r.role})")
        if explanations and r.strategy in explanations:
            lines.append(f"      {explanations[r.strategy]}")
        lines.append("")  # blank line between strategy blocks for readability
    if snapshot:
        lines += snapshot
    if changes:
        lines += [
            "",
            f"After trading, record it: python -m momentum confirm --universe {universe_name}",
        ]
    lines += ["", "Decision support only — you place the trades. Not financial advice."]
    return subject, "\n".join(lines)


def filter_to_followed(
    reports: list[SignalReport],
    changes: list[tuple[str, str, str]],
    followed: str | None,
) -> tuple[list[SignalReport], list[tuple[str, str, str]]]:
    """Scope the email to the one strategy the user follows (MOMENTUM_STRATEGY),
    so the monthly mail never dangles the other strategies as temptation. An
    unset or unknown name leaves the full set — better a verbose email than a
    silently missing signal."""
    if followed is None or followed not in {r.strategy for r in reports}:
        return reports, changes
    return (
        [r for r in reports if r.strategy == followed],
        [c for c in changes if c[0] == followed],
    )


def send_email_sendgrid(cfg: EmailConfig, subject: str, body: str) -> None:
    """POST to the SendGrid v3 API with stdlib urllib (no SDK dependency).
    Raises urllib.error.HTTPError/URLError on failure; caller decides fallback."""
    payload = {
        "personalizations": [{"to": [{"email": cfg.to_addr}]}],
        "from": {"email": cfg.from_addr},
        "subject": subject,
        "content": [{"type": "text/plain", "value": body}],
    }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30):
        pass  # 2xx means accepted; non-2xx raises HTTPError above


def run_signal(universe_name: str, notify: bool) -> int:
    universe = config.get_universe(universe_name)
    reports, as_of, truncated = evaluate(universe)
    prev = load_state(universe)
    prev_signals = prev.get("signals", {})
    prev_as_of = prev.get("as_of")

    print(f"Signals for universe '{universe.name}' as of month-end {as_of.date()}")
    print(f"(data cached: {data.cache_date(universe)})\n")

    changes: list[tuple[str, str, str]] = []  # (strategy, old_ticker, new_ticker)
    for r in reports:
        old_role = prev_signals.get(r.strategy)
        if old_role is None:
            change = "no previous state"
        elif old_role == r.role:
            change = f"no change (held since {prev_as_of})"
        else:
            old_ticker = universe.tickers.get(old_role, old_role)
            change = f"CHANGED from {old_ticker} -> {r.ticker}"
            changes.append((r.strategy, old_ticker, r.ticker))
        print(f"  {r.strategy:14s} HOLD {r.ticker:8s} ({r.role})   [{change}]")

    save_state(universe, reports, as_of)
    added = ledger.record(universe, reports)
    if added:
        print(
            f"\nRecorded {added} recommendation(s) in the ledger. After trading, "
            f"run: python -m momentum confirm --universe {universe.name}"
        )

    if notify:
        _deliver(universe, reports, changes, as_of, truncated)

    return 0


def _deliver(
    universe: Universe,
    reports: list[SignalReport],
    changes: list[tuple[str, str, str]],
    as_of: pd.Timestamp,
    prices: pd.DataFrame,
) -> None:
    """Email if configured; macOS notification otherwise or on email failure."""
    followed = os.environ.get("MOMENTUM_STRATEGY")
    if followed and followed not in STRATEGIES:
        print(
            f"Warning: MOMENTUM_STRATEGY={followed!r} is not a known strategy "
            f"({', '.join(sorted(STRATEGIES))}); emailing all strategies."
        )
    reports, changes = filter_to_followed(reports, changes, followed)

    cfg = email_config_from_env()
    if cfg is not None:
        subject, body = compose_email(
            universe.name,
            reports,
            changes,
            as_of.date().isoformat(),
            explanations=strategy_explanations(prices),
            snapshot=market_snapshot(prices, universe),
        )
        try:
            send_email_sendgrid(cfg, subject, body)
            print(f"\nEmailed signal to {cfg.to_addr}.")
            return
        except urllib.error.HTTPError as exc:
            # SendGrid explains rejections (bad key, unverified sender) in the
            # response body — surface it or the 4xx alone is undebuggable.
            detail = exc.read().decode("utf-8", "replace")[:300]
            print(
                f"\nEmail failed (HTTP {exc.code}: {detail}); "
                "falling back to macOS notification."
            )
        except (urllib.error.URLError, OSError) as exc:
            print(f"\nEmail failed ({exc}); falling back to macOS notification.")
    else:
        print(
            "\nEmail not configured (set SENDGRID_API_KEY, MOMENTUM_EMAIL_FROM, "
            "MOMENTUM_EMAIL_TO); using macOS notification."
        )
    if changes:
        actions = "; ".join(f"{s}: SELL {old}, BUY {new}" for s, old, new in changes)
        notify_macos("Momentum: signal changed", actions)
    else:
        holds = ", ".join(f"{r.strategy}={r.ticker}" for r in reports)
        notify_macos("Momentum: no change", holds)
