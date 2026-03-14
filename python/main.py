"""FastAPI service for the Data Fetcher (PROJ-1) and Backtesting Engine (PROJ-2).

Provides endpoints for fetching/caching historical OHLCV data and running
backtests against cached data sets.
"""

import logging
import threading
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import List, Literal, Optional

import numpy as np
import pandas as pd
from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from models import FetchRequest, FetchResponse, ErrorResponse, SkippedDayOut
from fetchers.dukascopy_fetcher import fetch_dukascopy
from fetchers.yfinance_fetcher import fetch_yfinance, VALID_INTERVALS as YFINANCE_INTERVALS
from services.auth import verify_jwt
from services.cache_service import find_cached_entry, load_cached_data, save_to_cache, delete_cache_entry
from services.resampler import resample_ohlcv, TIMEFRAME_TO_RULE
from engine import run_backtest
from engine.models import BacktestConfig, InstrumentConfig
from analytics import calculate_analytics
from analytics.trade_metrics import r_multiple as compute_r_multiple
from strategies.breakout import BreakoutStrategy, BreakoutParams

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
            # Always fetch 1m data first, then resample if needed.
            # Optional hour_from/hour_to narrow the download to specific UTC hours (BUG-27).
            h_from = request.hour_from if request.hour_from is not None else 0
            h_to = request.hour_to if request.hour_to is not None else 23
            base_df = fetch_dukascopy(symbol, date_from, date_to, hour_from=h_from, hour_to=h_to)
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


_DIRECTION_MAP = {"long": "long_only", "short": "short_only", "both": "both"}


# ── Asset list models + endpoint ──────────────────────────────────────────────

class AssetOut(BaseModel):
    symbol: str
    name: str
    category: str


@app.get("/assets", response_model=List[AssetOut])
async def list_assets(token: dict = Depends(verify_jwt)):
    """
    Return the full list of instruments supported by the platform.

    Reads from the Supabase `instruments` table — add new assets via the
    Supabase dashboard without redeploying the service.
    """
    from services.cache_service import _get_supabase_client

    try:
        client = _get_supabase_client()
        resp = (
            client.table("instruments")
            .select("symbol, name, category")
            .order("category")
            .order("symbol")
            .execute()
        )
    except Exception as e:
        logger.error(f"Failed to fetch instruments: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to load instrument list.")

    return resp.data or []


async def _resolve_instrument(symbol: str) -> dict:
    """
    Look up an instrument's engine config (pip_size, pip_value_per_lot) from
    the Supabase `instruments` table.

    Raises HTTPException 400 if the symbol is not in the database.
    """
    from services.cache_service import _get_supabase_client

    try:
        client = _get_supabase_client()
        resp = (
            client.table("instruments")
            .select("pip_size, pip_value_per_lot")
            .eq("symbol", symbol)
            .single()
            .execute()
        )
    except Exception as e:
        logger.error(f"Instrument lookup failed for {symbol}: {e}", exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to validate instrument.")

    if not resp.data:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Symbol '{symbol}' is not supported. "
                "See the asset list at GET /assets for supported instruments."
            ),
        )

    return resp.data


# ── Orchestration request / response models ───────────────────────────────────

class BacktestOrchestrationRequest(BaseModel):
    strategy: str
    symbol: str = Field(min_length=1)
    timeframe: str
    startDate: str
    endDate: str
    rangeStart: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    rangeEnd: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    triggerDeadline: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    timeExit: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    stopLoss: float = Field(gt=0)
    takeProfit: float = Field(gt=0)
    direction: Literal["long", "short", "both"]
    commission: float = Field(default=0.0, ge=0)
    slippage: float = Field(default=0.0, ge=0)
    initialCapital: float = Field(gt=0)
    sizingMode: Literal["risk_percent", "fixed_lot"]
    riskPercent: Optional[float] = Field(default=None, gt=0, le=100)
    fixedLot: Optional[float] = Field(default=None, gt=0)


class BacktestMetricsOut(BaseModel):
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown_pct: float
    calmar_ratio: float
    longest_drawdown_days: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    avg_win_pips: float
    avg_loss_pips: float
    profit_factor: float
    avg_r_multiple: float
    expectancy_pips: float
    final_balance: float


class EquityCurveOut(BaseModel):
    date: str
    balance: float


class DrawdownCurveOut(BaseModel):
    date: str
    drawdown_pct: float


class TradeDetailOut(BaseModel):
    id: int
    entry_time: str
    exit_time: str
    direction: str
    entry_price: float
    exit_price: float
    lot_size: float
    pnl_pips: float
    pnl_currency: float
    r_multiple: float
    exit_reason: str
    duration_minutes: int


