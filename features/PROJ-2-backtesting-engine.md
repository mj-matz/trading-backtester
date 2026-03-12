# PROJ-2: Backtesting Engine

## Status: In Review
**Created:** 2026-03-09
**Last Updated:** 2026-03-11 (QA complete — 7 bugs fixed, 27 tests passing)

## Dependencies
- Requires: PROJ-1 (Data Fetcher) — engine consumes OHLCV DataFrames produced by the fetcher

## User Stories
- As a trader, I want the engine to simulate orders bar-by-bar so that there is no look-ahead bias in the results.
- As a trader, I want Stop Loss and Take Profit orders to be triggered correctly within a bar (using bar High/Low) so that exits are realistic.
- As a trader, I want OCO (One-Cancels-Other) order pairs so that I can model breakout entries where only one side fires.
- As a trader, I want a time-based forced exit so that open positions are closed at a specified time (e.g. 21:00).
- As a trader, I want configurable commission and slippage per trade so that results reflect realistic trading costs.
- As a trader, I want the engine to enforce a maximum of 1 open trade at a time so that strategy rules are correctly respected.
- As a trader, I want the engine to be deterministic so that running the same backtest twice always produces identical results.
- As a trader, I want the engine to support a conditional SL step: when a trade's unrealised profit reaches a configured threshold, the SL is moved to a new level (locking in partial profit), so that I can test profit-protection rules without coding them manually.

## Acceptance Criteria
- [ ] Engine processes data bar-by-bar in strict chronological order — no future data is accessible during simulation
- [ ] Within a bar, order of events is: Open → Stop/Limit trigger check (using High/Low) → Close
- [ ] SL and TP are evaluated on every bar using bar High and Low; if both are hit in one bar, SL is assumed (worst case)
- [ ] OCO order logic: when one order fires, the partner order is immediately cancelled
- [ ] Time exit: open positions are closed at the bar whose datetime >= configured exit time, using that bar's open price
- [ ] Commission modeled as fixed cost per trade (configurable, e.g. 0.0 for no commission)
- [ ] Slippage modeled as fixed offset on entry/exit price (configurable in pips/points)
- [ ] Maximum 1 open position enforced; new entry signals are ignored while a position is open
- [ ] Position sizing mode supported: "fixed lot" (user specifies lot size directly) or "risk percent" (engine calculates lot size from account balance × risk % / (SL pips × pip value per lot))
- [ ] In "risk percent" mode, lot size is recalculated for each trade based on the account balance at trade entry (compounding)
- [ ] Engine output includes a trade log: entry time, entry price, exit time, exit price, exit reason (SL / SL_TRAILED / TP / TIME), lot size used, PnL in pips and in account currency, initial risk in pips (= entry price − initial SL price), initial risk in account currency (initial risk in pips × pip value × lot size)
  - `SL` — stopped out at the original, unmodified stop loss
  - `SL_TRAILED` — stopped out after the SL was moved by the conditional SL step (trail trigger was reached)
  - `TP` — take profit hit
  - `TIME` — position force-closed at configured time exit
- [ ] Engine output includes an equity curve: time series of account balance after each closed trade
- [ ] Engine is callable as a pure Python function — no side effects, fully testable in isolation
- [ ] Conditional SL step supported: if `trail_trigger_pips` is set and open trade profit reaches that level, SL is moved to `trail_lock_pips` above/below entry price (long/short respectively) — this adjustment happens exactly once per trade
- [ ] If the price never reaches `trail_trigger_pips`, the original SL remains unchanged
- [ ] `trail_trigger_pips` and `trail_lock_pips` are optional; if not set, engine behaves as fixed SL only

## Edge Cases
- Bar where both SL and TP would be hit → assume SL triggered (conservative / worst-case assumption)
- Trail trigger and new SL level both hit within the same bar → SL step is applied first, then evaluate exit against new SL level
- `trail_lock_pips` >= `stop_loss_pips` is not validated by engine (strategy must ensure this is meaningful); engine executes as configured
- Time exit bar is missing (e.g. market closed early) → close at last available bar before exit time
- Entry order placed at the close of a bar and immediately triggered on the same bar → not allowed; entry is evaluated from next bar
- No trades triggered in the entire backtest period → return empty trade log and flat equity curve, no error
- Backtest period contains gaps (weekends, holidays) → gaps are ignored, no phantom trades or errors
- Insufficient data for the strategy's lookback period → skip those initial bars silently

