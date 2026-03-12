"""
Pytest tests for the Time-Range Breakout Strategy (PROJ-3).

All tests use synthetic OHLCV DataFrames with a UTC DatetimeIndex.
The strategy is tested in isolation from the backtesting engine.

Instrument assumptions for most tests:
  pip_size = 0.01 (like XAUUSD in a simplified model)
"""

import sys
import os

# Allow importing packages from python/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import time

import numpy as np
import pandas as pd
import pytest

from strategies.breakout import BreakoutStrategy, BreakoutParams


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_ohlcv(rows: list) -> pd.DataFrame:
    """
    rows: list of (iso_ts, open, high, low, close)
    Returns a DataFrame with a UTC DatetimeIndex and float OHLCV columns.
    """
    idx = pd.to_datetime([r[0] for r in rows], utc=True)
    return pd.DataFrame(
        {
            "open":   [r[1] for r in rows],
            "high":   [r[2] for r in rows],
            "low":    [r[3] for r in rows],
            "close":  [r[4] for r in rows],
            "volume": [1000] * len(rows),
        },
        index=idx,
    )


def default_params(**overrides) -> BreakoutParams:
    """Build BreakoutParams with sensible defaults, overridable via kwargs."""
    defaults = dict(
        asset="XAUUSD",
        range_start=time(8, 0),
        range_end=time(10, 0),
        trigger_deadline=time(17, 0),
        stop_loss_pips=50.0,
        take_profit_pips=100.0,
        pip_size=0.01,
        timezone="UTC",
        direction_filter="both",
        trail_trigger_pips=None,
        trail_lock_pips=None,
        entry_offset_pips=1.0,
    )
    defaults.update(overrides)
    return BreakoutParams(**defaults)


strategy = BreakoutStrategy()


# ── Test: Range Extraction ────────────────────────────────────────────────────

class TestRangeExtraction:
    def test_range_extraction(self):
        """Verify that Range High and Range Low are correctly computed from
        bars within [range_start, range_end)."""
        ohlcv = make_ohlcv([
            # Range bars: 08:00 - 09:59 UTC
            ("2024-01-02T08:00:00Z", 100.00, 100.50, 99.50,  100.20),
            ("2024-01-02T08:30:00Z", 100.20, 101.00, 100.00, 100.80),
            ("2024-01-02T09:00:00Z", 100.80, 100.90, 99.80,  100.10),
            ("2024-01-02T09:30:00Z", 100.10, 100.60, 99.70,  100.30),
            # First bar at/after range_end (signal bar)
            ("2024-01-02T10:00:00Z", 100.30, 100.40, 100.20, 100.35),
            # Post-signal bars
            ("2024-01-02T11:00:00Z", 100.35, 100.50, 100.30, 100.40),
        ])

        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        # Range High = 101.00 (bar at 08:30), Range Low = 99.50 (bar at 08:00)
        # range_height = 101.00 - 99.50 = 1.50
        # long_entry  = 101.00 + 1*0.01 = 101.01
        # short_entry = 99.50  - 1*0.01 = 99.49
        signal_bar = signals.loc["2024-01-02T10:00:00Z"]
        assert signal_bar["long_entry"] == pytest.approx(101.01, abs=1e-6)
        assert signal_bar["short_entry"] == pytest.approx(99.49, abs=1e-6)

    def test_single_bar_range(self):
        """A range with only 1 bar is still valid."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 100.50, 99.50, 100.20),
            ("2024-01-02T10:00:00Z", 100.20, 100.30, 100.10, 100.25),
        ])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        signal_bar = signals.loc["2024-01-02T10:00:00Z"]
        # Range High = 100.50, Range Low = 99.50
        assert signal_bar["long_entry"] == pytest.approx(100.51, abs=1e-6)
        assert signal_bar["short_entry"] == pytest.approx(99.49, abs=1e-6)

    def test_flat_range_skipped(self):
        """When Range High == Range Low, the day is skipped (no signals)."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 100.00, 100.00, 100.00),
            ("2024-01-02T10:00:00Z", 100.00, 100.10, 99.90, 100.00),
        ])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        signal_bar = signals.loc["2024-01-02T10:00:00Z"]
        assert np.isnan(signal_bar["long_entry"])
        assert np.isnan(signal_bar["short_entry"])

    def test_no_bars_in_range_skipped(self):
        """When no bars exist in the range window, the day is skipped."""
        ohlcv = make_ohlcv([
            # Only bars before range_start and after range_end
            ("2024-01-02T07:00:00Z", 100.00, 100.50, 99.50, 100.20),
            ("2024-01-02T10:00:00Z", 100.20, 100.30, 100.10, 100.25),
        ])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        # No range bars exist -> no signal
        signal_bar = signals.loc["2024-01-02T10:00:00Z"]
        assert np.isnan(signal_bar["long_entry"])
        assert np.isnan(signal_bar["short_entry"])


