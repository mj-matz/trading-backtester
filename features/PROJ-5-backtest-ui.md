# PROJ-5: Backtest UI (Configuration + Results)

## Status: Planned
**Created:** 2026-03-09
**Last Updated:** 2026-03-09

## Dependencies
- Requires: PROJ-1 (Data Fetcher) — UI triggers data download
- Requires: PROJ-2 (Backtesting Engine) — UI triggers backtest run
- Requires: PROJ-3 (Time-Range Breakout Strategy) — UI exposes strategy parameters
- Requires: PROJ-4 (Performance Analytics) — UI displays computed metrics
- Requires: PROJ-8 (Authentication) — all routes are protected; user session required
- Extended by: PROJ-9 (Backtest History) — adds "Save Run" button and history view to this UI

## User Stories
- As a trader, I want a configuration form where I select strategy template, asset, timeframe, date range, and strategy parameters so that I can define a backtest in one place.
- As a trader, I want to click "Run Backtest" and see a loading indicator while the backend processes data so that I know the system is working.
- As a trader, I want to see an Equity Curve chart so that I can visually assess the strategy's performance over time.
- As a trader, I want to see a Drawdown chart below the Equity Curve so that I can see drawdown periods at a glance.
- As a trader, I want to see all performance metrics in a structured summary card so that I can assess the strategy at a glance.
- As a trader, I want to see a trade list with entry time, exit time, direction, PnL, and exit reason so that I can inspect individual trades.
- As a trader, I want to change a parameter (e.g. Take Profit from 175 to 200 pips) and re-run the backtest immediately so that I can quickly iterate on strategy settings.
- As a trader, I want the last used configuration to be remembered so that I don't have to re-enter all parameters after a page refresh.

## Acceptance Criteria

### Configuration Form
- [ ] Strategy template selector (initially only "Time-Range Breakout"; extensible for future strategies)
- [ ] Asset input field with validation (e.g. XAUUSD, GER30)
- [ ] Timeframe selector: 1m, 5m, 15m, 1h, 1d
- [ ] Date range picker: start date and end date
- [ ] Strategy-specific parameter fields rendered dynamically based on selected template:
  - Range Start time, Range End time
  - Trigger Deadline time
  - Time Exit time
  - Stop Loss (pips/points)
  - Take Profit (pips/points)
  - Direction (Long / Short / Both)
  - Commission (pips), Slippage (pips)
- [ ] Initial Capital field (default: 10,000)
- [ ] Position sizing mode selector: "Risk %" or "Fixed Lot"
  - If "Risk %": input field for risk per trade in % (e.g. 1.0 = 1% of current balance per trade); lot size is calculated automatically by the engine
  - If "Fixed Lot": input field for lot size (e.g. 0.1); risk % is informational only
- [ ] "Run Backtest" button — disabled while a backtest is running
- [ ] Form validation: all required fields filled, times are valid, SL > 0, TP > 0, end date > start date, risk % between 0.01 and 100
- [ ] Last configuration is persisted in localStorage and restored on page load

### Results Dashboard
- [ ] Loading state shown while backtest runs (spinner + "Running backtest…" message)
- [ ] Error state shown if backtest fails (clear message, no crash)
- [ ] Empty state shown if no trades were generated
- [ ] Equity Curve chart: line chart, x-axis = date, y-axis = account balance
- [ ] Drawdown chart: area chart below equity curve, shows drawdown % over time
- [ ] Metrics summary card with all metrics from PROJ-4 (grouped: Overview, Trade Stats, Risk)
- [ ] Trade list table: sortable by date, PnL, duration; columns: #, Date, Direction, Entry, Exit, Lot Size, PnL (pips), PnL (€/$), R-Multiple, Exit Reason, Duration
- [ ] Trade list is paginated (50 trades per page) for long backtests
- [ ] Charts are interactive: hover shows exact values; zoom/pan on time axis
- [ ] "Save Run" button visible after a completed backtest (implemented by PROJ-9; placeholder shown in PROJ-5 with "coming soon" if PROJ-9 not yet built)

### UX
- [ ] Mobile responsive (375px, 768px, 1440px)
- [ ] Configuration and results visible without horizontal scrolling on desktop
- [ ] All shadcn/ui components used for form elements (Input, Select, Button, Card, Table, Tabs)

## Edge Cases
- Backtest runs longer than 30 seconds → show timeout warning, allow cancellation
- Backend returns an error (e.g. symbol not found on Dukascopy) → show user-friendly error, keep form intact
- Zero trades returned → show "No trades in this period" message instead of empty charts
- User changes parameters while results are displayed → results stay visible until new backtest is explicitly run

## Technical Requirements
- Next.js App Router page at `/` or `/backtest`
- API route at `POST /api/backtest` — accepts config JSON, returns results JSON
- Chart library: Recharts (already compatible with shadcn/ui ecosystem)
- Form state managed with react-hook-form + Zod validation
- Backtest runs asynchronously; frontend polls or uses streaming response

---
<!-- Sections below are added by subsequent skills -->

## Tech Design (Solution Architect)

### Existing Infrastructure
- `POST /api/backtest/run` — low-level engine endpoint; requires pre-computed signals (not used directly by the UI)
- `GET /api/data/*` — data fetch, cache, and availability endpoints
- Dashboard shell at `/(dashboard)/` with sidebar + auth-protected layout
- All required shadcn/ui components already installed

### Component Structure

