"""Trading strategies for the backtesting engine (PROJ-3+)."""

from .base import BaseStrategy
from .breakout import BreakoutStrategy, BreakoutParams

__all__ = ["BaseStrategy", "BreakoutStrategy", "BreakoutParams"]
