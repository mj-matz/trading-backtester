# PROJ-1: Data Fetcher

## Status: Deployed
**Created:** 2026-03-09
**Last Updated:** 2026-03-11

## Dependencies
- None

## User Stories
- As a trader, I want to download historical OHLCV data for XAUUSD on 1-minute resolution so that I can backtest intraday strategies with sufficient history.
- As a trader, I want to download DAX (GER30) 1-minute data from Dukascopy so that I can apply the same strategy templates to index instruments.
- As a trader, I want to download daily stock/ETF data via yfinance so that I can backtest longer-term strategies on equities.
- As a trader, I want downloaded data to be cached locally so that repeated backtests don't re-download the same data.
- As a trader, I want to see the available date range for a given asset so that I know how far back my backtest can go.

## Acceptance Criteria
- [ ] Dukascopy data can be fetched for: XAUUSD, GER30 (DAX), major Forex pairs (EUR/USD, GBP/USD, USD/CHF, etc.)
- [ ] yfinance data can be fetched for any valid ticker symbol (stocks, ETFs, indices) at daily resolution
- [ ] Fetched data is stored as local cache (e.g. Parquet files) to avoid redundant downloads
- [ ] Data is returned as OHLCV DataFrame with columns: datetime (UTC), open, high, low, close, volume
- [ ] Datetime index is timezone-aware (UTC) and monotonically increasing (no duplicates, no gaps beyond market hours)
- [ ] Resampling from tick/1m to higher timeframes (5m, 15m, 1h, 1d) works correctly (OHLCV aggregation rules respected)
- [ ] API returns clear error if asset symbol is not supported or data is unavailable for the requested date range
- [ ] Cache invalidation: user can force a refresh to re-download data

## Edge Cases
- Dukascopy returns no data for a weekend or holiday → filter these rows, don't treat as error
- Requested start date is before available history → return available range and warn user
- Network timeout during download → return partial data with error message, do not corrupt cache
- yfinance returns adjusted vs. unadjusted prices → always use adjusted close for daily data
- Timezone handling: Dukascopy data is in UTC; local market hours (e.g. 14:30 Frankfurt time) must be correctly mapped to UTC

## Technical Requirements
- Python script/module callable from Next.js API route via subprocess or FastAPI endpoint
- Cache stored in `/data/cache/` as Parquet files, named `{source}_{symbol}_{timeframe}_{start}_{end}.parquet`
- Dukascopy access via `duka` Python library or direct HTTP download
- yfinance access via `yfinance` Python library
- All datetimes stored and returned in UTC

---
<!-- Sections below are added by subsequent skills -->

## Tech Design (Solution Architect)

### Overview

A Python-powered backend service that fetches, caches, and serves historical OHLCV data from two sources (Dukascopy, yfinance). Uses a **hybrid cache strategy**: actual data stored as Parquet files on disk for fast bulk reads; cache metadata stored in Supabase for queryability and UI integration.

---

### Component Structure

```
Data Fetcher System
+-- FastAPI Service (Python)
|   +-- GET  /data/fetch       ← request OHLCV data for a symbol + range
|   +-- GET  /data/available   ← list cached datasets (reads from Supabase)
|   +-- DELETE /data/cache     ← force-refresh (invalidate cache + DB row)
|
+-- Data Sources (Python modules)
|   +-- Dukascopy Fetcher      ← intraday 1m data (XAUUSD, GER30, Forex)
|   +-- yfinance Fetcher       ← daily data (stocks, ETFs, indices)
|
+-- Resampler                  ← 1m → 5m / 15m / 1h / 1d aggregation
|
+-- Cache Layer (Hybrid)
|   +-- /data/cache/           ← Parquet files on disk (actual OHLCV rows)
|   +-- Supabase: data_cache   ← metadata only (symbol, dates, file path)
|
+-- Next.js API Proxy
    +-- /api/data/fetch        ← forwards to FastAPI, adds auth check
    +-- /api/data/available    ← forwards to FastAPI, adds auth check
    +-- /api/data/cache        ← forwards to FastAPI, adds auth check
```

---