# ── Test: Long Signal Calculation ─────────────────────────────────────────────

class TestLongSignalCalculation:
    def test_long_signal_calculation(self):
        """Verify entry, SL, and TP for a long signal with fixed-pip offsets."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T09:00:00Z", 100.50, 101.50, 99.00, 100.80),
            ("2024-01-02T10:00:00Z", 100.80, 101.00, 100.50, 100.70),
        ])
        params = default_params(
            stop_loss_pips=50.0,
            take_profit_pips=75.0,
            entry_offset_pips=2.0,
        )
        signals = strategy.generate_signals(ohlcv, params)

        # Range High = 102.00, Range Low = 98.00
        # Long entry = 102.00 + 2.0*0.01 = 102.02
        # Long SL    = 102.02 - 50.0*0.01 = 101.52  (fixed offset from entry)
        # Long TP    = 102.02 + 75.0*0.01 = 102.77  (fixed offset from entry)
        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert sig["long_entry"] == pytest.approx(102.02, abs=1e-6)
        assert sig["long_sl"] == pytest.approx(101.52, abs=1e-6)
        assert sig["long_tp"] == pytest.approx(102.77, abs=1e-6)


# ── Test: Short Signal Calculation ────────────────────────────────────────────

class TestShortSignalCalculation:
    def test_short_signal_calculation(self):
        """Verify entry, SL, and TP for a short signal with fixed-pip offsets."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T09:00:00Z", 100.50, 101.50, 99.00, 100.80),
            ("2024-01-02T10:00:00Z", 100.80, 101.00, 100.50, 100.70),
        ])
        params = default_params(
            stop_loss_pips=50.0,
            take_profit_pips=75.0,
            entry_offset_pips=2.0,
        )
        signals = strategy.generate_signals(ohlcv, params)

        # Range High = 102.00, Range Low = 98.00
        # Short entry = 98.00 - 2.0*0.01  = 97.98
        # Short SL    = 97.98 + 50.0*0.01 = 98.48  (fixed offset from entry)
        # Short TP    = 97.98 - 75.0*0.01 = 97.23  (fixed offset from entry)
        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert sig["short_entry"] == pytest.approx(97.98, abs=1e-6)
        assert sig["short_sl"] == pytest.approx(98.48, abs=1e-6)
        assert sig["short_tp"] == pytest.approx(97.23, abs=1e-6)


# ── Test: Direction Filter ────────────────────────────────────────────────────

