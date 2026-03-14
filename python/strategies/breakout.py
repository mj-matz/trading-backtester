"""Time-Range Breakout Strategy (PROJ-3).

Computes a consolidation range from bars within [range_start, range_end) each
trading day, then emits OCO stop-entry signals on the first bar after
range_end.  The engine manages pending orders, OCO cancellation, and expiry.
"""

from dataclasses import dataclass
from datetime import time
from typing import Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pandas as pd

from .base import BaseStrategy


@dataclass
class SkippedDay:
    """Represents a trading day that was skipped during signal generation."""

    date: str
    reason: str  # NO_BARS | NO_RANGE_BARS | FLAT_RANGE | NO_SIGNAL_BAR | DEADLINE_MISSED


@dataclass
class BreakoutParams:
    """All user-configurable parameters for the breakout strategy."""

    asset: str                 # instrument identifier, e.g. "XAUUSD", "GER40"
    range_start: time          # e.g. time(3, 0) for 03:00
    range_end: time            # e.g. time(8, 0) for 08:00
    trigger_deadline: time     # e.g. time(17, 0) for 17:00
    stop_loss_pips: float      # fixed pip offset from entry price for SL
    take_profit_pips: float    # fixed pip offset from entry price for TP
    pip_size: float            # instrument pip size from engine config
    timezone: str = "UTC"      # IANA timezone, e.g. "Europe/Berlin" for CET
    direction_filter: Literal["long_only", "short_only", "both"] = "both"
    trail_trigger_pips: Optional[float] = None  # profit level (pips) that activates the lock
    trail_lock_pips: Optional[float] = None     # pips from entry to which SL is moved on trigger
    entry_offset_pips: float = 1.0


