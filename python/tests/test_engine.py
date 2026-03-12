"""
Pytest tests for the backtesting engine (PROJ-2).

All tests use synthetic OHLCV data and hand-crafted signals so that every
assertion can be verified by mental arithmetic without a real market data file.

Instrument: XAUUSD-like
  pip_size           = 0.01   (1 pip = $0.01 price move)
  pip_value_per_lot  = 1.00   (1 pip with 1.0 lot = $1.00)
  → 100 pips = $100 per lot

Default config: 10 000 USD balance, 1.0 fixed lot, no commission/slippage.
"""

import sys
import os

# Allow importing the engine package from python/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd
import pytest

from engine.engine import run_backtest
from engine.models import BacktestConfig, InstrumentConfig

# ── Shared fixtures ──────────────────────────────────────────────────────────

GOLD = InstrumentConfig(pip_size=0.01, pip_value_per_lot=1.0)


def cfg(**kwargs) -> BacktestConfig:
    """Build a BacktestConfig with sensible defaults, overridable via kwargs."""
    defaults = dict(
        initial_balance=10_000.0,
        sizing_mode="fixed_lot",
        fixed_lot=1.0,
        instrument=GOLD,
    )
    defaults.update(kwargs)
    return BacktestConfig(**defaults)


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


def make_signals(ohlcv: pd.DataFrame, signals: dict) -> pd.DataFrame:
    """
    signals: {iso_ts: {col: value, ...}}
    Returns a float DataFrame with the same index as ohlcv.
    Missing cells are NaN (= no order).
    """
    cols = ["long_entry", "long_sl", "long_tp", "short_entry", "short_sl", "short_tp"]
    df = pd.DataFrame(np.nan, index=ohlcv.index, columns=cols, dtype=float)
    for ts_str, vals in signals.items():
        ts = pd.Timestamp(ts_str, tz="UTC")
        for col, val in vals.items():
            df.at[ts, col] = val
    return df


# ── Tests ────────────────────────────────────────────────────────────────────


class TestNoTrades:
    def test_empty_signals_returns_no_trades(self):
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1955, 1945, 1952),
            ("2024-01-02T09:01:00Z", 1952, 1958, 1950, 1956),
        ])
        signals = make_signals(ohlcv, {})
        result = run_backtest(ohlcv, signals, cfg())

        assert result.trades == []
        assert result.final_balance == 10_000.0
        # Equity curve contains only the initial point
        assert len(result.equity_curve) == 1

    def test_signal_on_last_bar_never_enters(self):
        """Signal on the final bar has no next bar to trigger entry."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1960, 1940, 1955),
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {"long_entry": 1955.0, "long_sl": 1940.0},
        })
        result = run_backtest(ohlcv, signals, cfg())
        assert result.trades == []


class TestLongTrade:
    def test_tp_hit(self):
        """Long order queued on bar 0, entry triggers on bar 1, TP hit on bar 2."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1952, 1948, 1951),  # signal bar
            ("2024-01-02T09:01:00Z", 1951, 1960, 1950, 1958),  # entry triggers (high ≥ 1955)
            ("2024-01-02T09:02:00Z", 1958, 1975, 1957, 1972),  # TP hit (high ≥ 1970)
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.direction == "long"
        assert t.exit_reason == "TP"
        assert t.exit_price == 1970.0
        # PnL = (1970 - 1955) / 0.01 * 1.0 = 1500 pips * $1 = $1500
        assert t.pnl_pips == pytest.approx(1500.0, abs=0.1)
        assert t.pnl_currency == pytest.approx(1500.0, abs=0.01)
        assert result.final_balance == pytest.approx(11_500.0, abs=0.01)

    def test_sl_hit(self):
        """SL is hit when bar low goes below the SL price."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),  # signal bar
            ("2024-01-02T09:01:00Z", 1954, 1958, 1953, 1956),  # entry triggers (high ≥ 1955)
            ("2024-01-02T09:02:00Z", 1956, 1957, 1935, 1940),  # SL hit (low ≤ 1940)
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1980.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.exit_reason == "SL"
        assert t.exit_price == 1940.0
        # PnL = (1940 - 1955) / 0.01 = -1500 pips = -$1500
        assert t.pnl_pips == pytest.approx(-1500.0, abs=0.1)
        assert t.pnl_currency == pytest.approx(-1500.0, abs=0.01)
        assert result.final_balance == pytest.approx(8_500.0, abs=0.01)

    def test_sl_wins_when_both_sl_and_tp_hit_same_bar(self):
        """Worst-case assumption: SL wins when both SL and TP are hit in one bar."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T09:01:00Z", 1954, 1958, 1953, 1956),  # entry
            ("2024-01-02T09:02:00Z", 1956, 1975, 1930, 1960),  # both TP≥1970 AND SL≤1940
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert result.trades[0].exit_reason == "SL"


