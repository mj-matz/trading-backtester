"""Data models for analytics output (PROJ-4)."""

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class Metric:
    """A single named metric with its value and unit.

    Attributes:
        name:  Human-readable metric name (e.g. "Win Rate").
        value: Numeric value, None for undefined, or float('inf') for infinity.
        unit:  One of: "%", "currency", "pips", "R", "count", "days", "ratio", "hours".
        note:  Optional explanatory note (e.g. when value is null due to insufficient data).
    """

    name: str
    value: Optional[Any]  # float | int | None | inf
    unit: str
    note: Optional[str] = field(default=None)


@dataclass
class MonthlyR:
    """R earned in a single calendar month.

    Attributes:
        month:       "YYYY-MM" string.
        r_earned:    Sum of R-multiples for trades exiting in this month.
        trade_count: Number of trades exiting in this month.
    """

    month: str
    r_earned: Optional[float]
    trade_count: int


@dataclass
class AnalyticsResult:
    """Complete analytics output returned to the API layer.

    Attributes:
        summary:   List of Metric objects (23+ metrics).
        monthly_r: List of MonthlyR rows, one per calendar month.
    """

    summary: List[Metric]
    monthly_r: List[MonthlyR]
