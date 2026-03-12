"""Backtesting Engine (PROJ-2).

Public entry point: run_backtest() in engine.py
"""
from .engine import run_backtest
from .models import BacktestConfig, InstrumentConfig, Trade, BacktestResult

__all__ = ["run_backtest", "BacktestConfig", "InstrumentConfig", "Trade", "BacktestResult"]