class TestShortTrade:
    def test_tp_hit(self):
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1960, 1962, 1958, 1959),  # signal bar
            ("2024-01-02T09:01:00Z", 1959, 1961, 1950, 1952),  # entry triggers (low ≤ 1955)
            ("2024-01-02T09:02:00Z", 1952, 1953, 1930, 1935),  # TP hit (low ≤ 1940)
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "short_entry": 1955.0, "short_sl": 1970.0, "short_tp": 1940.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.direction == "short"
        assert t.exit_reason == "TP"
        assert t.exit_price == 1940.0
        # PnL = (1955 - 1940) / 0.01 = 1500 pips = $1500
        assert t.pnl_pips == pytest.approx(1500.0, abs=0.1)
        assert result.final_balance == pytest.approx(11_500.0, abs=0.01)

    def test_sl_hit(self):
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1960, 1962, 1958, 1959),  # signal
            ("2024-01-02T09:01:00Z", 1959, 1961, 1950, 1952),  # entry
            ("2024-01-02T09:02:00Z", 1952, 1975, 1951, 1972),  # SL hit (high ≥ 1970)
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "short_entry": 1955.0, "short_sl": 1970.0, "short_tp": 1920.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert result.trades[0].exit_reason == "SL"
        assert result.trades[0].exit_price == 1970.0
        assert result.trades[0].pnl_pips == pytest.approx(-1500.0, abs=0.1)


class TestOCO:
    def test_long_triggers_first_short_cancelled(self):
        """Both long and short pending; bar high reaches long entry first."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1952, 1948, 1951),  # OCO signal
            ("2024-01-02T09:01:00Z", 1951, 1965, 1945, 1960),  # long triggers (high ≥ 1955)
            ("2024-01-02T09:02:00Z", 1960, 1972, 1959, 1970),  # long TP hit
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry":  1955.0, "long_sl":  1940.0, "long_tp":  1970.0,
                "short_entry": 1940.0, "short_sl": 1960.0, "short_tp": 1920.0,
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert len(result.trades) == 1
        assert result.trades[0].direction == "long"
        assert result.trades[0].exit_reason == "TP"

    def test_short_triggers_when_only_short_reached(self):
        """Bar low hits short entry; long entry never reached → short opens."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1952, 1948, 1951),  # OCO signal
            ("2024-01-02T09:01:00Z", 1951, 1954, 1935, 1937),  # short triggers (low ≤ 1940)
            ("2024-01-02T09:02:00Z", 1937, 1938, 1920, 1922),  # short TP hit (low ≤ 1925)
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry":  1965.0, "long_sl":  1945.0, "long_tp":  1985.0,
                "short_entry": 1940.0, "short_sl": 1955.0, "short_tp": 1925.0,
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert len(result.trades) == 1
        assert result.trades[0].direction == "short"
        assert result.trades[0].exit_reason == "TP"