class TestDirectionFilter:
    def test_direction_filter_long_only(self):
        """When direction_filter is 'long_only', short signals must be NaN."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(direction_filter="long_only")
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert pd.notna(sig["long_entry"])
        assert pd.notna(sig["long_sl"])
        assert pd.notna(sig["long_tp"])
        assert np.isnan(sig["short_entry"])
        assert np.isnan(sig["short_sl"])
        assert np.isnan(sig["short_tp"])

    def test_direction_filter_short_only(self):
        """When direction_filter is 'short_only', long signals must be NaN."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(direction_filter="short_only")
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert np.isnan(sig["long_entry"])
        assert np.isnan(sig["long_sl"])
        assert np.isnan(sig["long_tp"])
        assert pd.notna(sig["short_entry"])
        assert pd.notna(sig["short_sl"])
        assert pd.notna(sig["short_tp"])

    def test_direction_filter_both(self):
        """When direction_filter is 'both', both long and short signals are set."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(direction_filter="both")
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert pd.notna(sig["long_entry"])
        assert pd.notna(sig["short_entry"])


# ── Test: Trigger Deadline ────────────────────────────────────────────────────

class TestTriggerDeadline:
    def test_no_signal_after_deadline(self):
        """Bars after trigger_deadline should not have signals.  When the first
        bar after range_end falls past the deadline, no signal is emitted."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T09:00:00Z", 100.50, 101.50, 99.00, 100.80),
            # No bar between 10:00 and 18:00. First bar after range_end is
            # 18:00, which is past the deadline of 17:00.
            ("2024-01-02T18:00:00Z", 100.80, 101.00, 100.50, 100.70),
        ])
        params = default_params(trigger_deadline=time(17, 0))
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T18:00:00Z"]
        assert np.isnan(sig["long_entry"])
        assert np.isnan(sig["short_entry"])

    def test_signal_at_deadline_is_valid(self):
        """A bar exactly at trigger_deadline is still within the valid window."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            # First bar after range_end is exactly at deadline
            ("2024-01-02T17:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(trigger_deadline=time(17, 0))
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T17:00:00Z"]
        assert pd.notna(sig["long_entry"])
        assert pd.notna(sig["short_entry"])

    def test_signal_expiry_set_to_deadline(self):
        """The signal_expiry column is set to trigger_deadline as a UTC timestamp."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(trigger_deadline=time(17, 0), timezone="UTC")
        signals = strategy.generate_signals(ohlcv, params)

        expiry = signals.loc["2024-01-02T10:00:00Z", "signal_expiry"]
        expected = pd.Timestamp("2024-01-02T17:00:00Z")
        assert expiry == expected


# ── Test: Signal Emission Granularity ─────────────────────────────────────────

