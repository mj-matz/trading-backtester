"""Pydantic models for request/response validation."""

import re

from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from datetime import date

# Safe symbol characters: letters, digits, caret (^GSPC), dot (BRK.B), dash
_SYMBOL_RE = re.compile(r"^[A-Za-z0-9^.\-]{1,20}$")


class FetchRequest(BaseModel):
    """Request model for the /fetch endpoint."""

    symbol: str = Field(..., min_length=1, max_length=20, description="Instrument symbol, e.g. XAUUSD, SPY")

    @field_validator("symbol")
    @classmethod
    def symbol_safe(cls, v: str) -> str:
        if not _SYMBOL_RE.match(v):
            raise ValueError(
                "Symbol must contain only letters, digits, ^, ., or - (no slashes or special characters)"
            )
        return v.upper()
    source: Literal["dukascopy", "yfinance"] = Field(..., description="Data source")
    timeframe: str = Field(..., min_length=1, max_length=10, description="Timeframe, e.g. 1m, 5m, 1h, 1d")
    date_from: date = Field(..., description="Start date (inclusive)")
    date_to: date = Field(..., description="End date (inclusive)")
    force_refresh: bool = Field(default=False, description="Skip cache and re-download")
    hour_from: Optional[int] = Field(default=None, ge=0, le=23, description="First UTC hour to include (inclusive, Dukascopy only)")
    hour_to: Optional[int] = Field(default=None, ge=0, le=23, description="Last UTC hour to include (inclusive, Dukascopy only)")


class FetchResponse(BaseModel):
    """Response model for the /fetch endpoint."""

    symbol: str
    source: str
    timeframe: str
    date_from: date
    date_to: date
    row_count: int
    file_path: str
    file_size_bytes: int
    cache_id: Optional[str] = None
    cached: bool = False
    columns: list[str] = []
    preview: list[dict] = Field(default_factory=list, description="First 5 rows as dicts for quick inspection")
    actual_date_from: Optional[date] = None
    actual_date_to: Optional[date] = None
    warnings: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    details: Optional[str] = None


class SkippedDayOut(BaseModel):
    """A trading day that was skipped during signal generation."""

    date: str
    reason: str


class CacheEntry(BaseModel):
    """A single data_cache row."""

    id: str
    symbol: str
    source: str
    timeframe: str
    date_from: date
    date_to: date
    file_path: str
    file_size_bytes: int
    row_count: int
    created_at: str
    updated_at: str
    created_by: str
