"""Cache service for managing Parquet files and Supabase metadata.

Handles:
- Checking if data is already cached
- Saving DataFrames as Parquet files
- Inserting/updating metadata in the Supabase data_cache table
- Deleting cache entries (both file and DB row)
"""

import logging
import os
from datetime import date
from pathlib import Path
from typing import Optional

import pandas as pd
from supabase import create_client, Client

from config import SUPABASE_URL, SUPABASE_SERVICE_KEY, DATA_DIR

logger = logging.getLogger(__name__)


def _get_supabase_client() -> Client:
    """Create a Supabase client using the service role key."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in environment variables"
        )
    return create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def _build_parquet_path(
    source: str, symbol: str, timeframe: str, date_from: date, date_to: date
) -> Path:
    """Build the Parquet file path following the naming convention."""
    return (
        DATA_DIR
        / "parquet"
        / source
        / symbol.upper()
        / timeframe
        / f"{date_from.isoformat()}_{date_to.isoformat()}.parquet"
    )


def find_cached_entry(
    symbol: str, source: str, timeframe: str, date_from: date, date_to: date
) -> Optional[dict]:
    """
    Check if a matching cache entry exists in Supabase.

    Returns the cache entry dict if found, or None.
    """
    client = _get_supabase_client()
    result = (
        client.table("data_cache")
        .select("*")
        .eq("symbol", symbol.upper())
        .eq("source", source)
        .eq("timeframe", timeframe)
        .lte("date_from", date_from.isoformat())
        .gte("date_to", date_to.isoformat())
        .limit(1)
        .execute()
    )

    if result.data and len(result.data) > 0:
        entry = result.data[0]
        # Verify the file still exists on disk
        if Path(entry["file_path"]).exists():
            logger.info(f"Cache hit for {symbol}/{source}/{timeframe}")
            return entry
        else:
            logger.warning(
                f"Cache entry found but file missing: {entry['file_path']}. "
                f"Treating as cache miss."
            )
            # Clean up the stale DB entry
            client.table("data_cache").delete().eq("id", entry["id"]).execute()

    return None


def load_cached_data(file_path: str) -> pd.DataFrame:
    """Load a cached Parquet file into a DataFrame."""
    logger.info(f"Loading cached data from {file_path}")
    return pd.read_parquet(file_path)


def save_to_cache(
    df: pd.DataFrame,
    symbol: str,
    source: str,
    timeframe: str,
    date_from: date,
    date_to: date,
    created_by: str,
) -> dict:
    """
    Save a DataFrame as a Parquet file and record metadata in Supabase.

    Args:
        df: OHLCV DataFrame to cache
        symbol: Instrument symbol
        source: Data source (dukascopy/yfinance)
        timeframe: Data timeframe
        date_from: Start date
        date_to: End date
        created_by: User ID who triggered the fetch

    Returns:
        The created cache entry dict from Supabase.
    """
    file_path = _build_parquet_path(source, symbol, timeframe, date_from, date_to)

    # Ensure directory exists
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # Save as Parquet
    df.to_parquet(str(file_path), index=False, engine="pyarrow")
    file_size = os.path.getsize(file_path)

    logger.info(
        f"Saved {len(df)} rows to {file_path} ({file_size} bytes)"
    )

    # Insert metadata into Supabase
    client = _get_supabase_client()
    entry = {
        "symbol": symbol.upper(),
        "source": source,
        "timeframe": timeframe,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "file_path": str(file_path),
        "file_size_bytes": file_size,
        "row_count": len(df),
        "created_by": created_by,
    }

    result = client.table("data_cache").insert(entry).execute()

    if result.data and len(result.data) > 0:
        logger.info(f"Cache entry created: {result.data[0]['id']}")
        return result.data[0]

    raise RuntimeError("Failed to insert cache entry into Supabase")


def delete_cache_entry(cache_id: str) -> bool:
    """
    Delete a cache entry: remove the Parquet file and the DB row.

    Args:
        cache_id: UUID of the data_cache row.

    Returns:
        True if deletion was successful.
    """
    client = _get_supabase_client()

    # First, look up the entry to find the file path
    result = client.table("data_cache").select("file_path").eq("id", cache_id).limit(1).execute()

    if not result.data or len(result.data) == 0:
        logger.warning(f"Cache entry not found: {cache_id}")
        return False

    file_path = Path(result.data[0]["file_path"])
    if file_path.exists():
        file_path.unlink()
        logger.info(f"Deleted Parquet file: {file_path}")

        # Clean up empty parent directories
        try:
            for parent in [file_path.parent, file_path.parent.parent, file_path.parent.parent.parent]:
                if parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
        except OSError:
            pass  # Directory not empty or other OS error, that's fine

    # Delete from Supabase
    client.table("data_cache").delete().eq("id", cache_id).execute()
    logger.info(f"Deleted cache entry: {cache_id}")

    return True