class TestSignalGranularity:
    def test_signal_only_on_first_bar_after_range_end(self):
        """Only the first bar after range_end has the signal; all other bars
        on the same day must be NaN."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T09:00:00Z", 100.50, 101.50, 99.00, 100.80),
            # Three bars after range_end
            ("2024-01-02T10:00:00Z", 100.80, 101.00, 100.50, 100.70),
            ("2024-01-02T11:00:00Z", 100.70, 100.90, 100.40, 100.60),
            ("2024-01-02T12:00:00Z", 100.60, 100.80, 100.30, 100.50),
        ])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        # Only bar at 10:00 should have signal
        assert pd.notna(signals.loc["2024-01-02T10:00:00Z", "long_entry"])
        assert np.isnan(signals.loc["2024-01-02T11:00:00Z", "long_entry"])
        assert np.isnan(signals.loc["2024-01-02T12:00:00Z", "long_entry"])

    def test_multi_day_independent_signals(self):
        """Each trading day produces its own independent signal."""
        ohlcv = make_ohlcv([
            # Day 1
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
            # Day 2
            ("2024-01-03T08:00:00Z", 200.00, 204.00, 196.00, 200.50),
            ("2024-01-03T10:00:00Z", 200.50, 201.00, 200.20, 200.70),
        ])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        # Day 1: Range High=102, Low=98
        sig1 = signals.loc["2024-01-02T10:00:00Z"]
        assert sig1["long_entry"] == pytest.approx(102.01, abs=1e-6)
        assert sig1["short_entry"] == pytest.approx(97.99, abs=1e-6)

        # Day 2: Range High=204, Low=196 (different range)
        sig2 = signals.loc["2024-01-03T10:00:00Z"]
        assert sig2["long_entry"] == pytest.approx(204.01, abs=1e-6)
        assert sig2["short_entry"] == pytest.approx(195.99, abs=1e-6)


# ── Test: Parameter Validation ────────────────────────────────────────────────

class TestValidateParams:
    def test_validate_params_invalid_range(self):
        """range_end <= range_start raises ValueError."""
        params = default_params(range_start=time(10, 0), range_end=time(8, 0))
        with pytest.raises(ValueError, match="range_end"):
            strategy.validate_params(params)

    def test_validate_params_equal_range(self):
        """range_end == range_start raises ValueError."""
        params = default_params(range_start=time(10, 0), range_end=time(10, 0))
        with pytest.raises(ValueError, match="range_end"):
            strategy.validate_params(params)

    def test_validate_params_invalid_deadline(self):
        """trigger_deadline <= range_end raises ValueError."""
        params = default_params(
            range_end=time(10, 0), trigger_deadline=time(9, 0)
        )
        with pytest.raises(ValueError, match="trigger_deadline"):
            strategy.validate_params(params)

    def test_validate_params_invalid_sl(self):
        """stop_loss_pips <= 0 raises ValueError."""
        params = default_params(stop_loss_pips=0)
        with pytest.raises(ValueError, match="stop_loss_pips"):
            strategy.validate_params(params)

    def test_validate_params_negative_sl(self):
        """stop_loss_pips < 0 raises ValueError."""
        params = default_params(stop_loss_pips=-5)
        with pytest.raises(ValueError, match="stop_loss_pips"):
            strategy.validate_params(params)

    def test_validate_params_invalid_tp(self):
        """take_profit_pips <= 0 raises ValueError."""
        params = default_params(take_profit_pips=0)
        with pytest.raises(ValueError, match="take_profit_pips"):
            strategy.validate_params(params)

    def test_validate_params_negative_entry_offset(self):
        """entry_offset_pips < 0 raises ValueError."""
        params = default_params(entry_offset_pips=-1)
        with pytest.raises(ValueError, match="entry_offset_pips"):
            strategy.validate_params(params)

    def test_validate_params_trail_trigger_without_lock(self):
        """trail_trigger_pips set without trail_lock_pips raises ValueError."""
        params = default_params(trail_trigger_pips=100.0, trail_lock_pips=None)
        with pytest.raises(ValueError, match="trail_trigger_pips and trail_lock_pips"):
            strategy.validate_params(params)

    def test_validate_params_trail_lock_zero(self):
        """trail_lock_pips=0 raises ValueError (must be > 0)."""
        params = default_params(trail_trigger_pips=100.0, trail_lock_pips=0)
        with pytest.raises(ValueError, match="trail_lock_pips"):
            strategy.validate_params(params)

    def test_validate_params_trail_trigger_not_greater_than_lock(self):
        """trail_trigger_pips <= trail_lock_pips raises ValueError."""
        params = default_params(trail_trigger_pips=50.0, trail_lock_pips=50.0)
        with pytest.raises(ValueError, match="trail_trigger_pips"):
            strategy.validate_params(params)

    def test_validate_params_trail_trigger_gte_take_profit(self):
        """trail_trigger_pips >= take_profit_pips raises ValueError."""
        params = default_params(
            take_profit_pips=100.0, trail_trigger_pips=100.0, trail_lock_pips=50.0
        )
        with pytest.raises(ValueError, match="trail_trigger_pips"):
            strategy.validate_params(params)

    def test_validate_params_valid(self):
        """Valid params (no trail) do not raise."""
        params = default_params()
        strategy.validate_params(params)  # should not raise

    def test_validate_params_trail_valid(self):
        """Valid trail config (trigger=100, lock=50, tp=175) does not raise."""
        params = default_params(
            take_profit_pips=175.0, trail_trigger_pips=100.0, trail_lock_pips=50.0
        )
        strategy.validate_params(params)  # should not raise

    def test_validate_params_invalid_pip_size(self):
        """pip_size <= 0 raises ValueError."""
        params = default_params(pip_size=0)
        with pytest.raises(ValueError, match="pip_size"):
            strategy.validate_params(params)


# ── Test: Timezone Conversion ─────────────────────────────────────────────────

class TestTimezoneConversion:
    def test_cet_timezone_conversion(self):
        """Strategy uses CET (Europe/Berlin) times correctly.

        In CET (UTC+1), range_start=08:00 CET = 07:00 UTC,
        range_end=10:00 CET = 09:00 UTC.
        """
        ohlcv = make_ohlcv([
            # These UTC times correspond to CET range [08:00, 10:00):
            # 07:00 UTC = 08:00 CET (in range)
            ("2024-01-02T07:00:00Z", 100.00, 102.00, 98.00, 100.50),
            # 08:00 UTC = 09:00 CET (in range)
            ("2024-01-02T08:00:00Z", 100.50, 101.50, 99.00, 100.80),
            # 09:00 UTC = 10:00 CET (first bar at range_end -> signal bar)
            ("2024-01-02T09:00:00Z", 100.80, 101.00, 100.50, 100.70),
            # 10:00 UTC = 11:00 CET (no signal)
            ("2024-01-02T10:00:00Z", 100.70, 100.90, 100.40, 100.60),
        ])
        params = default_params(
            range_start=time(8, 0),
            range_end=time(10, 0),
            trigger_deadline=time(17, 0),
            timezone="Europe/Berlin",
        )
        signals = strategy.generate_signals(ohlcv, params)

        # Range computed from 07:00Z and 08:00Z (which are 08:00 and 09:00 CET)
        # Range High = 102.00 (07:00Z), Range Low = 98.00 (07:00Z)
        # Signal should be on 09:00Z (= 10:00 CET = range_end)
        sig = signals.loc["2024-01-02T09:00:00Z"]
        assert pd.notna(sig["long_entry"])
        assert sig["long_entry"] == pytest.approx(102.01, abs=1e-6)

        # No signal on other bars
        assert np.isnan(signals.loc["2024-01-02T07:00:00Z", "long_entry"])
        assert np.isnan(signals.loc["2024-01-02T08:00:00Z", "long_entry"])
        assert np.isnan(signals.loc["2024-01-02T10:00:00Z", "long_entry"])

    def test_cet_signal_expiry_converted_to_utc(self):
        """Signal expiry is computed in CET then stored as UTC.

        trigger_deadline=17:00 CET on 2024-01-02 = 16:00 UTC.
        """
        ohlcv = make_ohlcv([
            ("2024-01-02T07:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T09:00:00Z", 100.80, 101.00, 100.50, 100.70),
        ])
        params = default_params(
            range_start=time(8, 0),
            range_end=time(10, 0),
            trigger_deadline=time(17, 0),
            timezone="Europe/Berlin",
        )
        signals = strategy.generate_signals(ohlcv, params)

        expiry = signals.loc["2024-01-02T09:00:00Z", "signal_expiry"]
        # 17:00 CET on Jan 2 = 16:00 UTC (CET = UTC+1 in January)
        expected = pd.Timestamp("2024-01-02T16:00:00Z")
        assert expiry == expected

    def test_utc_timezone_no_offset(self):
        """With timezone='UTC', times are used directly without offset."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(timezone="UTC")
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert pd.notna(sig["long_entry"])

        expiry = sig["signal_expiry"]
        expected = pd.Timestamp("2024-01-02T17:00:00Z")
        assert expiry == expected


