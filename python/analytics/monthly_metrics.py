"""Monthly R-Multiple breakdown (PROJ-4).

Groups trades by the calendar month of their exit_time and sums R-Multiples.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from engine.models import Trade
from .trade_metrics import r_multiple
from .models import MonthlyR


def monthly_r_breakdown(trades: List[Trade]) -> List[MonthlyR]:
    """Compute R earned per calendar month.

    Groups by exit_time month. Trades with initial_risk_currency == 0
    (R-Multiple = None) are included in trade_count but excluded from r_earned.

    Returns a list sorted by month ascending.
    """
    if not trades:
        return []

    # month_key -> (sum_r, count, has_valid_r)
    monthly: Dict[str, Tuple[float, int]] = defaultdict(lambda: [0.0, 0])

    for t in trades:
        month_key = t.exit_time.strftime("%Y-%m")
        r = r_multiple(t)
        monthly[month_key][1] += 1
        if r is not None:
            monthly[month_key][0] += r

    result = []
    for month_key in sorted(monthly.keys()):
        r_sum, count = monthly[month_key]
        result.append(
            MonthlyR(
                month=month_key,
                r_earned=round(r_sum, 4),
                trade_count=count,
            )
        )

    return result


def avg_r_per_month(trades: List[Trade], monthly: List[MonthlyR]) -> Optional[float]:
    """Average R per Month = Total R / Number of calendar months.

    Returns None if no valid R-multiples exist or no months.
    """
    if not monthly:
        return None

    from .trade_metrics import total_r as calc_total_r
    tr = calc_total_r(trades)
    if tr is None:
        return None

    return tr / len(monthly)
