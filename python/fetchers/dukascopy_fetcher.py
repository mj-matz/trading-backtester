"""Dukascopy data fetcher using the duka library.

Fetches tick data from Dukascopy and converts it to OHLCV DataFrames.
Supports intraday timeframes: 1m (tick), 5m, 15m, 1h, 4h, 1d.
"""

import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from config import FETCH_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# Mapping of common symbol names to duka-compatible tickers
# Includes Forex majors, indices, commodities, and metals
DUKASCOPY_SYMBOLS = {
    # Forex Majors
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
    "USDCHF": "USDCHF",
    "USDJPY": "USDJPY",
    "AUDUSD": "AUDUSD",
    "NZDUSD": "NZDUSD",
    "USDCAD": "USDCAD",
    # Forex Crosses
    "EURGBP": "EURGBP",
    "EURJPY": "EURJPY",
    "GBPJPY": "GBPJPY",
    "EURAUD": "EURAUD",
    "EURCHF": "EURCHF",
    "GBPCHF": "GBPCHF",
    "AUDCAD": "AUDCAD",
    "AUDJPY": "AUDJPY",
    "CADJPY": "CADJPY",
    "CHFJPY": "CHFJPY",
    "NZDJPY": "NZDJPY",
    # Indices
    "GER30": "DEUIDXEUR",
    "GER40": "DEUIDXEUR",
    "DAX": "DEUIDXEUR",
    "US30": "USA30IDXUSD",
    "US500": "USA500IDXUSD",
    "SPX500": "USA500IDXUSD",
    "NAS100": "USATECHIDXUSD",
    "USTEC": "USATECHIDXUSD",
    "UK100": "GBRIDXGBP",
    "FTSE100": "GBRIDXGBP",
    "FRA40": "FRAIDXEUR",
    "JPN225": "JPNIDXJPY",
    "AUS200": "AUSIDXAUD",
    # Precious Metals
    "XAUUSD": "XAUUSD",
    "GOLD": "XAUUSD",
    "XAGUSD": "XAGUSD",
    "SILVER": "XAGUSD",
    "XPTUSD": "XPTUSD",
    "PLATINUM": "XPTUSD",
    "XPDUSD": "XPDUSD",
    "PALLADIUM": "XPDUSD",
    # Commodities — Energy
    "WTIUSD": "LIGHTCMDUSD",
    "CRUDEOIL": "LIGHTCMDUSD",
    "WTI": "LIGHTCMDUSD",
    "BRENTUSD": "BRENTCMDUSD",
    "BRENT": "BRENTCMDUSD",
    "NATGASUSD": "NATGASCMDUSD",
    "NATGAS": "NATGASCMDUSD",
    # Commodities — Agricultural
    "CORNUSD": "CORNCMDUSX",
    "CORN": "CORNCMDUSX",
    "SOYBEANUSD": "SOYBEANCMDUSX",
    "SOYBEAN": "SOYBEANCMDUSX",
    "WHEATUSD": "WHEATCMDUSX",
    "WHEAT": "WHEATCMDUSX",
    # Industrial Metals
    "COPPERUSD": "COPPERCMDUSD",
    "COPPER": "COPPERCMDUSD",
}


def resolve_symbol(symbol: str) -> str:
    """Resolve a user-friendly symbol name to a Dukascopy ticker."""
    upper = symbol.upper()
    if upper in DUKASCOPY_SYMBOLS:
        return DUKASCOPY_SYMBOLS[upper]
    # If not in our mapping, try the symbol as-is
    return upper


def get_supported_symbols() -> dict[str, str]:
    """Return the full mapping of supported symbol aliases."""
    return dict(DUKASCOPY_SYMBOLS)