```
src/app/(dashboard)/backtest/page.tsx   ← NEW route
+-- BacktestPage (2-column layout on desktop, stacked on mobile)
    |
    +-- [Left Column] ConfigurationPanel
    |   +-- StrategySelector (Select: "Time-Range Breakout" + future strategies)
    |   +-- AssetInput (Input: e.g. XAUUSD, GER30)
    |   +-- TimeframeSelector (Select: 1m / 5m / 15m / 1h / 1d)
    |   +-- DateRangePicker (two date Input fields: Start / End)
    |   +-- StrategyParamsSection (rendered dynamically per selected strategy)
    |   |   +-- [Time-Range Breakout]
    |   |       +-- Range Start / Range End (time inputs)
    |   |       +-- Trigger Deadline / Time Exit (time inputs)
    |   |       +-- Stop Loss / Take Profit (number inputs, in pips)
    |   |       +-- Direction (RadioGroup: Long / Short / Both)
    |   |       +-- Commission / Slippage (number inputs, in pips)
    |   +-- CapitalSection
    |   |   +-- Initial Capital (Input, default 10,000)
    |   |   +-- Sizing Mode (RadioGroup: "Risk %" / "Fixed Lot")
    |   |   +-- Risk % OR Lot Size (conditional Input based on sizing mode)
    |   +-- RunBacktestButton (disabled while a run is in progress)
    |
    +-- [Right Column] ResultsPanel
        +-- EmptyState      (before first run — prompt to configure and run)
          OR LoadingState   (spinner + "Running backtest…" + timeout warning at 30s)
          OR ErrorState     (user-friendly error message; form stays intact)
          OR ResultsDashboard
              +-- MetricsSummaryCard
              |   +-- Overview group (Total Return, CAGR, Sharpe Ratio)
              |   +-- Trade Stats group (Win Rate, Avg Win, Avg Loss, Profit Factor)
              |   +-- Risk group (Max Drawdown, Calmar Ratio, Longest Drawdown)
              +-- ChartsSection
              |   +-- EquityCurveChart (Recharts LineChart, x=date, y=balance)
              |   +-- DrawdownChart (Recharts AreaChart, below equity curve)
              +-- TradeListSection
                  +-- SortControls (sort by date / PnL / duration)
                  +-- TradeTable (columns: #, Date, Direction, Entry, Exit,
                  |              Lot Size, PnL pips, PnL €, R-Multiple,
                  |              Exit Reason, Duration)
                  +-- Pagination (50 trades per page)
                  +-- SaveRunButton (placeholder — "coming soon" badge; wired up by PROJ-9)
```

### New API Endpoint

The existing `POST /api/backtest/run` is a low-level engine endpoint that requires pre-computed signals — it is not suitable for direct use from the UI. A new user-facing orchestration endpoint is needed:

```
POST /api/backtest
  Input:  symbol, date range, timeframe, strategy name + params, engine config
  Internally: fetch/cache data → compute signals → run engine → compute analytics
  Output: { metrics, equity_curve[], drawdown_curve[], trades[] }
```

This keeps the frontend simple: one request in, full results out. The multi-step pipeline remains hidden on the server.

### User Interaction Flow

1. On page load → last config is restored from `localStorage`
2. User adjusts parameters → form validates in real time (react-hook-form + Zod)
3. User clicks "Run Backtest" → button disables, loading state replaces results panel
4. Frontend calls `POST /api/backtest` with full config JSON
5. If the request takes > 30s → a timeout warning appears with a cancel option
6. Response arrives → results are parsed and displayed in ResultsDashboard
7. Changing parameters does **not** clear results — old results remain until a new run is explicitly started

### Data Model (plain language)

**Form Config** — persisted to `localStorage`, restored on page load:
- Strategy name, asset/symbol, timeframe
- Date range (start + end)
- Strategy parameters (range times, trigger deadline, time exit, SL, TP, direction, commission, slippage)
- Initial capital, sizing mode, risk % or fixed lot size

**Backtest Result** — held in React state only (not persisted until PROJ-9 adds save):
- Metrics object (all PROJ-4 analytics values)
- Equity curve: list of `{ date, balance }` data points
- Drawdown curve: list of `{ date, drawdown_pct }` data points
- Trade list: array of trade records (entry, exit, lot size, PnL, R-multiple, exit reason, duration)

### Tech Decisions

| Decision | Choice | Why |
|----------|--------|-----|
| Page location | `(dashboard)/backtest/page.tsx` | Inside existing auth-protected dashboard shell |
| Form library | react-hook-form + Zod | Already used in project; handles conditional fields cleanly |
| Chart library | Recharts | Spec-mandated; integrates well with shadcn/ui dark theme |
| Config persistence | localStorage | No server round-trip needed; single-user, single-device scope |
| Result state | React useState | Results are ephemeral until PROJ-9 adds persistence |
| Long-running UX | 30s timeout warning + cancel | Prevents confusion without streaming complexity in MVP |
| API design | New orchestration endpoint | Keeps UI simple; hides multi-step backend pipeline |

### Future Consideration (PROJ-6 or later)

If FastAPI processing regularly exceeds 30 seconds (e.g. large date ranges or tick-level data), replace the simple HTTP request + timeout with **Server-Sent Events (SSE)** or a **WebSocket** connection. This would allow a real progress bar with named stages ("Fetching data…", "Computing signals…", "Running engine…") instead of a generic spinner. The UI component boundary for this is already isolated in `LoadingState`, making the upgrade straightforward.

### New Dependencies

| Package | Purpose |
|---------|---------|
| `recharts` | Equity curve + drawdown charts |
| `date-fns` | Date formatting in chart tooltips and trade table |

(react-hook-form and Zod are already installed)

## QA Test Results
_To be added by /qa_

## Deployment
_To be added by /deploy_
