"""Finds the highest-value redemption path for credit-card points.

Deterministic graph search over a versioned rewards world returns ranked,
step-by-step redemption plans with honest math — including when the right
answer is "pay cash and keep your points".
"""

from .models import DISCLAIMER

__version__ = "0.1.0"

__all__ = ["DISCLAIMER", "__version__"]
