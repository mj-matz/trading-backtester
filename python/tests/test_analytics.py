"""Tests for the Performance Analytics module (PROJ-4).

Covers trade_metrics, equity_metrics, risk_metrics, monthly_metrics,
and the top-level calculator with edge cases.
"""

import math
import sys
import os
from datetime import datetime, timezone

import pytest

# Ensure python/ is on the path so imports resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from engine.models import BacktestResult, Trade
from analytics.calculator import calculate_analytics
from analytics.models import AnalyticsResult, Metric, MonthlyR
from analytics import trade_metrics, equity_metrics, risk_metrics, monthly_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dt(s: str) -> datetime:
    """Parse an ISO-8601 string to a tz-aware datetime."""
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _make_trade(
    entry: str,
    exit_: str,
    pnl_currency: float,
    pnl_pips: float,
    risk_currency: float = 100.0,
    risk_pips: float = 10.0,
    direction: str = "long",
    exit_reason: str = "TP",
) -> Trade:
    return Trade(
        entry_time=_dt(entry),
        entry_price=1.1000,
        exit_time=_dt(exit_),
        exit_price=1.1100,
        exit_reason=exit_reason,
        direction=direction,
        lot_size=1.0,
        pnl_pips=pnl_pips,
        pnl_currency=pnl_currency,
        initial_risk_pips=risk_pips,
        initial_risk_currency=risk_currency,
    )


# A standard set of 5 trades for reuse
SAMPLE_TRADES = [
    _make_trade("2025-01-10T10:00", "2025-01-10T14:00", 200.0, 20.0),   # win
    _make_trade("2025-01-11T09:00", "2025-01-11T12:00", -100.0, -10.0, exit_reason="SL"),  # loss
    _make_trade("2025-01-15T08:00", "2025-01-15T16:00", 300.0, 30.0),   # win
    _make_trade("2025-02-02T10:00", "2025-02-02T15:00", -50.0, -5.0, exit_reason="SL"),    # loss
    _make_trade("2025-02-10T09:00", "2025-02-10T11:00", 150.0, 15.0),   # win
]

SAMPLE_EQUITY = [
    {"time": "2025-01-10T10:00:00+00:00", "balance": 10000.0},
    {"time": "2025-01-10T14:00:00+00:00", "balance": 10200.0},
    {"time": "2025-01-11T12:00:00+00:00", "balance": 10100.0},
    {"time": "2025-01-15T16:00:00+00:00", "balance": 10400.0},
    {"time": "2025-02-02T15:00:00+00:00", "balance": 10350.0},
    {"time": "2025-02-10T11:00:00+00:00", "balance": 10500.0},
]


# ===========================================================================
# Trade Metrics
# ===========================================================================

class TestTradeMetricsCounts:
    def test_total_trades(self):
        assert trade_metrics.total_trades(SAMPLE_TRADES) == 5

    def test_winning_trades(self):
        assert len(trade_metrics.winning_trades(SAMPLE_TRADES)) == 3

    def test_losing_trades(self):
        assert len(trade_metrics.losing_trades(SAMPLE_TRADES)) == 2

    def test_empty(self):
        assert trade_metrics.total_trades([]) == 0
        assert trade_metrics.winning_trades([]) == []
        assert trade_metrics.losing_trades([]) == []


class TestWinRate:
    def test_sample(self):
        assert trade_metrics.win_rate(SAMPLE_TRADES) == pytest.approx(60.0)

    def test_no_trades(self):
        assert trade_metrics.win_rate([]) is None


class TestProfitFactorCurrency:
    def test_sample(self):
        # Gross Profit = 200 + 300 + 150 = 650
        # Gross Loss = 100 + 50 = 150
        # PF = 650 / 150 = 4.333...
        assert trade_metrics.profit_factor_currency(SAMPLE_TRADES) == pytest.approx(650.0 / 150.0)

    def test_all_winners(self):
        winners = [t for t in SAMPLE_TRADES if t.pnl_currency > 0]
        assert trade_metrics.profit_factor_currency(winners) == float("inf")

    def test_no_trades(self):
        assert trade_metrics.profit_factor_currency([]) is None