class TestTimeExit:
    def test_position_closed_at_exit_time(self):
        """Open position is closed at bar open when bar_time >= 21:00."""
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),  # entry
            ("2024-01-02T20:59:00Z", 1956, 1957, 1955, 1956),  # still open
            ("2024-01-02T21:00:00Z", 1958, 1965, 1957, 1963),  # time exit at open=1958
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg(time_exit="21:00"))

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.exit_reason == "TIME"
        assert t.exit_price == pytest.approx(1958.0, abs=0.01)

    def test_time_exit_fires_on_next_bar_when_exact_bar_missing(self):
        """When no bar falls exactly on exit_time, the first bar AFTER it triggers the exit."""
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),  # entry
            ("2024-01-02T15:29:00Z", 1956, 1957, 1955, 1956),  # still open, before exit_time
            ("2024-01-02T15:31:00Z", 1960, 1965, 1958, 1963),  # first bar ≥ 15:30 → exit at open=1960
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg(time_exit="15:30"))

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.exit_reason == "TIME"
        assert t.exit_price == pytest.approx(1960.0, abs=0.01)  # 15:31 bar open

    def test_time_exit_takes_priority_over_sl_tp(self):
        """Even if SL/TP would fire, time exit (bar open) fires first."""
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),  # entry
            # At 21:00 the bar open is below SL — time exit runs before SL check
            ("2024-01-02T21:00:00Z", 1942, 1943, 1930, 1935),  # time exit at 1942
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1945.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg(time_exit="21:00"))

        t = result.trades[0]
        assert t.exit_reason == "TIME"
        assert t.exit_price == pytest.approx(1942.0, abs=0.01)