# ── Test: Empty DataFrame ─────────────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_dataframe(self):
        """Empty input DataFrame returns empty signals without error."""
        ohlcv = make_ohlcv([])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        assert len(signals) == 0
        assert "long_entry" in signals.columns
        assert "signal_expiry" in signals.columns

    def test_no_bar_after_range_end(self):
        """When all bars are in the range and none after, no signal is emitted."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T09:00:00Z", 100.50, 101.50, 99.00, 100.80),
        ])
        params = default_params()
        signals = strategy.generate_signals(ohlcv, params)

        assert all(np.isnan(signals["long_entry"]))
        assert all(np.isnan(signals["short_entry"]))

    def test_entry_offset_zero(self):
        """With entry_offset_pips=0, entry is exactly at range high/low."""
        ohlcv = make_ohlcv([
            ("2024-01-02T08:00:00Z", 100.00, 102.00, 98.00, 100.50),
            ("2024-01-02T10:00:00Z", 100.50, 101.00, 100.20, 100.70),
        ])
        params = default_params(entry_offset_pips=0)
        signals = strategy.generate_signals(ohlcv, params)

        sig = signals.loc["2024-01-02T10:00:00Z"]
        assert sig["long_entry"] == pytest.approx(102.00, abs=1e-6)
        assert sig["short_entry"] == pytest.approx(98.00, abs=1e-6)


# ── Test: Engine Integration (signal_expiry in engine) ────────────────────────

class TestEngineExpiryIntegration:
    """Verify that the engine correctly expires pending orders using the
    signal_expiry column added by the breakout strategy."""

    @staticmethod
    def _make_signals_with_expiry(ohlcv):
        """Helper to create a signals DataFrame with tz-aware signal_expiry."""
        sig_cols = ["long_entry", "long_sl", "long_tp",
                    "short_entry", "short_sl", "short_tp"]
        signals = pd.DataFrame(np.nan, index=ohlcv.index, columns=sig_cols)
        signals["signal_expiry"] = pd.Series(
            pd.NaT, index=ohlcv.index, dtype="datetime64[ns, UTC]"
        )
        return signals

    def test_engine_expires_pending_orders_past_deadline(self):
        """Pending orders are cancelled when bar_time > signal_expiry."""
        from engine.engine import run_backtest
        from engine.models import BacktestConfig, InstrumentConfig

        instrument = InstrumentConfig(pip_size=0.01, pip_value_per_lot=1.0)
        config = BacktestConfig(
            initial_balance=10_000.0,
            sizing_mode="fixed_lot",
            fixed_lot=1.0,
            instrument=instrument,
        )

        ohlcv = make_ohlcv([
            # Signal bar
            ("2024-01-02T10:00:00Z", 100.00, 100.10, 99.90, 100.05),
            # Next bar: price does not reach entry levels
            ("2024-01-02T11:00:00Z", 100.05, 100.15, 99.95, 100.10),
            # Bar past deadline: price would reach entry but orders expired
            ("2024-01-02T18:00:00Z", 100.10, 102.50, 97.00, 100.00),
        ])

        signals = self._make_signals_with_expiry(ohlcv)

        # Set signal on the first bar with expiry at 17:00
        signals.at[ohlcv.index[0], "long_entry"] = 101.00
        signals.at[ohlcv.index[0], "long_sl"] = 99.00
        signals.at[ohlcv.index[0], "long_tp"] = 103.00
        signals.at[ohlcv.index[0], "short_entry"] = 99.00
        signals.at[ohlcv.index[0], "short_sl"] = 101.00
        signals.at[ohlcv.index[0], "short_tp"] = 97.00
        signals.at[ohlcv.index[0], "signal_expiry"] = pd.Timestamp(
            "2024-01-02T17:00:00Z"
        )

        result = run_backtest(ohlcv, signals, config)

        # Orders should have been expired before the 18:00 bar
        # so no trade should occur
        assert len(result.trades) == 0
        assert result.final_balance == 10_000.0

    def test_engine_does_not_expire_orders_before_deadline(self):
        """Orders are not expired when bar_time <= signal_expiry."""
        from engine.engine import run_backtest
        from engine.models import BacktestConfig, InstrumentConfig

        instrument = InstrumentConfig(pip_size=0.01, pip_value_per_lot=1.0)
        config = BacktestConfig(
            initial_balance=10_000.0,
            sizing_mode="fixed_lot",
            fixed_lot=1.0,
            instrument=instrument,
        )

        ohlcv = make_ohlcv([
            # Signal bar
            ("2024-01-02T10:00:00Z", 100.00, 100.10, 99.90, 100.05),
            # Bar before deadline: entry triggers
            ("2024-01-02T16:00:00Z", 100.05, 101.50, 99.95, 101.20),
            # TP hit
            ("2024-01-02T16:30:00Z", 101.20, 103.50, 101.10, 103.00),
        ])

        signals = self._make_signals_with_expiry(ohlcv)

        signals.at[ohlcv.index[0], "long_entry"] = 101.00
        signals.at[ohlcv.index[0], "long_sl"] = 99.00
        signals.at[ohlcv.index[0], "long_tp"] = 103.00
        signals.at[ohlcv.index[0], "signal_expiry"] = pd.Timestamp(
            "2024-01-02T17:00:00Z"
        )

        result = run_backtest(ohlcv, signals, config)

        # Order should trigger at 16:00 (before deadline) and TP at 16:30
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "TP"
