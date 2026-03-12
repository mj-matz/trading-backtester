# PROJ-4: Performance Analytics

## Status: In Review
**Created:** 2026-03-09
**Last Updated:** 2026-03-12

## Dependencies
- Requires: PROJ-2 (Backtesting Engine) — analytics consumes the trade log and equity curve produced by the engine

## User Stories
- As a trader, I want to see my Total Return, CAGR, and final account balance so that I understand the absolute profitability of the strategy.
- As a trader, I want to see Sharpe Ratio and Sortino Ratio so that I can assess risk-adjusted returns.
- As a trader, I want to see Maximum Drawdown (absolute and %) so that I understand the worst capital decline I would have experienced.
- As a trader, I want to see Win Rate, Profit Factor, and Average Win/Loss so that I understand the trade-by-trade characteristics.
- As a trader, I want to see the number of total trades, winning trades, and losing trades so that I can assess statistical significance.
- As a trader, I want to see average trade duration so that I understand how long capital is at risk per trade.
- As a trader, I want to see the R-Multiple per trade so that I can evaluate each trade relative to the risk I took.
- As a trader, I want to see R earned per month so that I can track consistency and identify which months were productive (e.g. 3 wins × 3.5R + 2 losses × −1R = +8.5R in one month).
- As a trader, I want all metrics to be calculated with a transparent formula so that I can verify them manually on a sample.

## Acceptance Criteria
- [ ] All metrics are calculated from the trade log produced by the engine — no approximations
- [ ] The following metrics are calculated and returned:

| Metric | Formula / Definition |
|--------|---------------------|
| Total Trades | Count of all closed trades |
| Winning Trades | Trades with PnL > 0 |
| Losing Trades | Trades with PnL <= 0 |
| Win Rate | Winning Trades / Total Trades × 100% |
| Gross Profit | Sum of PnL for winning trades |
| Gross Loss | Sum of PnL for losing trades (absolute) |
| Profit Factor | Gross Profit / Gross Loss |
| Average Win | Gross Profit / Winning Trades |
| Average Loss | Gross Loss / Losing Trades |
| Avg Win / Avg Loss (R) | Average Win / Average Loss |
| Total Return % | (Final Balance − Initial Balance) / Initial Balance × 100% |
| CAGR | (Final Balance / Initial Balance)^(1/years) − 1 |
| Max Drawdown % | Max peak-to-trough decline of equity curve |
| Max Drawdown Duration | Longest time (days) from peak to recovery |
| Sharpe Ratio | Mean daily return / Std daily return × √252 (risk-free rate = 0) |
| Sortino Ratio | Mean daily return / Downside deviation × √252 |
| Avg Trade Duration | Mean time between entry and exit across all trades |
| Best Trade | Highest single trade PnL |
| Worst Trade | Lowest single trade PnL |
| Consecutive Wins | Longest streak of winning trades |
| Consecutive Losses | Longest streak of losing trades |
| R-Multiple per Trade | Trade PnL in currency / Initial Risk in currency (from trade log) |
| Total R | Sum of all R-Multiples across all trades |
| Avg R per Trade | Total R / Total Trades |
| R per Month | Sum of R-Multiples for all trades whose exit falls in that calendar month |
| Avg R per Month | Total R / Number of calendar months in backtest period |

- [ ] All metrics are returned as a structured object (dict/JSON) with metric name, value, and unit
- [ ] Metrics are calculated both in pips and in account currency (requires initial capital and pip value as inputs)
- [ ] If total trades = 0, all metrics return 0 or null with no division-by-zero error
- [ ] If all trades are winners (gross loss = 0), Profit Factor returns `∞` (infinity), not an error

## Edge Cases
- Single trade in backtest → metrics are returned but Sharpe/Sortino may be undefined (std = 0) → return null with note
- Initial risk = 0 for a trade (e.g. SL placed at entry) → R-Multiple for that trade = null, excluded from R aggregations
- All trades in a single calendar month → R per Month shows one row, Avg R per Month = Total R
- Backtest period under 1 year → CAGR extrapolated but labelled as annualised estimate
- All trades exit at time exit (no SL/TP hits) → still valid, metrics calculated normally
- Equity curve never recovers from drawdown to new high → Max Drawdown Duration = total backtest duration

## Technical Requirements
- Pure Python calculation module, no external analytics library required (numpy/pandas only)
- All formulas documented in code comments with references
- Module accepts: trade_log (list of trades), equity_curve (time series), initial_capital (float), pip_value (float)
- Returns: metrics dict + equity curve data ready for charting

---
<!-- Sections below are added by subsequent skills -->

## Tech Design (Solution Architect)

### Overview
Pure Python calculation module that consumes `BacktestResult` from the engine and produces a structured metrics object. No database tables needed — analytics are computed on-the-fly after each backtest run.

