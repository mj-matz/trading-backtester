"""Abstract base class for all trading strategies."""

from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    """
    Abstract base strategy that all concrete strategies must implement.

    Strategies are pure signal generators: they accept OHLCV data and
    configuration parameters, and return a signals DataFrame compatible
    with the backtesting engine (PROJ-2).

    Required output columns (all float, NaN = no signal):
        long_entry, long_sl, long_tp,
        short_entry, short_sl, short_tp,
        signal_expiry  (pd.Timestamp or NaT)
    """

    @abstractmethod
    def validate_params(self, params) -> None:
        """
        Validate strategy parameters.

        Raises ValueError if any parameter is invalid.
        """

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame, params) -> tuple[pd.DataFrame, list]:
        """
        Generate trading signals from OHLCV data.

        Parameters
        ----------
        df : pd.DataFrame
            OHLCV data with a UTC DatetimeIndex.
        params : strategy-specific parameter object

        Returns
        -------
        tuple[pd.DataFrame, list]
            - DataFrame: Same index as df. Columns:
                long_entry, long_sl, long_tp,
                short_entry, short_sl, short_tp,
                signal_expiry
              All float columns are NaN where no signal exists.
              signal_expiry is NaT where no signal exists.
            - list: Skipped days with reason codes (strategy-specific dataclass).
        """