class TestAvgWinLoss:
    def test_avg_win_currency(self):
        # 650 / 3 = 216.666...
        assert trade_metrics.avg_win_currency(SAMPLE_TRADES) == pytest.approx(650.0 / 3)

    def test_avg_loss_currency(self):
        # 150 / 2 = 75.0
        assert trade_metrics.avg_loss_currency(SAMPLE_TRADES) == pytest.approx(75.0)

    def test_avg_win_pips(self):
        # 20 + 30 + 15 = 65 / 3
        assert trade_metrics.avg_win_pips(SAMPLE_TRADES) == pytest.approx(65.0 / 3)

    def test_avg_loss_pips(self):
        # 10 + 5 = 15 / 2
        assert trade_metrics.avg_loss_pips(SAMPLE_TRADES) == pytest.approx(7.5)


class TestBestWorstTrade:
    def test_best_currency(self):
        assert trade_metrics.best_trade_currency(SAMPLE_TRADES) == 300.0

    def test_worst_currency(self):
        assert trade_metrics.worst_trade_currency(SAMPLE_TRADES) == -100.0

    def test_best_pips(self):
        assert trade_metrics.best_trade_pips(SAMPLE_TRADES) == 30.0

    def test_worst_pips(self):
        assert trade_metrics.worst_trade_pips(SAMPLE_TRADES) == -10.0

    def test_empty(self):
        assert trade_metrics.best_trade_currency([]) is None
        assert trade_metrics.worst_trade_currency([]) is None


class TestStreaks:
    def test_sample(self):
        # W, L, W, L, W -> max win streak = 1, max loss streak = 1
        wins, losses = trade_metrics.consecutive_streaks(SAMPLE_TRADES)
        assert wins == 1
        assert losses == 1

    def test_all_winners(self):
        winners = [_make_trade(f"2025-01-{i+10}T10:00", f"2025-01-{i+10}T14:00", 100, 10) for i in range(4)]
        wins, losses = trade_metrics.consecutive_streaks(winners)
        assert wins == 4
        assert losses == 0

    def test_empty(self):
        assert trade_metrics.consecutive_streaks([]) == (0, 0)


class TestDuration:
    def test_sample(self):
        # Trade 1: 4h, Trade 2: 3h, Trade 3: 8h, Trade 4: 5h, Trade 5: 2h
        # Average = 22/5 = 4.4 hours
        result = trade_metrics.avg_trade_duration_hours(SAMPLE_TRADES)
        assert result == pytest.approx(4.4)

    def test_empty(self):
        assert trade_metrics.avg_trade_duration_hours([]) is None


class TestRMultiples:
    def test_single_trade(self):
        t = _make_trade("2025-01-10T10:00", "2025-01-10T14:00", 200.0, 20.0, risk_currency=100.0)
        assert trade_metrics.r_multiple(t) == pytest.approx(2.0)

    def test_zero_risk(self):
        t = _make_trade("2025-01-10T10:00", "2025-01-10T14:00", 200.0, 20.0, risk_currency=0.0)
        assert trade_metrics.r_multiple(t) is None

    def test_total_r(self):
        # R-multiples: 2.0, -1.0, 3.0, -0.5, 1.5 => total = 5.0
        assert trade_metrics.total_r(SAMPLE_TRADES) == pytest.approx(5.0)

    def test_avg_r(self):
        assert trade_metrics.avg_r_per_trade(SAMPLE_TRADES) == pytest.approx(1.0)


class TestExpectancy:
    def test_currency(self):
        # WR=0.6, LR=0.4, AvgWin=216.67, AvgLoss=75
        # 0.6*216.67 - 0.4*75 = 130 - 30 = 100
        result = trade_metrics.expectancy_currency(SAMPLE_TRADES)
        assert result == pytest.approx(100.0)

    def test_pips(self):
        # WR=0.6, LR=0.4, AvgWin(pips)=21.67, AvgLoss(pips)=7.5
        # 0.6*21.67 - 0.4*7.5 = 13 - 3 = 10
        result = trade_metrics.expectancy_pips(SAMPLE_TRADES)
        assert result == pytest.approx(10.0)


# ===========================================================================
# Equity Metrics
# ===========================================================================

class TestTotalReturn:
    def test_positive(self):
        assert equity_metrics.total_return_pct(10000.0, 10500.0) == pytest.approx(5.0)

    def test_negative(self):
        assert equity_metrics.total_return_pct(10000.0, 9000.0) == pytest.approx(-10.0)

    def test_zero_initial(self):
        assert equity_metrics.total_return_pct(0.0, 100.0) is None


