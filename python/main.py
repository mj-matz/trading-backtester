"""FastAPI service for the Data Fetcher (PROJ-1).

Provides endpoints for fetching, caching, and serving historical OHLCV data
from Dukascopy (intraday) and Yahoo Finance (daily).
"""

import logging
from datetime import date

import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import FetchRequest, FetchResponse, ErrorResponse
from fetchers.dukascopy_fetcher import fetch_dukascopy
from fetchers.yfinance_fetcher import fetch_yfinance, VALID_INTERVALS as YFINANCE_INTERVALS
from services.auth import verify_jwt
from services.cache_service import find_cached_entry, load_cached_data, save_to_cache, delete_cache_entry
from services.resampler import resample_ohlcv, TIMEFRAME_TO_RULE

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Data Fetcher Service",
    description="Fetches and caches historical OHLCV data from Dukascopy and yfinance.",
    version="1.0.0",
)

# CORS — only allow Next.js frontend (the proxy routes) to communicate
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)


# Valid timeframes per source
DUKASCOPY_TIMEFRAMES = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}


def _validate_timeframe(source: str, timeframe: str) -> None:
    """Validate that the timeframe is supported for the given source."""
    if source == "dukascopy" and timeframe not in DUKASCOPY_TIMEFRAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}' for Dukascopy. "
            f"Supported: {', '.join(sorted(DUKASCOPY_TIMEFRAMES))}",
        )
    if source == "yfinance" and timeframe not in YFINANCE_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid timeframe '{timeframe}' for yfinance. "
            f"Supported: {', '.join(sorted(YFINANCE_INTERVALS))}",
        )


def _validate_date_range(date_from: date, date_to: date) -> None:
    """Validate that the date range is sensible."""
    if date_from >= date_to:
        raise HTTPException(status_code=400, detail="date_from must be before date_to")
    if (date_to - date_from).days > 365 * 5:
        raise HTTPException(status_code=400, detail="Date range cannot exceed 5 years")


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "data-fetcher"}


@app.post(
    "/fetch",
    response_model=FetchResponse,
    responses={400: {"model": ErrorResponse}, 502: {"model": ErrorResponse}, 504: {"model": ErrorResponse}},
)
async def fetch_data(
    request: FetchRequest,
    token: dict = Depends(verify_jwt),
):
    """
    Fetch OHLCV data for a given symbol, source, and timeframe.

    Checks cache first. On cache miss, downloads from the source,
    saves to cache, and returns the data.
    """
    user_id: str = token["sub"]  # verified user UUID from JWT

    symbol = request.symbol.upper()
    source = request.source
    timeframe = request.timeframe
    date_from = request.date_from
    date_to = request.date_to

    _validate_timeframe(source, timeframe)
    _validate_date_range(date_from, date_to)

    # Check cache (unless force_refresh)
    if not request.force_refresh:
        cached = find_cached_entry(symbol, source, timeframe, date_from, date_to)
        if cached:
            df = load_cached_data(cached["file_path"])

            # Determine actual date range from cached data
            cached_warnings: list[str] = []
            cached_actual_from: date | None = None
            cached_actual_to: date | None = None
            if "datetime" in df.columns and not df.empty:
                dt_col = pd.to_datetime(df["datetime"], utc=True)
                cached_actual_from = dt_col.min().date()
                cached_actual_to = dt_col.max().date()
                if cached_actual_from > date_from or cached_actual_to < date_to:
                    cached_warnings.append(
                        f"Data available from {cached_actual_from} to {cached_actual_to}, "
                        f"requested {date_from} to {date_to}"
                    )

            return FetchResponse(
                symbol=symbol,
                source=source,
                timeframe=timeframe,
                date_from=date_from,
                date_to=date_to,
                row_count=len(df),
                file_path=cached["file_path"],
                file_size_bytes=cached["file_size_bytes"],
                cache_id=cached["id"],
                cached=True,
                columns=list(df.columns),
                preview=df.head(5).to_dict(orient="records"),
                actual_date_from=cached_actual_from,
                actual_date_to=cached_actual_to,
                warnings=cached_warnings,
            )

    # Fetch from source
    try:
        if source == "dukascopy":
            # Always fetch 1m data first, then resample if needed
            base_df = fetch_dukascopy(symbol, date_from, date_to)
            if timeframe != "1m":
                df = resample_ohlcv(base_df, timeframe)
            else:
                df = base_df
        elif source == "yfinance":
            # yfinance supports 1d/1wk/1mo directly
            df = fetch_yfinance(symbol, date_from, date_to, interval=timeframe)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown source: {source}")
    except TimeoutError as e:
        raise HTTPException(status_code=504, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected fetch error: {e}", exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch data from {source}: {str(e)}",
        )

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data available for {symbol} from {source} between {date_from} and {date_to}",
        )

    # Determine actual date range from the DataFrame
    warnings: list[str] = []
    actual_date_from: date | None = None
    actual_date_to: date | None = None
    if "datetime" in df.columns and not df.empty:
        dt_col = pd.to_datetime(df["datetime"], utc=True)
        actual_date_from = dt_col.min().date()
        actual_date_to = dt_col.max().date()
        if actual_date_from > date_from or actual_date_to < date_to:
            warnings.append(
                f"Data available from {actual_date_from} to {actual_date_to}, "
                f"requested {date_from} to {date_to}"
            )

    # Save to cache
    try:
        cache_entry = save_to_cache(
            df=df,
            symbol=symbol,
            source=source,
            timeframe=timeframe,
            date_from=date_from,
            date_to=date_to,
            created_by=user_id,
        )
    except Exception as e:
        logger.error(f"Cache save error: {e}", exc_info=True)
        # Return data even if caching fails
        return FetchResponse(
            symbol=symbol,
            source=source,
            timeframe=timeframe,
            date_from=date_from,
            date_to=date_to,
            row_count=len(df),
            file_path="",
            file_size_bytes=0,
            cached=False,
            columns=list(df.columns),
            preview=df.head(5).to_dict(orient="records"),
            actual_date_from=actual_date_from,
            actual_date_to=actual_date_to,
            warnings=warnings,
        )

    return FetchResponse(
        symbol=symbol,
        source=source,
        timeframe=timeframe,
        date_from=date_from,
        date_to=date_to,
        row_count=len(df),
        file_path=cache_entry["file_path"],
        file_size_bytes=cache_entry["file_size_bytes"],
        cache_id=cache_entry["id"],
        cached=False,
        columns=list(df.columns),
        preview=df.head(5).to_dict(orient="records"),
        actual_date_from=actual_date_from,
        actual_date_to=actual_date_to,
        warnings=warnings,
    )


@app.delete(
    "/cache/{cache_id}",
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def delete_cache(
    cache_id: str,
    token: dict = Depends(verify_jwt),
):
    """
    Delete a cached data entry (Parquet file + DB metadata).

    Requires a valid JWT with app_metadata.is_admin = true.
    """
    is_admin = token.get("app_metadata", {}).get("is_admin") is True
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    try:
        success = delete_cache_entry(cache_id)
    except Exception as e:
        logger.error(f"Cache delete error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to delete cache entry: {str(e)}")

    if not success:
        raise HTTPException(status_code=404, detail=f"Cache entry {cache_id} not found")

    return {"success": True, "deleted_id": cache_id}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