class TestTrailTrigger:
    def test_short_trail_trigger(self):
        """
        Short trade: trail trigger fires when unrealised profit reaches 50 pips;
        SL moves to entry - lock_pips (i.e. 20 pips of locked profit).
        Entry short at 1955, trail trigger=50, lock=20.
        Bar where low reaches 1954.50 → profit = (1955-1954.50)/0.01 = 50 pips → trail fires.
        SL moves to 1955 - 0.20 = 1954.80.
        Next bar high = 1954.85 ≥ 1954.80 → SL_TRAILED.
        """
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1960, 1962, 1958, 1959),          # signal bar
            ("2024-01-02T10:01:00Z", 1959, 1961, 1950, 1952),          # entry: low=1950 ≤ 1955
            ("2024-01-02T10:02:00Z", 1952, 1954.70, 1954.50, 1954.60), # low=1954.50 → 50 pip profit, trail fires; high=1954.70 < 1954.80, SL not hit yet
            ("2024-01-02T10:03:00Z", 1954.60, 1954.85, 1954.40, 1954.50), # high=1954.85 ≥ 1954.80 → SL_TRAILED
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "short_entry": 1955.0, "short_sl": 1970.0, "short_tp": 1900.0
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(trail_trigger_pips=50, trail_lock_pips=20)
        )

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.direction == "short"
        assert t.exit_reason == "SL_TRAILED"
        # SL moved to entry - 0.20 = 1954.80; locked in 20 pips of profit
        assert t.exit_price == pytest.approx(1954.80, abs=0.001)
        assert t.pnl_pips == pytest.approx(20.0, abs=0.1)

    def test_trail_trigger_without_lock_pips_defaults_to_breakeven(self):
        """
        When trail_lock_pips is None it defaults to 0 (breakeven).
        Entry long at 1955, SL moves to 1955+0=1955 when trigger fires.
        A bar with low < 1955 then exits at SL_TRAILED with 0 gross pips.
        """
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),              # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),              # entry at 1955
            ("2024-01-02T10:02:00Z", 1956, 1955.60, 1955.05, 1955.10),    # high=1955.60 ≥ 1955.50 → trail fires, SL→1955; low=1955.05 > 1955, not hit
            ("2024-01-02T10:03:00Z", 1955.10, 1955.20, 1954.90, 1955.00), # low=1954.90 < 1955 → SL_TRAILED at 1955
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(trail_trigger_pips=50, trail_lock_pips=None)
        )

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.exit_reason == "SL_TRAILED"
        assert t.exit_price == pytest.approx(1955.0, abs=0.001)  # breakeven
        assert t.pnl_pips == pytest.approx(0.0, abs=0.1)

    def test_sl_moves_after_trigger_then_trailed_sl_hit(self):
        """
        Trail: trigger=50 pips, lock=20 pips.
        Entry long at 1955. After bar where high reaches 1955+0.50=1955.50 (50 pips),
        SL moves to 1955+0.20=1955.20.  Next bar low hits 1955.10 < new SL → SL_TRAILED.
        """
        # pip_size=0.01 → 50 pips = 0.50 price move; 20 pips = 0.20 price move
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),  # entry triggers
            ("2024-01-02T10:02:00Z", 1956, 1955.60, 1954, 1955),  # high=1955.60 ≥ 1955+0.50 → trail
            ("2024-01-02T10:03:00Z", 1955, 1956, 1955.10, 1955.15),  # low=1955.10 < 1955.20 → SL_TRAILED
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(trail_trigger_pips=50, trail_lock_pips=20)
        )

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.exit_reason == "SL_TRAILED"
        assert t.exit_price == pytest.approx(1955.20, abs=0.001)

    def test_trail_fires_once_only(self):
        """Trail trigger is applied at most once; further profit doesn't re-trigger."""
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),  # entry
            ("2024-01-02T10:02:00Z", 1956, 1956.50, 1954, 1955),  # trail fires (high ≥ 1955.50)
            ("2024-01-02T10:03:00Z", 1955, 1957.50, 1955.25, 1957),  # high ≥ 1955.50 again, but trail already applied
            ("2024-01-02T10:04:00Z", 1957, 1958, 1940, 1942),  # SL_TRAILED at 1955.20
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(trail_trigger_pips=50, trail_lock_pips=20)
        )

        t = result.trades[0]
        assert t.exit_reason == "SL_TRAILED"
        # SL should be 1955 + 0.20 = 1955.20, not some higher value
        assert t.exit_price == pytest.approx(1955.20, abs=0.001)

    def test_no_trail_trigger_uses_original_sl(self):
        """If profit never reaches trail_trigger, original SL is used and reason is SL."""
        ohlcv = make_ohlcv([
            ("2024-01-02T10:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T10:01:00Z", 1954, 1958, 1953, 1956),  # entry
            ("2024-01-02T10:02:00Z", 1956, 1955.40, 1935, 1938),  # max profit 40 pips < 50, SL hit
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T10:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(trail_trigger_pips=50, trail_lock_pips=20)
        )

        t = result.trades[0]
        assert t.exit_reason == "SL"
        assert t.exit_price == pytest.approx(1940.0, abs=0.001)


class TestCommissionAndSlippage:
    def test_commission_deducted(self):
        """Commission of $10 per trade is deducted from PnL."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry
            ("2024-01-02T09:02:00Z", 1960, 1980, 1959, 1978),  # TP at 1970
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg(commission=10.0))

        t = result.trades[0]
        # Gross PnL = (1970-1955)/0.01 * 1.0 = 1500 pips = $1500; minus $10 commission
        assert t.pnl_currency == pytest.approx(1490.0, abs=0.01)
        assert result.final_balance == pytest.approx(11_490.0, abs=0.01)

    def test_slippage_adverse_on_exit(self):
        """2 pip slippage is applied adversely at exit."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry
            ("2024-01-02T09:02:00Z", 1960, 1980, 1959, 1978),  # TP at 1970
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg(slippage_pips=2.0))

        t = result.trades[0]
        # Entry slippage: actual entry = 1955.00 + 0.02 = 1955.02
        # Exit slippage:  actual exit  = 1970.00 - 0.02 = 1969.98
        # PnL = (1969.98 - 1955.02) / 0.01 = 1496 pips = $1496
        assert t.exit_price == pytest.approx(1969.98, abs=0.001)
        assert t.pnl_pips == pytest.approx(1496.0, abs=0.1)

    def test_commission_and_slippage_combined(self):
        """Both commission and slippage apply simultaneously; effects are additive."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry
            ("2024-01-02T09:02:00Z", 1960, 1980, 1959, 1978),  # TP at 1970
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg(slippage_pips=2.0, commission=10.0))

        t = result.trades[0]
        # Entry: 1955.00 + 0.02 = 1955.02  (adverse entry slippage)
        # Exit:  1970.00 - 0.02 = 1969.98  (adverse exit slippage)
        # Gross pips: (1969.98 - 1955.02) / 0.01 = 1496.0
        # PnL currency: 1496.0 * 1.0 - 10.0 = $1486.0
        assert t.exit_price == pytest.approx(1969.98, abs=0.001)
        assert t.pnl_pips == pytest.approx(1496.0, abs=0.1)
        assert t.pnl_currency == pytest.approx(1486.0, abs=0.01)
        assert result.final_balance == pytest.approx(11_486.0, abs=0.01)


class TestRiskPercentSizing:
    def test_lot_size_calculated_from_balance(self):
        """
        risk_percent=1 on 10 000 USD → risk $100.
        SL distance: 1955 - 1940 = 15 / 0.01 = 1500 pips.
        pip_value_per_lot = 1.0
        lot = 100 / (1500 * 1.0) = 0.07 (rounded to 2 d.p.)
        """
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry
            ("2024-01-02T09:02:00Z", 1960, 1980, 1959, 1978),  # TP at 1970
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(sizing_mode="risk_percent", risk_percent=1.0, fixed_lot=None)
        )

        assert len(result.trades) == 1
        assert result.trades[0].lot_size == pytest.approx(0.07, abs=0.001)


class TestEndOfData:
    def test_open_position_closed_at_last_bar_close(self):
        """If data ends while a position is open it is closed at last bar's close."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),  # signal
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry
            ("2024-01-02T09:02:00Z", 1960, 1965, 1959, 1963),  # data ends, no SL/TP hit
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 2000.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        assert len(result.trades) == 1
        t = result.trades[0]
        assert t.exit_reason == "TIME"
        assert t.exit_price == pytest.approx(1963.0, abs=0.01)  # last bar close


