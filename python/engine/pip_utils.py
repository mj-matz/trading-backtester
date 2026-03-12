"""Pip / point value helpers for the backtesting engine."""


def price_diff_to_pips(price_diff: float, pip_size: float) -> float:
    """Convert an absolute price difference to pips."""
    return abs(price_diff) / pip_size


def pips_to_price_offset(pips: float, pip_size: float) -> float:
    """Convert a pip count to a price offset."""
    return pips * pip_size


def pip_value_for_lot(lot_size: float, pip_value_per_lot: float) -> float:
    """Monetary value of 1 pip for a given lot size."""
    return lot_size * pip_value_per_lot
