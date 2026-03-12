"""Risk-adjusted metrics: Sharpe Ratio and Sortino Ratio (PROJ-4).

Computed from daily returns derived from the equity curve.

Formulas (risk-free rate = 0):
    Sharpe  = mean(daily_returns) / std(daily_returns) * sqrt(252)
    Sortino = mean(daily_returns) / downside_deviation * sqrt(252)

Downside deviation = std of daily returns where return < 0.
"""

from datetime import datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd


def _daily_returns(equity_curve: List[Dict]) -> Optional[np.ndarray]:
    """Convert equity curve to daily returns.

    Groups by calendar date (using last balance of each day),
    then computes percentage returns.

    Returns None if fewer than 2 distinct days.
    """
    if len(equity_curve) < 2:
        return None

    # Build a Series: date -> last balance of that date
    dates = []
    balances = []
    for point in equity_curve:
        dt = datetime.fromisoformat(point["time"])
        dates.append(dt.date())
        balances.append(point["balance"])

    series = pd.Series(balances, index=pd.DatetimeIndex(dates))
    # Keep last value per day
    daily_balance = series.groupby(series.index).last()

    if len(daily_balance) < 2:
        return None

    # Percentage returns
    returns = daily_balance.pct_change().dropna().values
    return returns


def sharpe_ratio(equity_curve: List[Dict]) -> Optional[float]:
    """Annualised Sharpe Ratio (risk-free rate = 0).

    Sharpe = mean(daily_returns) / std(daily_returns) * sqrt(252)

    Returns None if:
      - Fewer than 2 daily data points
      - Standard deviation is 0 (all returns identical)
    """
    returns = _daily_returns(equity_curve)
    if returns is None or len(returns) < 2:
        return None

    std = np.std(returns, ddof=1)
    if std == 0:
        return None

    return float(np.mean(returns) / std * np.sqrt(252))


def sortino_ratio(equity_curve: List[Dict]) -> Optional[float]:
    """Annualised Sortino Ratio (risk-free rate = 0).

    Sortino = mean(daily_returns) / downside_deviation * sqrt(252)
    Downside deviation = sqrt(mean(min(return, 0)^2))

    Returns None if:
      - Fewer than 2 daily data points
      - Downside deviation is 0 (no negative returns)
    """
    returns = _daily_returns(equity_curve)
    if returns is None or len(returns) < 2:
        return None

    downside = returns[returns < 0]
    if len(downside) == 0:
        return None  # No negative returns — Sortino undefined

    # Downside deviation: sqrt(mean(negative_returns^2))
    downside_dev = np.sqrt(np.mean(downside ** 2))
    if downside_dev == 0:
        return None

    return float(np.mean(returns) / downside_dev * np.sqrt(252))