class TestCAGR:
    def test_one_year(self):
        ec = [
            {"time": "2025-01-01T00:00:00+00:00", "balance": 10000.0},
            {"time": "2026-01-01T00:00:00+00:00", "balance": 11000.0},
        ]
        result = equity_metrics.cagr(10000.0, 11000.0, ec)
        assert result == pytest.approx(10.0, rel=0.02)  # ~10% CAGR

    def test_short_period(self):
        result = equity_metrics.cagr(10000.0, 10500.0, SAMPLE_EQUITY)
        assert result is not None  # Extrapolated but valid

    def test_empty(self):
        assert equity_metrics.cagr(10000.0, 10000.0, []) is None


class TestMaxDrawdown:
    def test_sample(self):
        dd_pct, dd_dur = equity_metrics.max_drawdown(SAMPLE_EQUITY)
        # Peak 10200 -> trough 10100 = 100/10200 = 0.98% drawdown
        # (this is larger than the later 10400->10350 = 0.48%)
        assert dd_pct is not None
        assert dd_pct == pytest.approx(100.0 / 10200.0 * 100.0, rel=0.01)

    def test_monotonically_increasing(self):
        ec = [
            {"time": "2025-01-01T00:00:00+00:00", "balance": 100.0},
            {"time": "2025-01-02T00:00:00+00:00", "balance": 200.0},
            {"time": "2025-01-03T00:00:00+00:00", "balance": 300.0},
        ]
        dd_pct, dd_dur = equity_metrics.max_drawdown(ec)
        assert dd_pct == 0.0
        assert dd_dur == 0.0  # No drawdown occurred, so duration must also be 0

    def test_never_recovers(self):
        ec = [
            {"time": "2025-01-01T00:00:00+00:00", "balance": 1000.0},
            {"time": "2025-01-15T00:00:00+00:00", "balance": 800.0},
            {"time": "2025-02-01T00:00:00+00:00", "balance": 700.0},
        ]
        dd_pct, dd_dur = equity_metrics.max_drawdown(ec)
        assert dd_pct == pytest.approx(30.0)
        assert dd_dur is not None
        assert dd_dur == pytest.approx(31.0, abs=0.1)  # Jan 1 -> Feb 1

    def test_empty(self):
        assert equity_metrics.max_drawdown([]) == (None, None)


# ===========================================================================
# Risk Metrics
# ===========================================================================

class TestSharpeRatio:
    def test_returns_float(self):
        result = risk_metrics.sharpe_ratio(SAMPLE_EQUITY)
        # With only a few days we just check it returns a number
        assert result is None or isinstance(result, float)

    def test_empty(self):
        assert risk_metrics.sharpe_ratio([]) is None

    def test_constant_balance(self):
        ec = [
            {"time": "2025-01-01T00:00:00+00:00", "balance": 1000.0},
            {"time": "2025-01-02T00:00:00+00:00", "balance": 1000.0},
            {"time": "2025-01-03T00:00:00+00:00", "balance": 1000.0},
        ]
        # Std = 0 -> None
        assert risk_metrics.sharpe_ratio(ec) is None


class TestSortinoRatio:
    def test_returns_float(self):
        result = risk_metrics.sortino_ratio(SAMPLE_EQUITY)
        assert result is None or isinstance(result, float)

    def test_no_negative_returns(self):
        ec = [
            {"time": "2025-01-01T00:00:00+00:00", "balance": 1000.0},
            {"time": "2025-01-02T00:00:00+00:00", "balance": 1100.0},
            {"time": "2025-01-03T00:00:00+00:00", "balance": 1200.0},
        ]
        # No negative returns -> None
        assert risk_metrics.sortino_ratio(ec) is None


# ===========================================================================
# Monthly Metrics
# ===========================================================================

class TestMonthlyR:
    def test_two_months(self):
        result = monthly_metrics.monthly_r_breakdown(SAMPLE_TRADES)
        assert len(result) == 2
        assert result[0].month == "2025-01"
        assert result[1].month == "2025-02"

        # Jan: R = 2.0 + (-1.0) + 3.0 = 4.0, count = 3
        assert result[0].r_earned == pytest.approx(4.0)
        assert result[0].trade_count == 3

        # Feb: R = (-0.5) + 1.5 = 1.0, count = 2
        assert result[1].r_earned == pytest.approx(1.0)
        assert result[1].trade_count == 2

    def test_empty(self):
        assert monthly_metrics.monthly_r_breakdown([]) == []

    def test_avg_r_per_month(self):
        monthly = monthly_metrics.monthly_r_breakdown(SAMPLE_TRADES)
        result = monthly_metrics.avg_r_per_month(SAMPLE_TRADES, monthly)
        # Total R = 5.0, months = 2 -> 2.5
        assert result == pytest.approx(2.5)


