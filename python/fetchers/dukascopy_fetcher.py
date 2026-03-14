"""Direct Dukascopy data fetcher — replaces the broken duka==0.2.0 library.

Downloads .bi5 tick data directly from Dukascopy's public datafeed API:
  https://datafeed.dukascopy.com/datafeed/{SYMBOL}/{YEAR}/{MONTH-1:02d}/{DAY:02d}/{HOUR:02d}h_ticks.bi5

Each .bi5 file is LZMA-compressed binary data. Each tick is 20 bytes:
  - uint32 big-endian: milliseconds from start of the hour
  - uint32 big-endian: ask price (raw integer, divide by POINT_VALUE)
  - uint32 big-endian: bid price (raw integer, divide by POINT_VALUE)
  - float32 big-endian: ask volume
  - float32 big-endian: bid volume
"""

import lzma
import struct
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd

from config import FETCH_TIMEOUT_SECONDS

logger = logging.getLogger(__name__)

# ── Symbol mapping ────────────────────────────────────────────────────────────

DUKASCOPY_SYMBOLS: dict[str, str] = {
    # Forex Majors
    "EURUSD": "EURUSD", "GBPUSD": "GBPUSD", "USDCHF": "USDCHF",
    "USDJPY": "USDJPY", "AUDUSD": "AUDUSD", "NZDUSD": "NZDUSD", "USDCAD": "USDCAD",
    # Forex Crosses
    "EURGBP": "EURGBP", "EURJPY": "EURJPY", "GBPJPY": "GBPJPY",
    "EURAUD": "EURAUD", "EURCHF": "EURCHF", "GBPCHF": "GBPCHF",
    "AUDCAD": "AUDCAD", "AUDJPY": "AUDJPY", "CADJPY": "CADJPY",
    "CHFJPY": "CHFJPY", "NZDJPY": "NZDJPY",
    # Indices
    "GER30": "DEUIDXEUR", "GER40": "DEUIDXEUR", "DAX": "DEUIDXEUR",
    "US30": "USA30IDXUSD", "US500": "USA500IDXUSD", "SPX500": "USA500IDXUSD",
    "NAS100": "USATECHIDXUSD", "USTEC": "USATECHIDXUSD",
    "UK100": "GBRIDXGBP", "FTSE100": "GBRIDXGBP",
    "FRA40": "FRAIDXEUR", "JPN225": "JPNIDXJPY", "AUS200": "AUSIDXAUD",
    # Precious Metals
    "XAUUSD": "XAUUSD", "GOLD": "XAUUSD",
    "XAGUSD": "XAGUSD", "SILVER": "XAGUSD",
    "XPTUSD": "XPTUSD", "PLATINUM": "XPTUSD",
    "XPDUSD": "XPDUSD", "PALLADIUM": "XPDUSD",
    # Energy
    "WTIUSD": "LIGHTCMDUSD", "CRUDEOIL": "LIGHTCMDUSD", "WTI": "LIGHTCMDUSD",
    "BRENTUSD": "BRENTCMDUSD", "BRENT": "BRENTCMDUSD",
    "NATGASUSD": "NATGASCMDUSD", "NATGAS": "NATGASCMDUSD",
    # Agricultural
    "CORNUSD": "CORNCMDUSX", "CORN": "CORNCMDUSX",
    "SOYBEANUSD": "SOYBEANCMDUSX", "SOYBEAN": "SOYBEANCMDUSX",
    "WHEATUSD": "WHEATCMDUSX", "WHEAT": "WHEATCMDUSX",
    # Industrial Metals
    "COPPERUSD": "COPPERCMDUSD", "COPPER": "COPPERCMDUSD",
}

# Raw price divisor: actual_price = raw_integer / POINT_VALUE[duka_symbol]
# Determined by the number of decimal places Dukascopy encodes for each instrument.
POINT_VALUES: dict[str, int] = {
    # Standard Forex — 5 decimal places (e.g. 1.08234 → raw 108234 / 100000)
    "EURUSD": 100000, "GBPUSD": 100000, "USDCHF": 100000,
    "AUDUSD": 100000, "NZDUSD": 100000, "USDCAD": 100000,
    "EURGBP": 100000, "EURAUD": 100000, "EURCHF": 100000,
    "GBPCHF": 100000, "AUDCAD": 100000,
    # JPY pairs — 3 decimal places (e.g. 150.123 → raw 150123 / 1000)
    "USDJPY": 1000, "EURJPY": 1000, "GBPJPY": 1000,
    "AUDJPY": 1000, "CADJPY": 1000, "CHFJPY": 1000, "NZDJPY": 1000,
    # Metals — 2 decimal places (e.g. XAUUSD 2300.12 → raw 230012 / 100)
    "XAUUSD": 100, "XAGUSD": 1000, "XPTUSD": 100, "XPDUSD": 100,
    # Energy — 3 decimal places
    "LIGHTCMDUSD": 1000, "BRENTCMDUSD": 1000, "NATGASCMDUSD": 10000,
    # Indices — 1 decimal place (e.g. DAX 18000.1 → raw 180001 / 10)
    "DEUIDXEUR": 10, "USA30IDXUSD": 10, "USA500IDXUSD": 10,
    "USATECHIDXUSD": 10, "GBRIDXGBP": 10, "FRAIDXEUR": 10,
    "JPNIDXJPY": 10, "AUSIDXAUD": 10,
    # Agricultural
    "CORNCMDUSX": 10000, "SOYBEANCMDUSX": 10000, "WHEATCMDUSX": 10000,
    # Industrial
    "COPPERCMDUSD": 100000,
}