### Data Model

**OHLCV Record** — stored in Parquet files on disk:
```
- datetime   UTC timestamp
- open       Opening price
- high       Highest price in the period
- low        Lowest price in the period
- close      Closing price (adjusted close for daily yfinance data)
- volume     Trade volume (0 for Forex if unavailable)
```

**Cache Metadata** — one row per Parquet file, stored in Supabase:
```
- id               UUID
- symbol           e.g. "XAUUSD", "GER30", "SPY"
- source           "dukascopy" or "yfinance"
- timeframe        "1m", "5m", "15m", "1h", "1d"
- start_date       UTC date (actual data start)
- end_date         UTC date (actual data end)
- file_path        Path to the Parquet file on disk
- file_size_bytes  Size of the Parquet file
- row_count        Number of OHLCV rows
- downloaded_at    Timestamp of last download
```

> Storage estimate: ~300 bytes per cache entry. 500 files ≈ 150 KB in Supabase.
> The 500 MB free tier limit is not a concern for realistic solo-trader usage.

**Data Request** — what callers send:
```
- symbol          e.g. "XAUUSD", "GER30", "SPY"
- source          "dukascopy" or "yfinance"
- timeframe       "1m", "5m", "15m", "1h", "1d"
- start_date      UTC date
- end_date        UTC date
- force_refresh   boolean (skip cache, re-download)
```

---

### Request Flow

```
1. Frontend or backtesting engine requests data
2. Next.js API route verifies user is authenticated (PROJ-8)
3. Request forwarded to FastAPI service
4. FastAPI queries Supabase data_cache for a matching entry
   → Cache HIT:  load Parquet file from disk, return data
   → Cache MISS: download from Dukascopy or yfinance
5. Downloaded data cleaned:
   - Remove weekend/holiday rows
   - Normalize timezone to UTC
   - Validate no duplicate timestamps or unexpected gaps
6. Data saved as Parquet file to /data/cache/
7. Metadata row written to Supabase data_cache table
8. If timeframe > 1m: resample using OHLCV aggregation rules
   (open=first, high=max, low=min, close=last, volume=sum)
9. Return clean OHLCV dataset
```

---

### Tech Decisions

| Decision | Choice | Why |
|---|---|---|
| Python web framework | FastAPI + Uvicorn | Async support, auto-generated API docs, easy to extend for backtesting engine (PROJ-2) |
| Cache format | Parquet (via pandas + pyarrow) | Columnar, compressed, pandas-native — ideal for large time-series bulk reads |
| Cache metadata | Supabase (data_cache table) | Queryable from UI, consistent with rest of stack, negligible storage cost |
| Intraday data | `duka` library | Purpose-built for Dukascopy HTTP downloads |
| Daily data | `yfinance` library | De-facto standard, adjusted close built-in |
| Communication | Next.js proxies to FastAPI | Auth stays in Next.js; Python service never exposed directly to browser |
| Resampling | pandas `resample()` | Correct OHLCV aggregation rules, well-tested |

---

### New Dependencies

**Python:**
- `fastapi` + `uvicorn` — web server
- `pandas` + `pyarrow` — data manipulation and Parquet I/O
- `duka` — Dukascopy data downloader
- `yfinance` — Yahoo Finance data

**Next.js:** no new packages

**Supabase:** one new table (`data_cache`) — metadata only, no OHLCV rows in the database

---

### What Does NOT Change

- No new UI pages (this is infrastructure for PROJ-5)
- Existing auth system (PROJ-8) reused as-is for all API routes

## QA Test Results

**Last tested:** 2026-03-11 (Round 2) | **Tester:** QA Engineer (AI) | **Status:** In Review

### Acceptance Criteria: 8/8 passed

| AC | Description | Result |
|----|-------------|--------|
| AC-1 | Dukascopy fetch for XAUUSD, GER30, Forex pairs | PASS |
| AC-2 | yfinance fetch for any valid ticker at daily resolution | PASS |
| AC-3 | Parquet cache storage | PASS (minor: naming deviates from spec, BUG-6 open) |
| AC-4 | OHLCV DataFrame with correct columns | PASS |
| AC-5 | UTC-aware, monotonically increasing datetime | PASS |
| AC-6 | Resampling with correct OHLCV aggregation | PASS |
| AC-7 | Clear errors for invalid symbols/ranges | PASS |
| AC-8 | Cache invalidation via force_refresh and DELETE | PASS |