# ===========================================================================
# Top-level Calculator
# ===========================================================================

class TestCalculateAnalytics:
    def test_standard_run(self):
        result = BacktestResult(
            trades=SAMPLE_TRADES,
            equity_curve=SAMPLE_EQUITY,
            final_balance=10500.0,
            initial_balance=10000.0,
        )
        analytics = calculate_analytics(result)
        assert isinstance(analytics, AnalyticsResult)
        assert len(analytics.summary) > 20
        assert len(analytics.monthly_r) == 2

        # Spot-check a few metrics by name
        metrics_dict = {m.name: m for m in analytics.summary}
        assert metrics_dict["Total Trades"].value == 5
        assert metrics_dict["Win Rate"].value == pytest.approx(60.0)
        assert metrics_dict["Total Return"].value == pytest.approx(5.0)
        assert metrics_dict["Total R"].value == pytest.approx(5.0)

    def test_zero_trades(self):
        """Edge case: no trades should return all zeroes/nulls with no errors."""
        result = BacktestResult(
            trades=[],
            equity_curve=[{"time": "2025-01-01T00:00:00+00:00", "balance": 10000.0}],
            final_balance=10000.0,
            initial_balance=10000.0,
        )
        analytics = calculate_analytics(result)
        metrics_dict = {m.name: m for m in analytics.summary}
        assert metrics_dict["Total Trades"].value == 0
        assert metrics_dict["Win Rate"].value is None
        assert metrics_dict["Profit Factor"].value is None
        assert analytics.monthly_r == []

    def test_single_trade(self):
        """Edge case: single trade."""
        trade = _make_trade("2025-03-01T10:00", "2025-03-01T14:00", 200.0, 20.0)
        ec = [
            {"time": "2025-03-01T10:00:00+00:00", "balance": 10000.0},
            {"time": "2025-03-01T14:00:00+00:00", "balance": 10200.0},
        ]
        result = BacktestResult(
            trades=[trade],
            equity_curve=ec,
            final_balance=10200.0,
            initial_balance=10000.0,
        )
        analytics = calculate_analytics(result)
        metrics_dict = {m.name: m for m in analytics.summary}
        assert metrics_dict["Total Trades"].value == 1
        assert metrics_dict["Win Rate"].value == pytest.approx(100.0)
        assert metrics_dict["Avg R per Trade"].value == pytest.approx(2.0)

    def test_all_losers(self):
        """Edge case: all trades are losses."""
        losers = [
            _make_trade("2025-01-10T10:00", "2025-01-10T14:00", -100.0, -10.0, exit_reason="SL"),
            _make_trade("2025-01-11T10:00", "2025-01-11T14:00", -50.0, -5.0, exit_reason="SL"),
        ]
        ec = [
            {"time": "2025-01-10T10:00:00+00:00", "balance": 10000.0},
            {"time": "2025-01-10T14:00:00+00:00", "balance": 9900.0},
            {"time": "2025-01-11T14:00:00+00:00", "balance": 9850.0},
        ]
        result = BacktestResult(
            trades=losers,
            equity_curve=ec,
            final_balance=9850.0,
            initial_balance=10000.0,
        )
        analytics = calculate_analytics(result)
        metrics_dict = {m.name: m for m in analytics.summary}
        assert metrics_dict["Win Rate"].value == pytest.approx(0.0)
        assert metrics_dict["Avg Win"].value is None
        assert metrics_dict["Consecutive Losses"].value == 2

    def test_zero_risk_trade(self):
        """Edge case: trade with initial_risk_currency == 0 should not crash."""
        zero_risk = _make_trade(
            "2025-01-10T10:00", "2025-01-10T14:00",
            200.0, 20.0, risk_currency=0.0, risk_pips=0.0,
        )
        ec = [
            {"time": "2025-01-10T10:00:00+00:00", "balance": 10000.0},
            {"time": "2025-01-10T14:00:00+00:00", "balance": 10200.0},
        ]
        result = BacktestResult(
            trades=[zero_risk],
            equity_curve=ec,
            final_balance=10200.0,
            initial_balance=10000.0,
        )
        analytics = calculate_analytics(result)
        metrics_dict = {m.name: m for m in analytics.summary}
        # R-multiples should be None since risk = 0
        assert metrics_dict["Total R"].value is None
        assert metrics_dict["Avg R per Trade"].value is None
