"""FastAPI service for the Data Fetcher (PROJ-1) and Backtesting Engine (PROJ-2).

Provides endpoints for fetching/caching historical OHLCV data and running
backtests against cached data sets.
"""

import logging
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import List, Literal, Optional

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from models import FetchRequest, FetchResponse, ErrorResponse
from fetchers.dukascopy_fetcher import fetch_dukascopy
from fetchers.yfinance_fetcher import fetch_yfinance, VALID_INTERVALS as YFINANCE_INTERVALS
from services.auth import verify_jwt
from services.cache_service import find_cached_entry, load_cached_data, save_to_cache, delete_cache_entry
from services.resampler import resample_ohlcv, TIMEFRAME_TO_RULE
from engine import run_backtest
from engine.models import BacktestConfig, InstrumentConfig
from analytics import calculate_analytics
from analytics.trade_metrics import r_multiple as compute_r_multiple

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


# ── Backtest rate limiter (in-memory, per user, sliding 1-minute window) ────
_rl_lock = threading.Lock()
_rl_timestamps: dict = defaultdict(list)
BACKTEST_RATE_LIMIT = 30  # requests per minute


def _check_backtest_rate_limit(user_id: str) -> bool:
    """Return True if the request is within the rate limit, False if exceeded."""
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(minutes=1)
    with _rl_lock:
        times = _rl_timestamps[user_id]
        times[:] = [t for t in times if t > window_start]
        if len(times) >= BACKTEST_RATE_LIMIT:
            return False
        times.append(now)
        return True


# ── Backtest request / response models ──────────────────────────────────────

class InstrumentConfigRequest(BaseModel):
    pip_size: float = Field(gt=0)
    pip_value_per_lot: float = Field(gt=0)


class BacktestConfigRequest(BaseModel):
    initial_balance: float = Field(gt=0)
    sizing_mode: Literal["fixed_lot", "risk_percent"]
    instrument: InstrumentConfigRequest
    fixed_lot: Optional[float] = Field(default=None, gt=0)
    risk_percent: Optional[float] = Field(default=None, gt=0, le=100)
    commission: float = Field(default=0.0, ge=0)
    slippage_pips: float = Field(default=0.0, ge=0)
    time_exit: Optional[str] = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")  # "HH:MM" (BUG-10)
    timezone: str = "UTC"                                                                    # IANA timezone (BUG-7)
    trail_trigger_pips: Optional[float] = Field(default=None, gt=0)
    trail_lock_pips: Optional[float] = Field(default=None, gt=0)                            # BUG-9: was ge=0

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError:
            raise ValueError(f"Invalid IANA timezone: '{v}'")
        return v


class SignalEntry(BaseModel):
    """One bar's worth of entry signals.  All price fields are optional."""
    ts: str                               # ISO-8601 timestamp matching the OHLCV index
    long_entry: Optional[float] = None
    long_sl: Optional[float] = None
    long_tp: Optional[float] = None
    short_entry: Optional[float] = None
    short_sl: Optional[float] = None
    short_tp: Optional[float] = None
    signal_expiry: Optional[str] = None   # ISO-8601 timestamp; None = no expiry (BUG-8)
    trail_trigger_pips: Optional[float] = None  # per-signal override (BUG-8)
    trail_lock_pips: Optional[float] = None     # per-signal override (BUG-8)


class BacktestRunRequest(BaseModel):
    cache_id: str
    config: BacktestConfigRequest
    signals: List[SignalEntry] = Field(min_length=1, max_length=500_000)


class TradeResponse(BaseModel):
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    exit_reason: str
    direction: str
    lot_size: float
    pnl_pips: float
    pnl_currency: float
    initial_risk_pips: float
    initial_risk_currency: float
    r_multiple: Optional[float] = None


class MetricResponse(BaseModel):
    name: str
    value: Optional[float]  # None for undefined
    value_string: Optional[str] = None  # Set to "Infinity" when value is float('inf')
    unit: str
    note: Optional[str] = None


class MonthlyRResponse(BaseModel):
    month: str
    r_earned: Optional[float]
    trade_count: int


class AnalyticsResponse(BaseModel):
    summary: List[MetricResponse]
    monthly_r: List[MonthlyRResponse]


class BacktestRunResponse(BaseModel):
    trades: List[TradeResponse]
    equity_curve: List[dict]
    final_balance: float
    initial_balance: float
    analytics: Optional[AnalyticsResponse] = None


# ── /backtest/run endpoint ───────────────────────────────────────────────────