## Technical Requirements
- Pure Python implementation, no external backtesting framework dependency (e.g. no backtrader, no vectorbt) to ensure full rule transparency
- All calculations in floating point with consistent rounding (pip/point precision per instrument)
- Pip/point value must be configurable per instrument (e.g. XAUUSD: 1 pip = $0.10 per 0.01 lot; GER30: 1 point = €1 per contract)
- Performance target: backtest of 1 year of 1-minute XAUUSD data completes in under 60 seconds

---
<!-- Sections below are added by subsequent skills -->

## Tech Design (Solution Architect)

### Build Order Note
PROJ-3 (Breakout Strategy) is a **consumer** of the engine, not a prerequisite. PROJ-2 is built and tested first using synthetic/hardcoded test signals. PROJ-3 then plugs its signal output into the same engine function.

### Module Structure

```
backend/engine/
+-- engine.py            Core simulator — public entry point: run_backtest()
+-- order_manager.py     Tracks pending orders; evaluates SL/TP/OCO per bar
+-- position_tracker.py  Manages the single open position + SL step logic
+-- sizing.py            Position sizing (fixed lot vs. risk percent)
+-- models.py            Data classes: BacktestConfig, Trade, BacktestResult
+-- pip_utils.py         Pip/point value calculations per instrument
```

Called by:
```
PROJ-3 (Breakout Strategy)   → passes OHLCV + entry/exit signals
PROJ-5 (Backtest UI)         → POST /api/backtest/run → calls run_backtest()
```

### Data Model (Plain Language)

**Input — BacktestConfig**
- Initial account balance (e.g. $10,000)
- Position sizing mode: "fixed lot" or "risk percent"
- Commission: fixed cost per trade (default 0)
- Slippage: fixed offset on entry and exit price
- Time exit: clock time to force-close any open position (e.g. "21:00")
- Instrument config: pip size + pip value per lot
- Conditional SL step (optional): trail trigger pips + trail lock pips

**Input — Signals** (produced by PROJ-3, or synthetic test data for PROJ-2 development)
- Per bar: Direction (Long / Short / None), entry price, Stop Loss price, Take Profit price

**Output — Trade Record (per closed trade)**
- Entry time and price
- Exit time and price
- Exit reason: `SL` / `SL_TRAILED` / `TP` / `TIME`
  - `SL` — stopped out at the original, unmodified stop loss
  - `SL_TRAILED` — stopped out after the SL was moved by the conditional SL step
  - `TP` — take profit hit
  - `TIME` — force-closed at configured time exit
- Lot size used
- PnL in pips and in account currency
- Initial risk in pips and account currency

**Output — BacktestResult**
- List of all trade records (trade log)
- Equity curve: account balance after each closed trade
- Final account balance

### Simulation Logic (per bar)

1. **Position open:** Check SL step trigger → move SL if reached (once per trade). Then check SL/TP hit using bar High/Low. If both hit: assume SL (worst case). Check time exit.
2. **No position:** Check for entry signal → open trade if present (apply slippage, deduct commission).
3. **End of data:** Close any remaining open position at last bar close.

### Tech Decisions

| Decision | Why |
|---|---|
| Pure Python, no backtesting framework | Full transparency of rules; no hidden 3rd-party behavior |
| `dataclass` for models | Type-safe, easily serializable to JSON |
| pandas DataFrame for OHLCV | Consistent with PROJ-1 output; no conversion needed |
| Single function entry point `run_backtest()` | Stateless, testable in isolation, no side effects |
| NumPy for price comparisons where safe | Meets 60-second performance target on 1 year of 1-min data |

### Dependencies

| Package | Purpose |
|---|---|
| `pandas` | Already installed — OHLCV data handling |
| `numpy` | Already installed — vectorized price comparisons |
| `dataclasses` | Built-in Python — structured data models |
| `pytest` | Testing — verify determinism and all edge cases |

No new packages required.

### API Route (for PROJ-5)
`POST /api/backtest/run` — accepts BacktestConfig + signals as JSON, returns BacktestResult as JSON.

## QA Test Results

**Tested:** 2026-03-11 | **Method:** Code review + static analysis + pytest
**Result:** Production-ready after fixes (see below)

### Acceptance Criteria: 16/16 PASSED
All 16 acceptance criteria verified by code review.

### Edge Cases (from spec): 8/8 PASSED
All documented edge cases handled correctly.

### Additional Edge Cases Found & Fixed

