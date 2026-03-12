"""Position sizing calculations for the backtesting engine."""

from .models import BacktestConfig
from .pip_utils import price_diff_to_pips


def calculate_lot_size(
    config: BacktestConfig,
    entry_price: float,
    sl_price: float,
    balance: float,
) -> float:
    """
    Calculate the position lot size.

    fixed_lot:    returns config.fixed_lot directly.
    risk_percent: derives lot size from the current balance, risk %, and SL distance.
                  lot = (balance * risk%) / (sl_pips * pip_value_per_lot)
    """
    if config.sizing_mode == "fixed_lot":
        if config.fixed_lot is None:
            raise ValueError("fixed_lot must be set when sizing_mode is 'fixed_lot'")
        return config.fixed_lot

    # risk_percent mode
    if config.risk_percent is None:
        raise ValueError("risk_percent must be set when sizing_mode is 'risk_percent'")

    sl_pips = price_diff_to_pips(entry_price - sl_price, config.instrument.pip_size)
    if sl_pips <= 0:
        raise ValueError(f"SL distance must be > 0 pips, got {sl_pips:.2f}")

    risk_amount = balance * (config.risk_percent / 100.0)
    lot_size = risk_amount / (sl_pips * config.instrument.pip_value_per_lot)
    return round(max(lot_size, 0.01), 2)  # minimum 0.01 lot, 2 d.p. precision