_BASE_URL = "https://datafeed.dukascopy.com/datafeed"

# Binary format per tick: ms_uint32, ask_uint32, bid_uint32, ask_float32, bid_float32
_TICK_FMT = ">IIIff"
_TICK_SIZE = struct.calcsize(_TICK_FMT)  # 20 bytes


def resolve_symbol(symbol: str) -> str:
    """Resolve a user-friendly symbol to a Dukascopy ticker."""
    return DUKASCOPY_SYMBOLS.get(symbol.upper(), symbol.upper())


def get_supported_symbols() -> dict[str, str]:
    return dict(DUKASCOPY_SYMBOLS)


def _hour_url(duka_symbol: str, dt: datetime) -> str:
    """Build the .bi5 download URL for one hour. Month is 0-indexed in Dukascopy URLs."""
    return (
        f"{_BASE_URL}/{duka_symbol}/"
        f"{dt.year}/{dt.month - 1:02d}/{dt.day:02d}/"
        f"{dt.hour:02d}h_ticks.bi5"
    )


def _download_hour(
    duka_symbol: str,
    dt: datetime,
    point: int,
    client: httpx.Client,
) -> Optional[pd.DataFrame]:
    """Download and decode one hour of tick data. Returns None when no data."""
    url = _hour_url(duka_symbol, dt)
    try:
        resp = client.get(url, timeout=20)
        if resp.status_code == 404 or len(resp.content) == 0:
            return None  # Normal: weekend / holiday / no trading that hour
        if resp.status_code != 200:
            logger.debug("HTTP %d for %s", resp.status_code, url)
            return None

        raw = lzma.decompress(resp.content)
        n = len(raw) // _TICK_SIZE
        if n == 0:
            return None

        hour_ms = int(dt.timestamp() * 1000)
        rows = []
        for i in range(n):
            ms, ask_raw, bid_raw, ask_vol, bid_vol = struct.unpack_from(
                _TICK_FMT, raw, i * _TICK_SIZE
            )
            rows.append(
                {
                    "datetime": pd.Timestamp(hour_ms + ms, unit="ms", tz="UTC"),
                    "ask": ask_raw / point,
                    "bid": bid_raw / point,
                    "ask_volume": float(ask_vol),
                    "bid_volume": float(bid_vol),
                }
            )
        return pd.DataFrame(rows)

    except lzma.LZMAError:
        return None  # Corrupt or truncated file — skip
    except Exception as exc:
        logger.debug("Error for %s: %s", url, exc)
        return None


def fetch_dukascopy(
    symbol: str,
    date_from: date,
    date_to: date,
) -> pd.DataFrame:
    """
    Fetch tick data from Dukascopy and return a 1-minute OHLCV DataFrame.

    Args:
        symbol:    Instrument symbol (e.g. "XAUUSD", "EURUSD", "GER40")
        date_from: Start date (inclusive)
        date_to:   End date (inclusive)

    Returns:
        DataFrame with columns: datetime (UTC), open, high, low, close, volume

    Raises:
        ValueError: No data found or symbol unsupported.
        TimeoutError: Download exceeded FETCH_TIMEOUT_SECONDS.
    """
    duka_symbol = resolve_symbol(symbol)
    point = POINT_VALUES.get(duka_symbol, 100000)

    # Generate all hours in [date_from, date_to] inclusive
    start = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
    end = datetime(date_to.year, date_to.month, date_to.day, 23, tzinfo=timezone.utc)
    hours = []
    cur = start
    while cur <= end:
        hours.append(cur)
        cur += timedelta(hours=1)

    logger.info(
        "Downloading %d hours of %s (%s) from Dukascopy",
        len(hours),
        symbol,
        duka_symbol,
    )

    frames: list[pd.DataFrame] = []
    with httpx.Client(follow_redirects=True) as client:
        with ThreadPoolExecutor(max_workers=12) as executor:
            future_map = {
                executor.submit(_download_hour, duka_symbol, h, point, client): h
                for h in hours
            }
            for future in as_completed(future_map, timeout=FETCH_TIMEOUT_SECONDS):
                result = future.result()
                if result is not None:
                    frames.append(result)

    if not frames:
        raise ValueError(
            f"No data returned from Dukascopy for {symbol} ({duka_symbol}) "
            f"between {date_from} and {date_to}. "
            "The symbol may be unsupported or the date range may have no trading data."
        )

    ticks = pd.concat(frames, ignore_index=True).sort_values("datetime")

    # Mid price
    ticks["price"] = (ticks["ask"] + ticks["bid"]) / 2
    ticks["volume"] = ticks["ask_volume"] + ticks["bid_volume"]

    ticks = ticks.set_index("datetime")
    ticks = ticks[~ticks.index.duplicated(keep="first")]

    # Resample to 1-minute OHLCV
    ohlcv = pd.DataFrame(
        {
            "open": ticks["price"].resample("1min").first(),
            "high": ticks["price"].resample("1min").max(),
            "low": ticks["price"].resample("1min").min(),
            "close": ticks["price"].resample("1min").last(),
            "volume": ticks["volume"].resample("1min").sum(),
        }
    )
    ohlcv = ohlcv.dropna(subset=["open"]).reset_index()

    logger.info("Fetched %d 1-minute bars for %s", len(ohlcv), symbol)
    return ohlcv
