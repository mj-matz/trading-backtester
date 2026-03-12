"""Dataclasses for the backtesting engine (PROJ-2)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional


@dataclass
class InstrumentConfig:
    """Instrument-specific price and monetary value configuration."""

    pip_size: float  # e.g. 0.0001 for EURUSD, 0.01 for XAUUSD, 0.5 for GER30
    pip_value_per_lot: float  # monetary value of 1 pip for 1.0 lot (account currency)


@dataclass
class BacktestConfig:
    """Full configuration for a single backtest run."""

    initial_balance: float
    sizing_mode: Literal["fixed_lot", "risk_percent"]
    instrument: InstrumentConfig

    # Exactly one must be set, depending on sizing_mode
    fixed_lot: Optional[float] = None
    risk_percent: Optional[float] = None  # e.g. 1.0 means 1 %

    commission: float = 0.0        # fixed cost deducted per trade (account currency)
    slippage_pips: float = 0.0     # adverse price offset applied on entry and exit

    time_exit: Optional[str] = None         # "HH:MM" in instrument local timezone, e.g. "21:00"
    timezone: str = "UTC"                   # IANA timezone for time_exit, e.g. "Europe/Berlin"
    trail_trigger_pips: Optional[float] = None  # unrealised profit threshold to step SL
    trail_lock_pips: Optional[float] = None     # pips from entry to which SL is moved


@dataclass
class Trade:
    """Record of one completed trade."""

    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    exit_reason: Literal["SL", "SL_TRAILED", "TP", "TIME"]
    direction: Literal["long", "short"]
    lot_size: float
    pnl_pips: float
    pnl_currency: float
    initial_risk_pips: float
    initial_risk_currency: float


@dataclass
class BacktestResult:
    """Output of run_backtest()."""

    trades: list          # List[Trade]
    equity_curve: list    # List[{"time": str, "balance": float}]
    final_balance: float
    initial_balance: float