class BreakoutStrategy(BaseStrategy):
    """Time-Range Breakout: buy stop above / sell stop below the range."""

    def validate_params(self, params: BreakoutParams) -> None:
        """Validate breakout parameters. Raises ValueError on invalid input."""
        if params.range_end == params.range_start:
            raise ValueError(
                f"range_end ({params.range_end}) must differ from "
                f"range_start ({params.range_start}); zero-width ranges are not allowed"
            )
        # Compute effective range duration in minutes.
        # For overnight ranges (range_start > range_end) the window wraps midnight,
        # e.g. 22:00–02:00 = 4 h. A "range" of 10:00–08:00 would be 22 h — not a
        # legitimate overnight window, so we cap at MAX_RANGE_MINUTES (12 h).
        start_min = params.range_start.hour * 60 + params.range_start.minute
        end_min = params.range_end.hour * 60 + params.range_end.minute
        duration_min = end_min - start_min if end_min > start_min else end_min - start_min + 24 * 60
        MAX_RANGE_MINUTES = 12 * 60  # 12 hours
        if duration_min > MAX_RANGE_MINUTES:
            raise ValueError(
                f"range_end ({params.range_end}) results in a range duration of "
                f"{duration_min // 60}h {duration_min % 60}m, which exceeds the "
                f"maximum of {MAX_RANGE_MINUTES // 60}h. "
                f"For overnight ranges use e.g. range_start=22:00, range_end=02:00."
            )
        # For overnight ranges (range_start > range_end) trigger_deadline is on the next
        # calendar day, so the simple time comparison still works (deadline must follow
        # range_end on that next day, e.g. range_end=02:00, deadline=04:00).
        if params.trigger_deadline <= params.range_end:
            raise ValueError(
                f"trigger_deadline ({params.trigger_deadline}) must be after "
                f"range_end ({params.range_end})"
            )
        if params.stop_loss_pips <= 0:
            raise ValueError(
                f"stop_loss_pips must be > 0, got {params.stop_loss_pips}"
            )
        if params.take_profit_pips <= 0:
            raise ValueError(
                f"take_profit_pips must be > 0, got {params.take_profit_pips}"
            )
        if params.entry_offset_pips < 0:
            raise ValueError(
                f"entry_offset_pips must be >= 0, got {params.entry_offset_pips}"
            )
        if params.trail_trigger_pips is not None or params.trail_lock_pips is not None:
            if params.trail_trigger_pips is None or params.trail_lock_pips is None:
                raise ValueError(
                    "Both trail_trigger_pips and trail_lock_pips must be set together"
                )
            if params.trail_lock_pips <= 0:
                raise ValueError(
                    f"trail_lock_pips must be > 0, got {params.trail_lock_pips}"
                )
            if params.trail_trigger_pips <= params.trail_lock_pips:
                raise ValueError(
                    f"trail_trigger_pips ({params.trail_trigger_pips}) must be > "
                    f"trail_lock_pips ({params.trail_lock_pips})"
                )
            if params.trail_trigger_pips >= params.take_profit_pips:
                raise ValueError(
                    f"trail_trigger_pips ({params.trail_trigger_pips}) must be < "
                    f"take_profit_pips ({params.take_profit_pips})"
                )
        if not params.asset or not params.asset.strip():
            raise ValueError("asset must not be empty")
        try:
            ZoneInfo(params.timezone)
        except (ZoneInfoNotFoundError, KeyError):
            raise ValueError(f"Unknown timezone: '{params.timezone}'")
        if params.pip_size <= 0:
            raise ValueError(f"pip_size must be > 0, got {params.pip_size}")

    def generate_signals(
        self, df: pd.DataFrame, params: BreakoutParams
    ) -> tuple[pd.DataFrame, list[SkippedDay]]:
        """
        Generate breakout signals from OHLCV data.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data with UTC DatetimeIndex. Must have columns:
            open, high, low, close.
        params : BreakoutParams

        Returns
        -------
        tuple[pd.DataFrame, list[SkippedDay]]
            - DataFrame: Same index as df. Signal columns are NaN / NaT except on
              the first bar after range_end for each valid trading day.
            - list[SkippedDay]: Days that were skipped with reason codes.
        """
        self.validate_params(params)

        # Prepare output DataFrame
        sig_cols = [
            "long_entry", "long_sl", "long_tp",
            "short_entry", "short_sl", "short_tp",
            "trail_trigger_pips", "trail_lock_pips",
        ]
        signals = pd.DataFrame(np.nan, index=df.index, columns=sig_cols, dtype=float)
        signals["signal_expiry"] = pd.Series(
            pd.NaT, index=df.index, dtype="datetime64[ns, UTC]"
        )

        skipped_days: list[SkippedDay] = []

        if df.empty:
            return (signals, skipped_days)

        tz = ZoneInfo(params.timezone)

        # Convert index to the instrument timezone for grouping by trading day
        local_times = df.index.tz_convert(tz)

        # Group bars by calendar date in the instrument timezone
        dates = local_times.date
        unique_dates = sorted(set(dates))

        is_overnight = params.range_start > params.range_end
        from datetime import datetime as dt

        for day_idx, day in enumerate(unique_dates):
            day_mask = dates == day
            day_local = local_times[day_mask]
            day_bar_times = day_local.time
            day_indices = df.index[day_mask]

            if is_overnight:
                # Overnight range: range_start on `day`, range_end on the next calendar day.
                # Need a next day to complete the range.
                if day_idx + 1 >= len(unique_dates):
                    skipped_days.append(SkippedDay(date=str(day), reason="NO_BARS"))
                    continue
                next_day = unique_dates[day_idx + 1]
                next_mask = dates == next_day
                next_local = local_times[next_mask]
                next_bar_times = next_local.time
                next_indices = df.index[next_mask]

                # Range bars: today >= range_start, next day < range_end
                today_range_indices = day_indices[day_bar_times >= params.range_start]
                next_range_indices = next_indices[next_bar_times < params.range_end]
                range_indices = today_range_indices.union(next_range_indices).sort_values()

                # Signal bar: first bar on next_day at or after range_end
                after_range_indices = next_indices[next_bar_times >= params.range_end]

                # Expiry is trigger_deadline on the next calendar day
                expiry_naive = dt.combine(next_day, params.trigger_deadline)
            else:
                # Normal intraday range
                range_mask_within_day = (
                    (day_bar_times >= params.range_start)
                    & (day_bar_times < params.range_end)
                )
                range_indices = day_indices[range_mask_within_day]

                # Signal bar: first bar on same day at or after range_end
                after_range_indices = day_indices[day_bar_times >= params.range_end]

                # Expiry is trigger_deadline on this calendar day
                expiry_naive = dt.combine(day, params.trigger_deadline)

            # -- Step 1: Validate range
            if len(range_indices) == 0:
                skipped_days.append(SkippedDay(date=str(day), reason="NO_RANGE_BARS"))
                continue

            range_bars = df.loc[range_indices]
            range_high = float(range_bars["high"].max())
            range_low = float(range_bars["low"].min())

            # Skip flat ranges (High == Low)
            if range_high == range_low:
                skipped_days.append(SkippedDay(date=str(day), reason="FLAT_RANGE"))
                continue

            # -- Step 2: Find first bar after range_end
            if len(after_range_indices) == 0:
                skipped_days.append(SkippedDay(date=str(day), reason="NO_SIGNAL_BAR"))
                continue

            signal_bar_idx = after_range_indices[0]

            # Check that this bar is within the trigger window
            signal_bar_local_time = pd.Timestamp(signal_bar_idx).tz_convert(tz).time()
            if signal_bar_local_time > params.trigger_deadline:
                skipped_days.append(SkippedDay(date=str(day), reason="DEADLINE_MISSED"))
                continue

            # -- Step 3: Calculate entry, SL, TP prices
            entry_offset = params.entry_offset_pips * params.pip_size
            sl_offset = params.stop_loss_pips * params.pip_size
            tp_offset = params.take_profit_pips * params.pip_size

            long_entry = range_high + entry_offset
            short_entry = range_low - entry_offset

            long_sl = long_entry - sl_offset
            short_sl = short_entry + sl_offset

            long_tp = long_entry + tp_offset
            short_tp = short_entry - tp_offset

            # -- Step 4: Build expiry timestamp (local tz → UTC)
            expiry_local = pd.Timestamp(expiry_naive, tz=tz)
            expiry_utc = expiry_local.tz_convert("UTC")

            # -- Step 5: Apply direction filter and write signals
            if params.direction_filter != "short_only":
                signals.at[signal_bar_idx, "long_entry"] = long_entry
                signals.at[signal_bar_idx, "long_sl"] = long_sl
                signals.at[signal_bar_idx, "long_tp"] = long_tp

            if params.direction_filter != "long_only":
                signals.at[signal_bar_idx, "short_entry"] = short_entry
                signals.at[signal_bar_idx, "short_sl"] = short_sl
                signals.at[signal_bar_idx, "short_tp"] = short_tp

            signals.at[signal_bar_idx, "signal_expiry"] = expiry_utc

            if params.trail_trigger_pips is not None:
                signals.at[signal_bar_idx, "trail_trigger_pips"] = params.trail_trigger_pips
                signals.at[signal_bar_idx, "trail_lock_pips"] = params.trail_lock_pips

        return signals, skipped_days
