from .base import Context, Method
from .simple import METHODS as _SIMPLE
from .theta_puts import METHOD as _THETA

REGISTRY: dict[str, Method] = {m.name: m for m in [_THETA, *_SIMPLE]}

__all__ = ["Context", "Method", "REGISTRY"]
