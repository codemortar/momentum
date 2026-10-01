"""One daily cycle: settle, decide, fill, mark. Idempotent per trading day."""

from __future__ import annotations

from datetime import date

from . import journal
from .broker import Equity, NoMarketError, Option, fill_equity, fill_option
from .config import UNDERLYING
from .data import Market, fetch_market
from .methods import Context, REGISTRY
from .portfolio import replay


def settle_expired(accounts, market: Market) -> None:
    for name, account in accounts.items():
        for key in list(account.positions):
            if not key.startswith("OPT:"):
                continue
            _, _, expiry, strike, right = key.split(":")
            if date.fromisoformat(expiry) >= date.fromisoformat(market.date):
                continue
            intrinsic = max(0.0, (float(strike) - market.spot) if right == "P" else (market.spot - float(strike)))
            journal.append({"kind": "settle", "date": market.date, "method": name,
                            "instrument": key, "intrinsic": intrinsic})
            account.settle_option(key, intrinsic)


def execute(name: str, order, market: Market):
    if isinstance(order.instrument, Equity):
        return fill_equity(order, market.bars.iloc[-1])
    quote = market.quote(order.instrument.expiry, order.instrument.strike, order.instrument.right)
    if quote is None:
        raise NoMarketError(f"{order.instrument.key} not in today's chain.")
    return fill_option(order, *quote)


def run(force: bool = False) -> int:
    records = journal.read_all()
    market = fetch_market(UNDERLYING)
    if not force and journal.last_mark_date(records) == market.date:
        print(f"Already ran for {market.date}; nothing to do.")
        return 0

    accounts = replay(records, list(REGISTRY))
    settle_expired(accounts, market)

    print(f"Lab run for {market.date} (spot {market.spot:.2f}, "
          f"{len(market.chain)} option quotes)")
    for name, method in REGISTRY.items():
        account = accounts[name]
        try:
            orders = method.decide(Context(market=market, account=account, name=name))
        except Exception as exc:
            print(f"  {name:12s} DECIDE FAILED: {exc}")
            continue
        for order in orders:
            try:
                fill = execute(name, order, market)
            except NoMarketError as exc:
                print(f"  {name:12s} rejected: {exc}")
                continue
            journal.append({"kind": "fill", "date": market.date, "method": name,
                            "instrument": fill.instrument_key, "qty": fill.qty,
                            "price": fill.price, "cash_delta": fill.cash_delta,
                            "friction": fill.friction})
            account.apply_fill(fill.instrument_key, fill.qty, fill.price,
                               fill.cash_delta, fill.friction)
            print(f"  {name:12s} {'BUY' if fill.qty > 0 else 'SELL':4s} "
                  f"{abs(fill.qty)} {fill.instrument_key} @ {fill.price:.2f}")

    marks = _mark_prices(accounts, market)
    for name, account in accounts.items():
        value = account.value(marks)
        journal.append({"kind": "mark", "date": market.date, "method": name, "value": value})
        print(f"  {name:12s} value {value:,.2f}")
    return 0


def _mark_prices(accounts, market: Market) -> dict[str, float]:
    marks: dict[str, float] = {Equity(UNDERLYING).key: market.spot}
    for account in accounts.values():
        for key in account.positions:
            if key.startswith("OPT:") and key not in marks:
                _, _, expiry, strike, right = key.split(":")
                mid = market.mid(expiry, float(strike), right)
                if mid is not None:
                    marks[key] = mid
    return marks
