"""Core backtesting engine (PROJ-2).

Public entry point: run_backtest(ohlcv, signals, config) -> BacktestResult
"""

from datetime import time
from typing import List, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .models import BacktestConfig, BacktestResult, Trade
from .order_manager import PendingOrder, evaluate_pending_orders
from .pip_utils import pips_to_price_offset
from .position_tracker import (
    OpenPosition,
    apply_trail_if_triggered,
    check_sl_tp,
    close_position,
)
from .sizing import calculate_lot_size


def _parse_time_exit(time_exit_str: Optional[str]) -> Optional[time]:
    if time_exit_str is None:
        return None
    try:
        h, m = time_exit_str.split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid time_exit '{time_exit_str}': must be HH:MM with valid hours (00-23) and minutes (00-59).") from exc


def _extract_pending_orders(sig_row: pd.Series) -> List[PendingOrder]:
    """
    Convert one row of the signals DataFrame into a list of PendingOrder objects.

    Recognised columns (all optional / NaN-able):
        long_entry, long_sl, long_tp
        short_entry, short_sl, short_tp
    """
    orders: List[PendingOrder] = []

    # Parse the optional signal_expiry (pd.Timestamp or NaT)
    raw_expiry = sig_row.get("signal_expiry", pd.NaT)
    expiry: Optional[pd.Timestamp] = None
    if pd.notna(raw_expiry):
        expiry = pd.Timestamp(raw_expiry)

    trail_trigger_raw = sig_row.get("trail_trigger_pips", np.nan)
    trail_lock_raw = sig_row.get("trail_lock_pips", np.nan)
    trail_trigger_pips = float(trail_trigger_raw) if pd.notna(trail_trigger_raw) else None
    trail_lock_pips = float(trail_lock_raw) if pd.notna(trail_lock_raw) else None

    long_entry = sig_row.get("long_entry", np.nan)
    if pd.notna(long_entry):
        long_tp_raw = sig_row.get("long_tp", np.nan)
        orders.append(
            PendingOrder(
                direction="long",
                entry_price=float(long_entry),
                sl_price=float(sig_row.get("long_sl", np.nan)),
                tp_price=float(long_tp_raw) if pd.notna(long_tp_raw) else None,
                expiry=expiry,
                trail_trigger_pips=trail_trigger_pips,
                trail_lock_pips=trail_lock_pips,
            )
        )

    short_entry = sig_row.get("short_entry", np.nan)
    if pd.notna(short_entry):
        short_tp_raw = sig_row.get("short_tp", np.nan)
        orders.append(
            PendingOrder(
                direction="short",
                entry_price=float(short_entry),
                sl_price=float(sig_row.get("short_sl", np.nan)),
                tp_price=float(short_tp_raw) if pd.notna(short_tp_raw) else None,
                expiry=expiry,
                trail_trigger_pips=trail_trigger_pips,
                trail_lock_pips=trail_lock_pips,
            )
        )

    return orders