| ID | Description | Severity | File | Fix Applied |
|----|-------------|----------|------|-------------|
| EC-9 | Empty OHLCV input caused `IndexError` at `ohlcv.index[0]` | High | `engine.py:114` | Early return with empty `BacktestResult` |
| EC-10 | `trail_trigger_pips` set without `trail_lock_pips` caused `TypeError` in `pips_to_price_offset(None, ...)` | High | `position_tracker.py:49` | Default `trail_lock_pips` to `0.0` (breakeven) when `None` |

---

### Bugs Found & Fixed

#### BUG-1 — CRITICAL: Authorization bypass — cache ownership not verified
- **File:** `python/main.py:383`
- **Problem:** `/backtest/run` queried `data_cache` by `cache_id` only, allowing any authenticated user to run backtests against another user's cached dataset.
- **Fix:** Added `.eq("created_by", user_id)` to the Supabase query — cache entries are now scoped to the requesting user.

#### BUG-2 — HIGH: TypeError when `trail_trigger_pips` set without `trail_lock_pips`
- **File:** `python/engine/position_tracker.py:49`
- **Problem:** `pips_to_price_offset(None, pip_size)` raised `TypeError`.
- **Fix:** `lock_pips = config.trail_lock_pips if config.trail_lock_pips is not None else 0.0` — defaults to breakeven (SL moves to entry).

#### BUG-3 — HIGH: IndexError on empty OHLCV DataFrame
- **File:** `python/engine/engine.py:114`
- **Problem:** `ohlcv.index[0]` crashed when OHLCV had 0 rows.
- **Fix:** Added early return guard before the main loop — returns empty `BacktestResult` with initial balance.

#### BUG-4 — MEDIUM: Internal error details leaked in API responses
- **File:** `python/main.py:411, 476`
- **Problem:** `detail=f"Engine error: {e}"` and `detail=f"Failed to load data: {e}"` exposed Python exception messages (including potential file paths) to clients.
- **Fix:** Replaced with generic messages: `"Internal engine error."` and `"Failed to load data."` — full details still logged server-side.

#### BUG-5 — MEDIUM: Invalid `time_exit` values accepted (e.g. `"25:99"`)
- **Files:** `src/app/api/backtest/run/route.ts:28`, `python/engine/engine.py:27`
- **Problem:** Zod regex `/^\d{2}:\d{2}$/` allowed out-of-range values; Python `time(25, 99)` raised an unhandled `ValueError` returning a 500.
- **Fix:** Zod regex changed to `/^([01]\d|2[0-3]):[0-5]\d$/`; Python `_parse_time_exit` wrapped in `try/except` re-raising as `ValueError` with a clear message (caught upstream as HTTP 400).

#### BUG-6 — MEDIUM: OCO always favoured long when both sides triggered on same bar
- **File:** `python/engine/order_manager.py:17`
- **Problem:** `_extract_pending_orders` always appended long before short; `evaluate_pending_orders` returned the first triggered — long always won on same-bar conflicts.
- **Fix:** Refactored `evaluate_pending_orders` to collect all triggered orders first, then select the one whose `entry_price` is closest to `bar_open` (i.e. triggered first). Ties still break in favour of long (documented).

#### BUG-7 — LOW: `datetime.utcnow()` deprecated in Python 3.12+
- **File:** `python/main.py:283`
- **Problem:** `datetime.utcnow()` is deprecated since Python 3.12.
- **Fix:** Replaced with `datetime.now(timezone.utc)`; added `timezone` to imports.

---

### Missing Test Coverage — Added

6 test scenarios were absent from `python/tests/test_engine.py` and have been added:

| Test | Class | Description |
|------|-------|-------------|
| `test_short_trail_trigger` | `TestTrailTrigger` | Trail trigger on short trade; SL locks in 20 pip profit |
| `test_trail_trigger_without_lock_pips_defaults_to_breakeven` | `TestTrailTrigger` | `trail_lock_pips=None` → SL moves to entry (breakeven) |
| `test_time_exit_fires_on_next_bar_when_exact_bar_missing` | `TestTimeExit` | Gap in bars — `>= exit_time` condition catches next available bar |
| `test_commission_and_slippage_combined` | `TestCommissionAndSlippage` | Both applied simultaneously; effects are additive |
| `test_empty_ohlcv_returns_empty_result` | `TestEdgeCases` | Empty OHLCV returns empty result without raising |
| `test_lot_size_grows_with_balance_after_winning_trade` | `TestRiskPercentCompounding` | After winning trade, next lot size grows proportionally |

**Final test count: 27/27 passed.**

## Deployment
_To be added by /deploy_
