"""One daily cycle: settle, decide, fill, mark. Idempotent per method per bar."""

from __future__ import annotations

from datetime import date

from . import journal
from .broker import Equity, NoMarketError, fill_equity, fill_option
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


def execute(order, market: Market):
    if isinstance(order.instrument, Equity):
        return fill_equity(order, market.bars.iloc[-1])
    quote = market.quote(order.instrument.expiry, order.instrument.strike, order.instrument.right)
    if quote is None:
        raise NoMarketError(f"{order.instrument.key} not in today's chain.")
    return fill_option(order, *quote)


def last_marks(records: list[dict]) -> dict[str, str]:
    """Latest bar date each method has been marked on."""
    out: dict[str, str] = {}
    for r in records:
        if r.get("kind") == "mark":
            out[r["method"]] = max(out.get(r["method"], ""), r["date"])
    return out


def run(force: bool = False) -> int:
    records = journal.read_all()
    markets: dict[str, Market] = {}
    for symbol in sorted({m.underlying for m in REGISTRY.values()}):
        try:
            markets[symbol] = fetch_market(symbol)
        except Exception as exc:
            print(f"  {symbol}: no market data today ({exc}); its methods sit out.")

    accounts = replay(records, list(REGISTRY))
    if UNDERLYING in markets:
        settle_expired(accounts, markets[UNDERLYING])

    for symbol, market in markets.items():
        print(f"{symbol} bar {market.date}: spot {market.spot:,.2f} {market.currency}, "
              f"{len(market.chain)} option quotes")

    marked = last_marks(records)
    ran = 0
    for name, method in REGISTRY.items():
        market = markets.get(method.underlying)
        # A stale or missing bar (London data gaps, UK holidays) must not trade twice.
        if market is None or (not force and marked.get(name, "") >= market.date):
            continue
        ran += 1
        account = accounts[name]
        try:
            orders = method.decide(Context(market=market, account=account, name=name))
        except Exception as exc:
            print(f"  {name:16s} DECIDE FAILED: {exc}")
            orders = []
        for order in orders:
            try:
                fill = execute(order, market)
            except NoMarketError as exc:
                print(f"  {name:16s} rejected: {exc}")
                continue
            journal.append({"kind": "fill", "date": market.date, "method": name,
                            "instrument": fill.instrument_key, "qty": fill.qty,
                            "price": fill.price, "cash_delta": fill.cash_delta,
                            "friction": fill.friction})
            account.apply_fill(fill.instrument_key, fill.qty, fill.price,
                               fill.cash_delta, fill.friction)
            print(f"  {name:16s} {'BUY' if fill.qty > 0 else 'SELL':4s} "
                  f"{abs(fill.qty)} {fill.instrument_key} @ {fill.price:,.2f}")

        value = account.value(_mark_prices(account, market))
        journal.append({"kind": "mark", "date": market.date, "method": name, "value": value})
        print(f"  {name:16s} value {value:,.2f} {market.currency}")

    if ran == 0:
        print("No market has a new bar since the last run; nothing to do.")
    return 0


def _mark_prices(account, market: Market) -> dict[str, float]:
    marks: dict[str, float] = {Equity(market.symbol).key: market.spot}
    for key in account.positions:
        if key.startswith("OPT:"):
            _, _, expiry, strike, right = key.split(":")
            mid = market.mid(expiry, float(strike), right)
            if mid is not None:
                marks[key] = mid
    return marks