def run_backtest(
    ohlcv: pd.DataFrame,
    signals: pd.DataFrame,
    config: BacktestConfig,
) -> BacktestResult:
    """
    Simulate a trading strategy bar-by-bar.

    Parameters
    ----------
    ohlcv : DataFrame
        Columns: open, high, low, close, volume.  DatetimeIndex (UTC).
    signals : DataFrame
        Same index as ohlcv.  Columns (all float, NaN = no signal):
            long_entry, long_sl, long_tp
            short_entry, short_sl, short_tp
        A bar with both long_entry and short_entry set forms an OCO pair.
        Signals on bar N become active (i.e. checked for entry) on bar N+1.
    config : BacktestConfig

    Returns
    -------
    BacktestResult

    Simulation rules
    ----------------
    Per bar (in order):
      1. If position open:
         a. Time exit  — close at bar open if bar_time >= exit_time.
         b. Trail trigger — move SL once when peak profit >= trigger threshold.
         c. SL / TP check — if both hit in same bar, SL wins (worst case).
      2. If no position and pending orders exist:
         — Evaluate entry trigger; if fired, open position, cancel OCO partner.
      3. If no position:
         — Record new signal from this bar as pending for the NEXT bar.
    End of data: close any remaining open position at the last bar's close.
    Maximum one open position at a time; new signals while a position is open
    are ignored.
    """
    exit_time = _parse_time_exit(config.time_exit)
    exit_tz = ZoneInfo(config.timezone)
    slippage_offset = pips_to_price_offset(
        config.slippage_pips, config.instrument.pip_size
    )

    if ohlcv.empty:
        return BacktestResult(
            trades=[],
            equity_curve=[],
            final_balance=config.initial_balance,
            initial_balance=config.initial_balance,
        )

    balance: float = config.initial_balance
    trades: List[Trade] = []
    equity_curve = [{"time": ohlcv.index[0].isoformat(), "balance": balance}]
    position: Optional[OpenPosition] = None
    pending_orders: List[PendingOrder] = []

    for i in range(len(ohlcv)):
        bar = ohlcv.iloc[i]
        bar_time = ohlcv.index[i]
        bar_open = float(bar["open"])
        bar_high = float(bar["high"])
        bar_low = float(bar["low"])

        # ── 1a. Time exit ───────────────────────────────────────────────────
        if position is not None and exit_time is not None:
            if bar_time.tz_convert(exit_tz).time() >= exit_time:
                trade = close_position(position, bar_time, bar_open, "TIME", config)
                trades.append(trade)
                balance += trade.pnl_currency
                equity_curve.append(
                    {"time": bar_time.isoformat(), "balance": round(balance, 2)}
                )
                position = None
                pending_orders = []

        # ── 1b & 1c. Trail trigger + SL/TP ─────────────────────────────────
        if position is not None:
            apply_trail_if_triggered(position, bar_high, bar_low, config)

            exit_reason = check_sl_tp(position, bar_high, bar_low)
            if exit_reason is not None:
                if exit_reason in ("SL", "SL_TRAILED"):
                    sl = position.sl_price
                    # Gap fill: if bar opened past SL, use open price (worse fill)
                    if position.direction == "long" and bar_open < sl:
                        exit_price = bar_open
                    elif position.direction == "short" and bar_open > sl:
                        exit_price = bar_open
                    else:
                        exit_price = sl
                else:  # TP
                    tp = position.tp_price
                    # Gap fill: if bar opened past TP, use open price
                    if position.direction == "long" and bar_open > tp:
                        exit_price = bar_open
                    elif position.direction == "short" and bar_open < tp:
                        exit_price = bar_open
                    else:
                        exit_price = tp
                trade = close_position(position, bar_time, exit_price, exit_reason, config)
                trades.append(trade)
                balance += trade.pnl_currency
                equity_curve.append(
                    {"time": bar_time.isoformat(), "balance": round(balance, 2)}
                )
                position = None
                pending_orders = []

        # ── 1d. Expire pending orders past their deadline ─────────────────
        if pending_orders:
            pending_orders = [
                o for o in pending_orders
                if o.expiry is None or bar_time <= o.expiry
            ]

        # ── 2. Check pending orders ─────────────────────────────────────────
        if position is None and pending_orders:
            triggered = evaluate_pending_orders(pending_orders, bar_high, bar_low, bar_open)
            if triggered is not None:
                # Adverse entry slippage
                if triggered.direction == "long":
                    actual_entry = triggered.entry_price + slippage_offset
                else:
                    actual_entry = triggered.entry_price - slippage_offset

                lot_size = calculate_lot_size(
                    config, triggered.entry_price, triggered.sl_price, balance
                )
                position = OpenPosition(
                    direction=triggered.direction,
                    entry_time=bar_time,
                    entry_price=actual_entry,
                    sl_price=triggered.sl_price,
                    tp_price=triggered.tp_price,
                    lot_size=lot_size,
                    initial_sl_price=triggered.sl_price,
                    trail_trigger_pips=triggered.trail_trigger_pips,
                    trail_lock_pips=triggered.trail_lock_pips,
                )
                pending_orders = []  # cancel OCO partner

        # ── 3. New signal for next bar ──────────────────────────────────────
        # Signals are intentionally discarded while a position is open (max 1
        # trade per day for PROJ-3).  Future multi-signal strategies (PROJ-6)
        # may need to queue signals here instead of dropping them.
        if position is None:
            new_orders = _extract_pending_orders(signals.iloc[i])
            if new_orders:
                pending_orders = new_orders

    # ── End of data: close any open position ───────────────────────────────
    if position is not None:
        last_bar = ohlcv.iloc[-1]
        last_time = ohlcv.index[-1]
        trade = close_position(
            position, last_time, float(last_bar["close"]), "TIME", config
        )
        trades.append(trade)
        balance += trade.pnl_currency
        equity_curve.append(
            {"time": last_time.isoformat(), "balance": round(balance, 2)}
        )

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        final_balance=round(balance, 2),
        initial_balance=config.initial_balance,
    )