@app.post("/backtest/run", response_model=BacktestRunResponse)
async def backtest_run(
    request: BacktestRunRequest,
    token: dict = Depends(verify_jwt),
):
    """
    Run a backtest against a previously cached OHLCV dataset.

    - Resolves cache_id → file_path via Supabase data_cache.
    - Loads the Parquet file from disk.
    - Aligns provided signals with the OHLCV index.
    - Runs the backtesting engine and returns the full result.

    Rate limit: 30 requests / minute per user.
    Any authenticated user may call this endpoint.
    """
    user_id: str = token["sub"]

    if not _check_backtest_rate_limit(user_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: max 30 backtest requests per minute.",
        )

    # ── 1. Resolve cache_id → file_path ─────────────────────────────────────
    from services.cache_service import _get_supabase_client  # reuse existing helper

    try:
        client = _get_supabase_client()
        resp = (
            client.table("data_cache")
            .select("file_path")
            .eq("id", request.cache_id)
            .eq("created_by", user_id)
            .single()
            .execute()
        )
    except Exception as e:
        logger.error(f"Supabase lookup failed for cache_id={request.cache_id}: {e}")
        raise HTTPException(status_code=502, detail="Failed to query data cache.")

    if not resp.data:
        raise HTTPException(
            status_code=404, detail=f"cache_id '{request.cache_id}' not found."
        )

    file_path: str = resp.data["file_path"]

    # ── 2. Load OHLCV from Parquet ───────────────────────────────────────────
    try:
        df = load_cached_data(file_path)
    except FileNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=f"Parquet file for cache_id '{request.cache_id}' not found on disk.",
        )
    except Exception as e:
        logger.error(f"Parquet load error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load data.")

    # Normalise to DatetimeIndex (the Parquet stores datetime as a column)
    if "datetime" in df.columns:
        df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]

    required_cols = {"open", "high", "low", "close"}
    if not required_cols.issubset(set(df.columns)):
        raise HTTPException(
            status_code=400,
            detail=f"Cached data is missing required columns: {required_cols - set(df.columns)}",
        )

    # ── 3. Build signals DataFrame aligned to OHLCV index ───────────────────
    price_cols = ["long_entry", "long_sl", "long_tp", "short_entry", "short_sl", "short_tp",
                  "trail_trigger_pips", "trail_lock_pips"]
    signals_df = pd.DataFrame(np.nan, index=df.index, columns=price_cols + ["signal_expiry"], dtype=object)
    signals_df[price_cols] = signals_df[price_cols].astype(float)

    unmatched: List[str] = []
    for entry in request.signals:
        try:
            ts = pd.Timestamp(entry.ts).tz_convert("UTC")
        except Exception:
            raise HTTPException(
                status_code=400, detail=f"Invalid signal timestamp: '{entry.ts}'"
            )
        if ts not in signals_df.index:
            unmatched.append(entry.ts)
            continue
        for col in price_cols:
            val = getattr(entry, col, None)
            if val is not None:
                signals_df.at[ts, col] = val
        if entry.signal_expiry is not None:
            try:
                signals_df.at[ts, "signal_expiry"] = pd.Timestamp(entry.signal_expiry).tz_convert("UTC")
            except Exception:
                raise HTTPException(
                    status_code=400, detail=f"Invalid signal_expiry timestamp: '{entry.signal_expiry}'"
                )

    if unmatched:
        logger.warning(
            f"Backtest {request.cache_id}: {len(unmatched)} signal timestamps "
            f"did not match any OHLCV bar and were skipped."
        )

    # ── 4. Build engine config and run ──────────────────────────────────────
    cfg = request.config
    engine_config = BacktestConfig(
        initial_balance=cfg.initial_balance,
        sizing_mode=cfg.sizing_mode,
        instrument=InstrumentConfig(
            pip_size=cfg.instrument.pip_size,
            pip_value_per_lot=cfg.instrument.pip_value_per_lot,
        ),
        fixed_lot=cfg.fixed_lot,
        risk_percent=cfg.risk_percent,
        commission=cfg.commission,
        slippage_pips=cfg.slippage_pips,
        time_exit=cfg.time_exit,
        timezone=cfg.timezone,
        trail_trigger_pips=cfg.trail_trigger_pips,
        trail_lock_pips=cfg.trail_lock_pips,
    )

    try:
        result = run_backtest(df, signals_df, engine_config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Backtest engine error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal engine error.")

    # ── 5. Compute analytics ─────────────────────────────────────────────────
    try:
        analytics_result = calculate_analytics(result)
        analytics_out = AnalyticsResponse(
            summary=[
                MetricResponse(
                    name=m.name,
                    value=None if (m.value is None or m.value == float("inf")) else m.value,
                    value_string="Infinity" if m.value == float("inf") else None,
                    unit=m.unit,
                    note=m.note,
                )
                for m in analytics_result.summary
            ],
            monthly_r=[
                MonthlyRResponse(
                    month=mr.month,
                    r_earned=mr.r_earned,
                    trade_count=mr.trade_count,
                )
                for mr in analytics_result.monthly_r
            ],
        )
    except Exception as e:
        logger.error(f"Analytics calculation error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analytics calculation failed.")

    # ── 6. Serialise and return ──────────────────────────────────────────────
    trades_out = [
        TradeResponse(
            entry_time=t.entry_time.isoformat(),
            entry_price=t.entry_price,
            exit_time=t.exit_time.isoformat(),
            exit_price=t.exit_price,
            exit_reason=t.exit_reason,
            direction=t.direction,
            lot_size=t.lot_size,
            pnl_pips=t.pnl_pips,
            pnl_currency=t.pnl_currency,
            initial_risk_pips=t.initial_risk_pips,
            initial_risk_currency=t.initial_risk_currency,
            r_multiple=compute_r_multiple(t),
        )
        for t in result.trades
    ]

    return BacktestRunResponse(
        trades=trades_out,
        equity_curve=result.equity_curve,
        final_balance=result.final_balance,
        initial_balance=result.initial_balance,
        analytics=analytics_out,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
