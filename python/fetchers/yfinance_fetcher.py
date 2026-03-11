"""yfinance data fetcher for stocks, ETFs, and indices.

Fetches daily OHLCV data using adjusted close prices.
Supports intervals: 1d, 1wk, 1mo.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from config import FETCH_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# Valid yfinance intervals for this fetcher
VALID_INTERVALS = {"1d", "1wk", "1mo"}


def fetch_yfinance(
    symbol: str,
    date_from: date,
    date_to: date,
    interval: str = "1d",
) -> pd.DataFrame:
    """
    Fetch OHLCV data from Yahoo Finance.

    Uses adjusted close prices. Volume is included as reported by Yahoo.

    Args:
        symbol: Ticker symbol (e.g., "SPY", "AAPL", "^GSPC")
        date_from: Start date (inclusive)
        date_to: End date (inclusive)
        interval: Data interval — "1d", "1wk", or "1mo"

    Returns:
        pandas DataFrame with columns: datetime, open, high, low, close, volume
        datetime is timezone-aware UTC.

    Raises:
        ValueError: If the interval is invalid, symbol is not found, or no data.
    """
    if interval not in VALID_INTERVALS:
        raise ValueError(
            f"Invalid interval '{interval}' for yfinance. "
            f"Supported intervals: {', '.join(sorted(VALID_INTERVALS))}"
        )

    logger.info(f"Fetching yfinance data: {symbol} from {date_from} to {date_to} ({interval})")

    ticker = yf.Ticker(symbol)

    # yfinance end date is exclusive, so add 1 day
    end_date = date_to + timedelta(days=1)

    def _do_fetch() -> pd.DataFrame:
        return ticker.history(
            start=date_from.isoformat(),
            end=end_date.isoformat(),
            interval=interval,
            auto_adjust=True,  # Use adjusted prices
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_do_fetch)
            df = future.result(timeout=FETCH_TIMEOUT_SECONDS)
    except FuturesTimeoutError:
        raise TimeoutError(
            f"Fetch timed out after {FETCH_TIMEOUT_SECONDS} seconds"
        )
    except TimeoutError:
        raise
    except Exception as e:
        raise ValueError(f"Failed to fetch data from Yahoo Finance for {symbol}: {e}")

    if df.empty:
        raise ValueError(
            f"No data returned from Yahoo Finance for {symbol} "
            f"between {date_from} and {date_to}. "
            f"The ticker may be invalid or the date range may have no trading data."
        )

    # Normalize column names to lowercase
    df.columns = [col.lower() for col in df.columns]

    # Ensure we have the expected columns
    required_cols = {"open", "high", "low", "close", "volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Missing expected columns from yfinance: {missing}")

    # Select only OHLCV columns
    ohlcv = df[["open", "high", "low", "close", "volume"]].copy()

    # Make datetime index UTC-aware
    if ohlcv.index.tz is None:
        ohlcv.index = ohlcv.index.tz_localize("UTC")
    else:
        ohlcv.index = ohlcv.index.tz_convert("UTC")

    # Remove duplicate timestamps
    ohlcv = ohlcv[~ohlcv.index.duplicated(keep="first")]

    # Sort by datetime
    ohlcv = ohlcv.sort_index()

    # Reset index to have datetime as a column
    ohlcv = ohlcv.reset_index()
    ohlcv = ohlcv.rename(columns={"Date": "datetime", "Datetime": "datetime", "index": "datetime"})

    # Ensure the column is named 'datetime'
    if "datetime" not in ohlcv.columns:
        # The index name might vary — use the first column if it looks like a date
        first_col = ohlcv.columns[0]
        if pd.api.types.is_datetime64_any_dtype(ohlcv[first_col]):
            ohlcv = ohlcv.rename(columns={first_col: "datetime"})

    logger.info(f"Fetched {len(ohlcv)} bars for {symbol} ({interval})")

    return ohlcv