class BacktestOrchestrationResponse(BaseModel):
    metrics: BacktestMetricsOut
    equity_curve: List[EquityCurveOut]
    drawdown_curve: List[DrawdownCurveOut]
    trades: List[TradeDetailOut]
    skipped_days: List[SkippedDayOut] = []


# ── /backtest orchestration endpoint ─────────────────────────────────────────

@app.post("/backtest", response_model=BacktestOrchestrationResponse)
async def backtest_orchestrate(
    request: BacktestOrchestrationRequest,
    token: dict = Depends(verify_jwt),
):
    """
    Full orchestration: fetch data → generate signals → run engine → analytics.

    Accepts a user-friendly configuration object from the frontend UI and
    returns a complete result ready for display (metrics, equity curve,
    drawdown curve, trade list).

    Rate limit: 30 requests / minute per user (shared with /backtest/run).
    Any authenticated user may call this endpoint.
    """
    user_id: str = token["sub"]

    if not _check_backtest_rate_limit(user_id):
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: max 30 backtest requests per minute.",
        )

    # ── 1. Validate strategy ──────────────────────────────────────────────────
    if request.strategy != "time_range_breakout":
        raise HTTPException(
            status_code=400,
            detail=f"Unknown strategy '{request.strategy}'. Supported: time_range_breakout",
        )

    symbol = request.symbol.upper()

    # ── 2. Resolve instrument config from Supabase (validates symbol) ─────────
    instrument = await _resolve_instrument(symbol)

    # ── 3. Parse and validate date range ──────────────────────────────────────
    try:
        date_from = date.fromisoformat(request.startDate)
        date_to = date.fromisoformat(request.endDate)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date: {e}")

    _validate_date_range(date_from, date_to)
    _validate_timeframe("dukascopy", request.timeframe)

    # ── 4. Fetch / load cached data ───────────────────────────────────────────
    # Derive the UTC hour range from the strategy config (BUG-27).
    # Add ±1h buffer around the trading window to handle DST transitions.
    _range_start_h = int(request.rangeStart.split(":")[0])
    _time_exit_h = int(request.timeExit.split(":")[0])
    hour_from = max(0, _range_start_h - 1)
    hour_to = min(23, _time_exit_h + 1)

    df = None
    cached = find_cached_entry(symbol, "dukascopy", request.timeframe, date_from, date_to)
    if cached:
        try:
            df = load_cached_data(cached["file_path"])
        except Exception as e:
            logger.warning(f"Cache load failed for {symbol}, re-fetching: {e}")
            df = None

    if df is None:
        try:
            # On cache miss: download only the required hours to save time (BUG-27).
            base_df = fetch_dukascopy(symbol, date_from, date_to, hour_from=hour_from, hour_to=hour_to)
            df = resample_ohlcv(base_df, request.timeframe) if request.timeframe != "1m" else base_df
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"Fetch error for {symbol}: {e}", exc_info=True)
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch data for {symbol}: {e}",
            )

        if df.empty:
            raise HTTPException(
                status_code=404,
                detail=f"No data available for {symbol} between {date_from} and {date_to}",
            )

        try:
            save_to_cache(df, symbol, "dukascopy", request.timeframe, date_from, date_to, user_id)
        except Exception as e:
            logger.warning(f"Cache save failed (non-fatal): {e}")

    # Normalize to DatetimeIndex
    if "datetime" in df.columns:
        df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index, utc=True)
    df.columns = [c.lower() for c in df.columns]

    required_cols = {"open", "high", "low", "close"}
    if not required_cols.issubset(set(df.columns)):
        raise HTTPException(
            status_code=400,
            detail=f"Data is missing required columns: {required_cols - set(df.columns)}",
        )

    # ── 4b. Filter to requested date range (BUG-14 fix) ─────────────────────
    # Cached data may cover a wider range than requested. Trim to [date_from, date_to].
    df = df[
        (df.index.date >= date_from) & (df.index.date <= date_to)
    ]
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data in range {date_from} to {date_to} (cached file may not cover this range — try force_refresh)",
        )

    # ── 4c. Filter to relevant UTC hours (BUG-27 fix) ────────────────────────
    # Keep only bars within [hour_from, hour_to]. This covers both the range-
    # formation window (rangeStart..rangeEnd) and the trade window (..timeExit),
    # with a ±1h DST buffer applied when hour_from/hour_to were derived above.
    df = df[df.index.hour.between(hour_from, hour_to)]
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data in UTC hour range {hour_from:02d}:00-{hour_to:02d}:00 for {symbol} ({date_from} to {date_to})",
        )

    # ── 5. Generate breakout signals ──────────────────────────────────────────
    breakout_params = BreakoutParams(
        asset=symbol,
        range_start=time.fromisoformat(request.rangeStart),
        range_end=time.fromisoformat(request.rangeEnd),
        trigger_deadline=time.fromisoformat(request.triggerDeadline),
        stop_loss_pips=request.stopLoss,
        take_profit_pips=request.takeProfit,
        pip_size=instrument["pip_size"],
        timezone="UTC",
        direction_filter=_DIRECTION_MAP[request.direction],
    )

    strategy = BreakoutStrategy()
    try:
        signals_df, skipped_days = strategy.generate_signals(df, breakout_params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # ── 6. Run backtesting engine ─────────────────────────────────────────────
    engine_config = BacktestConfig(
        initial_balance=request.initialCapital,
        sizing_mode=request.sizingMode,
        instrument=InstrumentConfig(
            pip_size=instrument["pip_size"],
            pip_value_per_lot=instrument["pip_value_per_lot"],
        ),
        fixed_lot=request.fixedLot,
        risk_percent=request.riskPercent,
        commission=request.commission,
        slippage_pips=request.slippage,
        time_exit=request.timeExit,
        timezone="UTC",
    )

    try:
        result = run_backtest(df, signals_df, engine_config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Backtest engine error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal engine error.")

    # ── 7. Calculate analytics ────────────────────────────────────────────────
    try:
        analytics_result = calculate_analytics(result)
    except Exception as e:
        logger.error(f"Analytics error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Analytics calculation failed.")

    # ── 8. Build response ─────────────────────────────────────────────────────
    m = {metric.name: metric.value for metric in analytics_result.summary}

    def _f(v, default: float = 0.0) -> float:
        """Coerce None / inf / NaN to a safe float default."""
        if v is None:
            return default
        try:
            f = float(v)
        except (TypeError, ValueError):
            return default
        return default if (f != f or f == float("inf")) else f  # NaN or +inf → default

    cagr_val = _f(m.get("CAGR"))
    dd_val = _f(m.get("Max Drawdown"))
    calmar_val = round(cagr_val / abs(dd_val), 2) if dd_val != 0.0 else 0.0

    metrics_out = BacktestMetricsOut(
        total_return_pct=_f(m.get("Total Return")),
        cagr_pct=cagr_val,
        sharpe_ratio=_f(m.get("Sharpe Ratio")),
        sortino_ratio=_f(m.get("Sortino Ratio")),
        max_drawdown_pct=dd_val,
        calmar_ratio=calmar_val,
        longest_drawdown_days=_f(m.get("Max Drawdown Duration")),
        total_trades=int(m.get("Total Trades") or 0),
        winning_trades=int(m.get("Winning Trades") or 0),
        losing_trades=int(m.get("Losing Trades") or 0),
        win_rate_pct=_f(m.get("Win Rate")),
        avg_win_pips=_f(m.get("Avg Win (Pips)")),
        avg_loss_pips=_f(m.get("Avg Loss (Pips)")),
        profit_factor=_f(m.get("Profit Factor (Pips)")),
        avg_r_multiple=_f(m.get("Avg R per Trade")),
        expectancy_pips=_f(m.get("Expectancy (Pips)")),
        final_balance=result.final_balance,
    )

    # Equity curve: rename "time" → "date" to match the frontend type
    equity_curve_out = [
        EquityCurveOut(date=pt["time"], balance=pt["balance"])
        for pt in result.equity_curve
    ]

    # Drawdown curve: compute running-peak drawdown from the equity curve
    peak = result.initial_balance
    drawdown_curve_out: list[DrawdownCurveOut] = []
    for pt in result.equity_curve:
        bal = pt["balance"]
        if bal > peak:
            peak = bal
        dd_pct = round((bal - peak) / peak * 100, 4) if peak > 0 else 0.0
        drawdown_curve_out.append(DrawdownCurveOut(date=pt["time"], drawdown_pct=dd_pct))

    # Trades
    trades_out: list[TradeDetailOut] = []
    for i, t in enumerate(result.trades):
        duration_minutes = int((t.exit_time - t.entry_time).total_seconds() / 60)
        trades_out.append(TradeDetailOut(
            id=i + 1,
            entry_time=t.entry_time.isoformat(),
            exit_time=t.exit_time.isoformat(),
            direction=t.direction,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            lot_size=t.lot_size,
            pnl_pips=t.pnl_pips,
            pnl_currency=t.pnl_currency,
            r_multiple=_f(compute_r_multiple(t)),
            exit_reason=t.exit_reason,
            duration_minutes=duration_minutes,
        ))

    skipped_days_out = [
        SkippedDayOut(date=sd.date, reason=sd.reason)
        for sd in skipped_days
    ]

    return BacktestOrchestrationResponse(
        metrics=metrics_out,
        equity_curve=equity_curve_out,
        drawdown_curve=drawdown_curve_out,
        trades=trades_out,
        skipped_days=skipped_days_out,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
