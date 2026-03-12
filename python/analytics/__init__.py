"""Performance Analytics module (PROJ-4).

Computes structured metrics from backtesting engine output.
"""

from .calculator import calculate_analytics
from .models import AnalyticsResult, Metric, MonthlyR

__all__ = ["calculate_analytics", "AnalyticsResult", "Metric", "MonthlyR"]