class TestEdgeCases:
    def test_empty_ohlcv_returns_empty_result(self):
        """Empty OHLCV DataFrame must not raise; returns an empty result."""
        ohlcv = make_ohlcv([])
        signals = make_signals(ohlcv, {})
        result = run_backtest(ohlcv, signals, cfg())

        assert result.trades == []
        assert result.equity_curve == []
        assert result.final_balance == pytest.approx(10_000.0)


class TestRiskPercentCompounding:
    def test_lot_size_grows_with_balance_after_winning_trade(self):
        """
        After a winning trade the balance is higher, so the next trade's lot size
        should be proportionally larger (compounding).

        Instrument: pip_size=0.01, pip_value_per_lot=1.0
        initial_balance=1000, risk_percent=10%

        Trade 1: entry=100.00, SL=99.50 → SL dist=50 pips
          lot = (1000 * 10%) / (50 * 1.0) = 2.0
          TP=101.00 → pnl = 100 pips * 2.0 = $200 → balance = $1200

        Trade 2: entry=101.10, SL=100.60 → SL dist=50 pips
          lot = (1200 * 10%) / (50 * 1.0) = 2.4
          TP=102.10 → pnl = 100 pips * 2.4 = $240 → final balance = $1440
        """
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z",  99.80,  99.90,  99.70,  99.85),  # signal 1
            ("2024-01-02T09:01:00Z",  99.85, 100.10,  99.80, 100.05),  # entry 1: high ≥ 100.00
            ("2024-01-02T09:02:00Z", 100.05, 101.10, 100.00, 101.05),  # TP1 hit (high ≥ 101.00); signal 2 queued
            ("2024-01-02T09:03:00Z", 101.05, 101.20, 101.00, 101.15),  # entry 2: high ≥ 101.10
            ("2024-01-02T09:04:00Z", 101.15, 102.20, 101.10, 102.15),  # TP2 hit (high ≥ 102.10)
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 100.00, "long_sl": 99.50, "long_tp": 101.00,
            },
            "2024-01-02T09:02:00Z": {
                "long_entry": 101.10, "long_sl": 100.60, "long_tp": 102.10,
            },
        })
        result = run_backtest(
            ohlcv, signals,
            cfg(
                initial_balance=1000.0,
                sizing_mode="risk_percent",
                risk_percent=10.0,
                fixed_lot=None,
                instrument=InstrumentConfig(pip_size=0.01, pip_value_per_lot=1.0),
            ),
        )

        assert len(result.trades) == 2
        assert result.trades[0].lot_size == pytest.approx(2.0, abs=0.01)
        assert result.trades[1].lot_size == pytest.approx(2.4, abs=0.01)
        assert result.final_balance == pytest.approx(1440.0, abs=0.01)


