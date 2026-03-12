"""Trade-level metrics: win rate, profit factor, R-multiples, streaks, avg duration (PROJ-4).

All functions accept a list of Trade dataclass instances from engine.models.
"""

from datetime import timedelta
from typing import List, Optional, Tuple

from engine.models import Trade


# ---------------------------------------------------------------------------
# Core counts
# ---------------------------------------------------------------------------

def total_trades(trades: List[Trade]) -> int:
    """Count of all closed trades."""
    return len(trades)


def winning_trades(trades: List[Trade]) -> List[Trade]:
    """Trades with PnL > 0 (currency)."""
    return [t for t in trades if t.pnl_currency > 0]


def losing_trades(trades: List[Trade]) -> List[Trade]:
    """Trades with PnL <= 0 (currency)."""
    return [t for t in trades if t.pnl_currency <= 0]


# ---------------------------------------------------------------------------
# Win / loss aggregates (currency-denominated)
# ---------------------------------------------------------------------------

def win_rate(trades: List[Trade]) -> Optional[float]:
    """Win Rate = Winning Trades / Total Trades * 100%.

    Returns None if total_trades == 0.
    """
    n = total_trades(trades)
    if n == 0:
        return None
    return len(winning_trades(trades)) / n * 100.0


def gross_profit_currency(trades: List[Trade]) -> float:
    """Sum of pnl_currency for winning trades."""
    return sum(t.pnl_currency for t in winning_trades(trades))


def gross_loss_currency(trades: List[Trade]) -> float:
    """Sum of |pnl_currency| for losing trades (returned as positive)."""
    return abs(sum(t.pnl_currency for t in losing_trades(trades)))


def profit_factor_currency(trades: List[Trade]) -> Optional[float]:
    """Profit Factor = Gross Profit / Gross Loss.

    Returns float('inf') when gross_loss == 0 and gross_profit > 0.
    Returns None when no trades.
    Returns 0.0 when gross_profit == 0.
    """
    n = total_trades(trades)
    if n == 0:
        return None
    gp = gross_profit_currency(trades)
    gl = gross_loss_currency(trades)
    if gl == 0:
        return float("inf") if gp > 0 else 1.0  # all-breakeven → neutral
    return gp / gl


def avg_win_currency(trades: List[Trade]) -> Optional[float]:
    """Average Win = Gross Profit / Winning Trades (currency)."""
    w = winning_trades(trades)
    if not w:
        return None
    return gross_profit_currency(trades) / len(w)


def avg_loss_currency(trades: List[Trade]) -> Optional[float]:
    """Average Loss = Gross Loss / Losing Trades (currency, positive value)."""
    lo = losing_trades(trades)
    if not lo:
        return None
    return gross_loss_currency(trades) / len(lo)


def avg_win_loss_ratio_currency(trades: List[Trade]) -> Optional[float]:
    """Avg Win / Avg Loss (currency). Returns None if either side is missing."""
    aw = avg_win_currency(trades)
    al = avg_loss_currency(trades)
    if aw is None or al is None or al == 0:
        return None
    return aw / al


def avg_win_loss_ratio_pips(trades: List[Trade]) -> Optional[float]:
    """Avg Win / Avg Loss (pips). Returns None if either side is missing."""
    aw = avg_win_pips(trades)
    al = avg_loss_pips(trades)
    if aw is None or al is None or al == 0:
        return None
    return aw / al


# ---------------------------------------------------------------------------
# Win / loss aggregates (pip-denominated)
# ---------------------------------------------------------------------------

def gross_profit_pips(trades: List[Trade]) -> float:
    """Sum of pnl_pips for winning trades (using currency to identify winners)."""
    return sum(t.pnl_pips for t in winning_trades(trades))


def gross_loss_pips(trades: List[Trade]) -> float:
    """Sum of |pnl_pips| for losing trades (returned as positive)."""
    return abs(sum(t.pnl_pips for t in losing_trades(trades)))


def profit_factor_pips(trades: List[Trade]) -> Optional[float]:
    """Profit Factor in pips = Gross Profit (pips) / Gross Loss (pips)."""
    n = total_trades(trades)
    if n == 0:
        return None
    gp = gross_profit_pips(trades)
    gl = gross_loss_pips(trades)
    if gl == 0:
        return float("inf") if gp > 0 else 1.0  # all-breakeven → neutral
    return gp / gl


def avg_win_pips(trades: List[Trade]) -> Optional[float]:
    """Average Win in pips."""
    w = winning_trades(trades)
    if not w:
        return None
    return gross_profit_pips(trades) / len(w)


