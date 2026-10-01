"""Method interface: a pure decision function per prediction method."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from ..broker import Order
from ..data import Market
from ..portfolio import Account


@dataclass
class Context:
    market: Market
    account: Account
    name: str


class Decide(Protocol):
    def __call__(self, ctx: Context) -> list[Order]: ...


@dataclass(frozen=True)
class Method:
    name: str
    rule: str        # the pre-registered rule, printed in reports
    decide: Callable[[Context], list[Order]]