### Edge Cases: 4/5 passed

| EC | Description | Result |
|----|-------------|--------|
| EC-1 | Weekend/holiday filtering | PASS |
| EC-2 | Start date before available history | PASS (BUG-7 fixed) |
| EC-3 | Network timeout handling | PARTIAL — timeout returns error, not partial data (BUG-15) |
| EC-4 | Adjusted close for yfinance | PASS |
| EC-5 | Timezone handling | PASS |

### Bug Tracker

| ID | Severity | Description | Status |
|----|----------|-------------|--------|
| BUG-1 | CRITICAL | Service role key committed in `python/services/.env` (not gitignored) | **Fixed by dev** |
| BUG-2 | CRITICAL | FastAPI has no JWT auth — any caller can spoof X-User-Id | **Fixed** |
| BUG-3 | HIGH | `file_path` (server path) leaked in API responses to browser | **Fixed** |
| BUG-4 | HIGH | FastAPI DELETE `/cache/{id}` has no auth | **Fixed** |
| BUG-5 | MEDIUM | Next.js accepted any timeframe string; no enum validation | **Fixed** |
| BUG-6 | LOW | Parquet naming convention deviates from spec (`{source}/{symbol}/{timeframe}/` dirs vs flat file name) | Open |
| BUG-7 | MEDIUM | No range warning when requested start date is before available history | **Fixed** |
| BUG-8 | MEDIUM | No network timeout on Dukascopy or yfinance fetches | **Fixed** |
| BUG-9 | MEDIUM | No rate limiting on `/api/data/available` and `/api/data/cache` routes | **Fixed** |
| BUG-10 | MEDIUM | No rate limiting on FastAPI endpoints | Deferred (local only) |
| BUG-11 | HIGH | Admin check used `user_metadata` (client-writable); should use `app_metadata` | **Fixed** |
| BUG-12 | MEDIUM | Delete order wrong: DB row deleted before Parquet file → orphaned files on partial failure | **Fixed** |
| BUG-13 | LOW | DELETE endpoint returned 200 even when cache entry not found | **Fixed** |
| BUG-14 | HIGH | `cache_service.py` uses service role key, bypassing RLS; `created_by` forgeable | **Fixed** (JWT sub used as verified user ID) |
| BUG-15 | LOW | On timeout, spec says return partial data; implementation returns error with no data | Open |
| BUG-16 | HIGH | RLS DELETE policy used `user_metadata` — any user could self-escalate via `supabase.auth.updateUser()` | **Fixed** |
| BUG-17 | MEDIUM | Symbol field allowed path traversal characters used in Parquet file paths | **Fixed** |
| BUG-18 | — | `python/services/.env` has real credentials on disk — expected for local dev, gitignored | Not a bug |
| BUG-25 | MEDIUM | FastAPI bound to `0.0.0.0`, exposing unauthenticated service to entire LAN | **Fixed** |

### Production Readiness

All critical and high severity bugs resolved. Remaining open items (BUG-6, BUG-15) are low severity and do not block deployment.

## Deployment

**Deployed:** 2026-03-11

| Component | Platform | URL |
|-----------|----------|-----|
| Next.js API proxy (`/api/data/*`) | Vercel | https://trading-backtester-production.up.railway.app (via Vercel frontend) |
| Python FastAPI service | Railway | https://trading-backtester-production.up.railway.app |

**Environment variables set:**
- Vercel: `FASTAPI_URL=https://trading-backtester-production.up.railway.app`
- Railway: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`, `DATA_DIR`, `FETCH_TIMEOUT_SECONDS`

**Supabase migrations applied:**
- `20260311_data_cache` — `data_cache` table + RLS policies
- `20260312_fix_rls_delete_policy` — DELETE policy uses `app_metadata`
