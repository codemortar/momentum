from .base import Context, Method
from .simple import METHODS as _SIMPLE
from .theta_puts import METHOD as _THETA
from .theta_spread import METHOD as _SPREAD

REGISTRY: dict[str, Method] = {m.name: m for m in [_THETA, _SPREAD, *_SIMPLE]}

__all__ = ["Context", "Method", "REGISTRY"]
