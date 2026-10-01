"""Per-method accounts, rebuilt by replaying the journal so history stays the only truth."""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import OPTION_MULTIPLIER, START_CAPITAL


@dataclass
class Position:
    qty: int
    avg_price: float   # entry price per unit, for profit-take rules


@dataclass
class Account:
    cash: float = START_CAPITAL
    positions: dict[str, Position] = field(default_factory=dict)
    friction: float = 0.0
    n_fills: int = 0

    def apply_fill(self, key: str, qty: int, price: float, cash_delta: float, friction: float) -> None:
        self.cash += cash_delta
        self.friction += friction
        self.n_fills += 1
        pos = self.positions.get(key)
        if pos is None:
            self.positions[key] = Position(qty=qty, avg_price=price)
            return
        new_qty = pos.qty + qty
        if new_qty == 0:
            del self.positions[key]
        elif (pos.qty > 0) == (new_qty > 0) and abs(new_qty) > abs(pos.qty):
            # Added to an existing position: blend the entry price.
            pos.avg_price = (pos.avg_price * abs(pos.qty) + price * abs(qty)) / abs(new_qty)
            pos.qty = new_qty
        else:
            pos.qty = new_qty

    def settle_option(self, key: str, intrinsic: float) -> None:
        # Cash settlement at intrinsic approximates assignment (see SPEC).
        pos = self.positions.pop(key, None)
        if pos is not None:
            self.cash += intrinsic * pos.qty * OPTION_MULTIPLIER

    def value(self, marks: dict[str, float]) -> float:
        total = self.cash
        for key, pos in self.positions.items():
            mark = marks.get(key)
            if mark is None:
                mark = pos.avg_price  # no quote today: carry at entry, stated not hidden
            mult = OPTION_MULTIPLIER if key.startswith("OPT:") else 1
            total += mark * pos.qty * mult
        return total


def replay(records: list[dict], method_names: list[str]) -> dict[str, Account]:
    accounts = {name: Account() for name in method_names}
    for r in records:
        account = accounts.get(r.get("method", ""))
        if account is None:
            continue
        if r["kind"] == "fill":
            account.apply_fill(r["instrument"], r["qty"], r["price"], r["cash_delta"], r["friction"])
        elif r["kind"] == "settle":
            account.settle_option(r["instrument"], r["intrinsic"])
    return accounts