def avg_loss_pips(trades: List[Trade]) -> Optional[float]:
    """Average Loss in pips (positive value)."""
    lo = losing_trades(trades)
    if not lo:
        return None
    return gross_loss_pips(trades) / len(lo)


# ---------------------------------------------------------------------------
# Best / worst trade
# ---------------------------------------------------------------------------

def best_trade_currency(trades: List[Trade]) -> Optional[float]:
    """Highest single trade PnL (currency)."""
    if not trades:
        return None
    return max(t.pnl_currency for t in trades)


def worst_trade_currency(trades: List[Trade]) -> Optional[float]:
    """Lowest single trade PnL (currency)."""
    if not trades:
        return None
    return min(t.pnl_currency for t in trades)


def best_trade_pips(trades: List[Trade]) -> Optional[float]:
    """Highest single trade PnL (pips)."""
    if not trades:
        return None
    return max(t.pnl_pips for t in trades)


def worst_trade_pips(trades: List[Trade]) -> Optional[float]:
    """Lowest single trade PnL (pips)."""
    if not trades:
        return None
    return min(t.pnl_pips for t in trades)


# ---------------------------------------------------------------------------
# Consecutive streaks
# ---------------------------------------------------------------------------

def consecutive_streaks(trades: List[Trade]) -> Tuple[int, int]:
    """Return (longest_win_streak, longest_loss_streak)."""
    if not trades:
        return (0, 0)

    max_wins = 0
    max_losses = 0
    cur_wins = 0
    cur_losses = 0

    for t in trades:
        if t.pnl_currency > 0:
            cur_wins += 1
            cur_losses = 0
        else:
            cur_losses += 1
            cur_wins = 0
        max_wins = max(max_wins, cur_wins)
        max_losses = max(max_losses, cur_losses)

    return (max_wins, max_losses)


# ---------------------------------------------------------------------------
# Average trade duration
# ---------------------------------------------------------------------------

def avg_trade_duration_hours(trades: List[Trade]) -> Optional[float]:
    """Mean time between entry_time and exit_time across all trades, in hours."""
    if not trades:
        return None
    total_seconds = sum(
        (t.exit_time - t.entry_time).total_seconds() for t in trades
    )
    return total_seconds / len(trades) / 3600.0


# ---------------------------------------------------------------------------
# R-Multiple calculations
# R-Multiple = pnl_currency / initial_risk_currency
# ---------------------------------------------------------------------------

def r_multiple(trade: Trade) -> Optional[float]:
    """R-Multiple for a single trade.

    Returns None if initial_risk_currency == 0 (SL at entry).
    """
    if trade.initial_risk_currency == 0:
        return None
    return trade.pnl_currency / trade.initial_risk_currency


def r_multiples(trades: List[Trade]) -> List[Optional[float]]:
    """R-Multiples for all trades."""
    return [r_multiple(t) for t in trades]


def _valid_r_multiples(trades: List[Trade]) -> List[float]:
    """R-Multiples excluding None entries (trades with zero risk)."""
    return [r for r in r_multiples(trades) if r is not None]


def total_r(trades: List[Trade]) -> Optional[float]:
    """Sum of all valid R-Multiples."""
    valid = _valid_r_multiples(trades)
    if not valid:
        return None
    return sum(valid)


def avg_r_per_trade(trades: List[Trade]) -> Optional[float]:
    """Total R / Total Trades (including trades with zero risk counted as 0 R)."""
    if not trades:
        return None
    valid = _valid_r_multiples(trades)
    return sum(valid) / len(trades)


def expectancy_currency(trades: List[Trade]) -> Optional[float]:
    """Expectancy = (Win Rate * Avg Win) - (Loss Rate * Avg Loss) in currency.

    Returns None if no trades.
    """
    n = total_trades(trades)
    if n == 0:
        return None
    wr = len(winning_trades(trades)) / n
    lr = len(losing_trades(trades)) / n
    aw = avg_win_currency(trades) or 0.0
    al = avg_loss_currency(trades) or 0.0
    return wr * aw - lr * al


def expectancy_pips(trades: List[Trade]) -> Optional[float]:
    """Expectancy in pips = (Win Rate * Avg Win Pips) - (Loss Rate * Avg Loss Pips)."""
    n = total_trades(trades)
    if n == 0:
        return None
    wr = len(winning_trades(trades)) / n
    lr = len(losing_trades(trades)) / n
    aw = avg_win_pips(trades) or 0.0
    al = avg_loss_pips(trades) or 0.0
    return wr * aw - lr * al