def fetch_dukascopy(
    symbol: str,
    date_from: date,
    date_to: date,
) -> pd.DataFrame:
    """
    Fetch tick data from Dukascopy and return a 1-minute OHLCV DataFrame.

    The duka library downloads tick data which we then resample to 1-minute bars
    as the base resolution. Higher timeframes are created by the resampler service.

    Args:
        symbol: Instrument symbol (e.g., "XAUUSD", "GER40", "EURUSD", "BRENT")
        date_from: Start date (inclusive)
        date_to: End date (inclusive)

    Returns:
        pandas DataFrame with columns: datetime, open, high, low, close, volume
        datetime is timezone-aware UTC.

    Raises:
        ValueError: If the symbol is not supported or no data is returned.
    """
    from duka.app import app as duka_app
    from duka.core.utils import TimeFrame

    duka_symbol = resolve_symbol(symbol)
    start_dt = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    end_dt = datetime(date_to.year, date_to.month, date_to.day, 23, 59, 59, tzinfo=timezone.utc)

    logger.info(f"Fetching Dukascopy data: {duka_symbol} from {date_from} to {date_to}")

    # duka writes CSV files to a directory. We use a temp directory.
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    duka_app,
                    duka_symbol,
                    start_dt,
                    end_dt,
                    1,  # threads
                    TimeFrame.TICK,
                    tmpdir,
                    True,  # header
                )
                future.result(timeout=FETCH_TIMEOUT_SECONDS)
        except FuturesTimeoutError:
            raise TimeoutError(
                f"Fetch timed out after {FETCH_TIMEOUT_SECONDS} seconds"
            )
        except TimeoutError:
            raise
        except Exception as e:
            raise ValueError(f"Failed to fetch data from Dukascopy for {duka_symbol}: {e}")

        # Find the generated CSV file
        csv_files = list(Path(tmpdir).glob("*.csv"))
        if not csv_files:
            raise ValueError(
                f"No data returned from Dukascopy for {symbol} ({duka_symbol}) "
                f"between {date_from} and {date_to}. "
                f"The symbol may be unsupported or the date range may have no trading data."
            )

        # Read tick data — duka CSV format: time, ask, bid, ask_volume, bid_volume
        all_frames = []
        for csv_file in csv_files:
            try:
                df = pd.read_csv(
                    csv_file,
                    names=["datetime", "ask", "bid", "ask_volume", "bid_volume"],
                    parse_dates=["datetime"],
                    header=0,
                )
                all_frames.append(df)
            except Exception as e:
                logger.warning(f"Failed to parse CSV {csv_file}: {e}")

        if not all_frames:
            raise ValueError(f"Failed to parse any tick data files for {symbol}")

        ticks = pd.concat(all_frames, ignore_index=True)

    if ticks.empty:
        raise ValueError(f"No tick data available for {symbol} in the requested range")

    # Convert to UTC-aware datetime
    ticks["datetime"] = pd.to_datetime(ticks["datetime"], utc=True)

    # Calculate mid price for OHLCV
    ticks["price"] = (ticks["ask"] + ticks["bid"]) / 2
    ticks["volume"] = ticks["ask_volume"] + ticks["bid_volume"]

    # Set datetime as index for resampling
    ticks = ticks.set_index("datetime").sort_index()

    # Remove duplicates
    ticks = ticks[~ticks.index.duplicated(keep="first")]

    # Resample to 1-minute OHLCV bars
    ohlcv = pd.DataFrame()
    ohlcv["open"] = ticks["price"].resample("1min").first()
    ohlcv["high"] = ticks["price"].resample("1min").max()
    ohlcv["low"] = ticks["price"].resample("1min").min()
    ohlcv["close"] = ticks["price"].resample("1min").last()
    ohlcv["volume"] = ticks["volume"].resample("1min").sum()

    # Drop rows with no trading activity (weekends, holidays)
    ohlcv = ohlcv.dropna(subset=["open"])

    # Reset index so datetime is a column
    ohlcv = ohlcv.reset_index()
    ohlcv = ohlcv.rename(columns={"index": "datetime"} if "index" in ohlcv.columns else {})

    # Ensure datetime column name
    if "datetime" not in ohlcv.columns and ohlcv.index.name == "datetime":
        ohlcv = ohlcv.reset_index()

    logger.info(f"Fetched {len(ohlcv)} 1-minute bars for {symbol}")

    return ohlcv