class TestDeterminism:
    def test_identical_runs_produce_identical_results(self):
        """Running the same backtest twice must return bit-for-bit identical results."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),
            ("2024-01-02T09:02:00Z", 1960, 1980, 1959, 1978),
            ("2024-01-02T09:03:00Z", 1978, 1985, 1977, 1982),
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
        })
        config = cfg()
        result1 = run_backtest(ohlcv, signals, config)
        result2 = run_backtest(ohlcv, signals, config)

        assert result1.final_balance == result2.final_balance
        assert len(result1.trades) == len(result2.trades)
        for t1, t2 in zip(result1.trades, result2.trades):
            assert t1.pnl_currency == t2.pnl_currency
            assert t1.exit_reason == t2.exit_reason


class TestMultipleTrades:
    def test_no_new_entry_while_position_open(self):
        """A second signal while a trade is open must be ignored."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),  # signal 1
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry 1
            ("2024-01-02T09:02:00Z", 1960, 1975, 1959, 1972),  # TP1 hit at 1970; also signal 2 on same bar
            ("2024-01-02T09:03:00Z", 1972, 1985, 1971, 1980),  # signal 2 would queue here - no entry since signal was on closed trade bar
            ("2024-01-02T09:04:00Z", 1980, 1990, 1979, 1988),  # bar after
        ])
        # Signal 2 is placed on bar 2 (same bar as TP exit) - engine sees it AFTER the exit,
        # so the signal from bar 2 becomes pending and would need bar 3 to trigger.
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1970.0
            },
            "2024-01-02T09:02:00Z": {
                "long_entry": 1975.0, "long_sl": 1960.0, "long_tp": 1990.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())
        # Trade 1 exits via TP on bar 2. Signal 2 is processed on bar 2 (position is None after exit).
        # Bar 3 high=1985 ≥ 1975 → entry 2 triggers on bar 3. Bar 4 high=1990 ≥ 1990 → TP2.
        assert len(result.trades) == 2
        assert result.trades[0].exit_reason == "TP"
        assert result.trades[1].exit_reason == "TP"

    def test_signal_ignored_while_position_open(self):
        """Signal on bar N is ignored if a position is still open at bar N."""
        ohlcv = make_ohlcv([
            ("2024-01-02T09:00:00Z", 1950, 1956, 1948, 1954),  # signal 1
            ("2024-01-02T09:01:00Z", 1954, 1962, 1953, 1960),  # entry 1 opens
            ("2024-01-02T09:02:00Z", 1960, 1964, 1959, 1962),  # position open; signal 2 on this bar ignored
            ("2024-01-02T09:03:00Z", 1962, 1985, 1961, 1983),  # TP1 hit; no second entry (signal 2 was ignored)
            ("2024-01-02T09:04:00Z", 1983, 1990, 1982, 1988),  # no pending orders
        ])
        signals = make_signals(ohlcv, {
            "2024-01-02T09:00:00Z": {
                "long_entry": 1955.0, "long_sl": 1940.0, "long_tp": 1980.0
            },
            "2024-01-02T09:02:00Z": {
                "long_entry": 1963.0, "long_sl": 1950.0, "long_tp": 1990.0
            },
        })
        result = run_backtest(ohlcv, signals, cfg())

        # Only 1 trade: the second signal was placed while the position was open
        assert len(result.trades) == 1
        assert result.trades[0].exit_reason == "TP"