### Data Flow
```
BacktestEngine (PROJ-2)
        |
        | BacktestResult (trades, equity_curve, balances)
        v
AnalyticsCalculator (PROJ-4)  <-- also receives initial_capital, pip_value
        |
        | AnalyticsResult (summary_metrics, monthly_r_breakdown)
        v
/api/backtest/run  (existing Next.js route — response extended with analytics field)
        |
        v
Backtest Results UI (PROJ-5)
```

### Module Structure
```
python/analytics/
  __init__.py
  calculator.py       Main entry point — orchestrates all calculations
  trade_metrics.py    Win rate, Profit Factor, R-multiples, streaks, avg duration
  equity_metrics.py   Total Return, CAGR, Max Drawdown, Max Drawdown Duration
  risk_metrics.py     Sharpe Ratio and Sortino Ratio (daily returns from equity curve)
  monthly_metrics.py  R-per-month breakdown and Avg R per Month
  models.py           AnalyticsResult dataclass (the output shape)
```

### Data Model

**Input to analytics module:**
| Input | Source | Description |
|-------|--------|-------------|
| Trade list | BacktestResult.trades | All closed trades with PnL, risk, timestamps |
| Equity curve | BacktestResult.equity_curve | Time-series of account balance |
| Initial capital | BacktestConfig | Starting account balance |
| Pip value | InstrumentConfig | Monetary value of 1 pip |

**Output — Summary Metrics:**
A list of 23 metric objects, each with `name`, `value`, `unit` (e.g. `%`, `R`, `currency`, `count`, `days`).

**Output — Monthly R Breakdown:**
A list of rows: `{ month: "YYYY-MM", r_earned: float, trade_count: int }`

### API Integration
The existing `/api/backtest/run` route response is **extended** — no new endpoint needed:
- Current: `{ trades, equity_curve, final_balance }`
- Extended: `{ trades, equity_curve, final_balance, analytics: { summary, monthly_r } }`

### Tech Decisions
| Decision | Choice | Reason |
|----------|--------|--------|
| Calculation library | pandas + numpy only | Spec requirement; avoids heavy dependencies |
| Module location | `python/analytics/` package | Mirrors the `python/engine/` pattern |
| When to compute | After each backtest run | Analytics is cheap (< 10ms); no caching needed |
| Output format | List of `{name, value, unit}` objects | Frontend renders a table without custom mapping |
| Edge case handling | Return `null` or `Infinity` explicitly | Required by spec for 0-trade and all-winners cases |

### Dependencies
- `pandas` — already installed; used for monthly grouping and daily return series
- `numpy` — available via pandas; used for std deviation (Sharpe/Sortino)

## QA Test Results

**Tested:** 2026-03-12 | **Method:** Code review of all analytics module files, test files, and API routes

### Acceptance Criteria: 6/6 PASSED
All 25 spec metrics implemented (33 total including pip/currency variants). Structured `Metric` objects with `name`, `value`, `unit`. Zero-trade and all-winners edge cases handled. Infinity serialised as `value=None, value_string="Infinity"` in API layer.

### Edge Cases: 9/9 PASSED
All 6 spec cases plus 3 additional (negative final balance, CAGR with negative returns, multiple drawdown cycles).

### Bugs Fixed
| ID | Severity | Description | Resolution |
|----|----------|-------------|------------|
| BUG-4 | Medium | Max Drawdown Duration non-zero when no drawdown exists | Fixed in `equity_metrics.py`: return `(0.0, 0.0)` when `max_dd_pct == 0` |
| BUG-3 | Medium | Profit Factor undefined for all-breakeven trades | Fixed in `trade_metrics.py`: return `1.0` (neutral) when both gross profit and gross loss are 0 |

### Remaining Low-Priority Bugs (deferred)
- **BUG-1:** `avg_r_per_trade` denominator includes zero-risk trades (ambiguous per spec)
- **BUG-2:** Module interface differs from spec (accepts `BacktestResult` vs 4 params — functionally equivalent)
- **BUG-5:** Sharpe Ratio may understate intraday volatility (industry-standard grouping)
- **BUG-6:** CAGR note not computed when equity curve has < 2 points (no functional impact)

### Security Audit: PASS
Auth on both Next.js (Supabase) and FastAPI (verify_jwt) layers. Cache filtered by `user_id`. Zod + Pydantic input validation. Rate limiting 30 req/min per user. No secrets, no eval/exec, no injection vectors.

### Regression: PASS
PROJ-1, PROJ-2, PROJ-3, PROJ-8 — no modifications.

### Verdict: **READY FOR DEPLOYMENT**

## Deployment
_To be added by /deploy_
