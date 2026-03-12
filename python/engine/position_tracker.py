"""Open-position management: trail trigger, SL/TP evaluation, position close."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional

from .models import BacktestConfig, Trade
from .pip_utils import pips_to_price_offset, price_diff_to_pips, pip_value_for_lot


@dataclass
class OpenPosition:
    """State of the currently open trade."""

    direction: Literal["long", "short"]
    entry_time: datetime
    entry_price: float
    sl_price: float
    tp_price: Optional[float]
    lot_size: float
    initial_sl_price: float  # frozen at entry; used for initial-risk reporting
    trail_applied: bool = False
    trail_trigger_pips: Optional[float] = None  # per-signal override; falls back to BacktestConfig
    trail_lock_pips: Optional[float] = None     # per-signal override; falls back to BacktestConfig


def apply_trail_if_triggered(
    position: OpenPosition,
    bar_high: float,
    bar_low: float,
    config: BacktestConfig,
) -> None:
    """
    Move the stop loss to trail_lock_pips from entry when unrealised profit
    reaches trail_trigger_pips.  Fires at most once per trade (in-place).

    Uses the bar's favourable extreme (high for long, low for short) to
    measure peak unrealised profit within the bar.
    """
    trigger_pips = (
        position.trail_trigger_pips
        if position.trail_trigger_pips is not None
        else config.trail_trigger_pips
    )
    if trigger_pips is None or position.trail_applied:
        return

    pip_size = config.instrument.pip_size

    if position.direction == "long":
        profit_pips = (bar_high - position.entry_price) / pip_size
    else:
        profit_pips = (position.entry_price - bar_low) / pip_size

    if profit_pips >= trigger_pips:
        lock_pips = (
            position.trail_lock_pips
            if position.trail_lock_pips is not None
            else (config.trail_lock_pips if config.trail_lock_pips is not None else 0.0)
        )
        offset = pips_to_price_offset(lock_pips, pip_size)
        if position.direction == "long":
            position.sl_price = position.entry_price + offset
        else:
            position.sl_price = position.entry_price - offset
        position.trail_applied = True


def check_sl_tp(
    position: OpenPosition,
    bar_high: float,
    bar_low: float,
) -> Optional[Literal["SL", "SL_TRAILED", "TP"]]:
    """
    Check whether SL or TP was hit in this bar.

    If both are hit in the same bar, SL wins (worst-case assumption).
    Returns the exit reason, or None if neither level was reached.
    """
    sl_hit = (
        (position.direction == "long"  and bar_low  <= position.sl_price)
        or (position.direction == "short" and bar_high >= position.sl_price)
    )
    tp_hit = (
        position.tp_price is not None
        and (
            (position.direction == "long"  and bar_high >= position.tp_price)
            or (position.direction == "short" and bar_low  <= position.tp_price)
        )
    )

    if sl_hit:
        return "SL_TRAILED" if position.trail_applied else "SL"
    if tp_hit:
        return "TP"
    return None


def close_position(
    position: OpenPosition,
    exit_time: datetime,
    exit_price: float,
    exit_reason: Literal["SL", "SL_TRAILED", "TP", "TIME"],
    config: BacktestConfig,
) -> Trade:
    """
    Close a position and return the completed Trade record.

    Applies adverse slippage to the exit price and deducts commission.
    """
    pip_size = config.instrument.pip_size
    pip_value_per_lot = config.instrument.pip_value_per_lot
    slippage_offset = pips_to_price_offset(config.slippage_pips, pip_size)

    # Adverse slippage: we receive a worse price than the order level
    if position.direction == "long":
        actual_exit = exit_price - slippage_offset
        pnl_pips = (actual_exit - position.entry_price) / pip_size
    else:
        actual_exit = exit_price + slippage_offset
        pnl_pips = (position.entry_price - actual_exit) / pip_size

    pnl_currency = (
        pnl_pips * pip_value_for_lot(position.lot_size, pip_value_per_lot)
        - config.commission
    )

    initial_risk_pips = price_diff_to_pips(
        position.entry_price - position.initial_sl_price, pip_size
    )
    initial_risk_currency = initial_risk_pips * pip_value_for_lot(
        position.lot_size, pip_value_per_lot
    )

    return Trade(
        entry_time=position.entry_time,
        entry_price=position.entry_price,
        exit_time=exit_time,
        exit_price=round(actual_exit, 5),
        exit_reason=exit_reason,
        direction=position.direction,
        lot_size=position.lot_size,
        pnl_pips=round(pnl_pips, 1),
        pnl_currency=round(pnl_currency, 2),
        initial_risk_pips=round(initial_risk_pips, 1),
        initial_risk_currency=round(initial_risk_currency, 2),
    )
