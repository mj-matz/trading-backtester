import { z } from "zod";

// ── Form Schema ──────────────────────────────────────────────────────────────

export const backtestFormSchema = z
  .object({
    strategy: z.string().min(1, "Strategy is required"),
    symbol: z
      .string()
      .min(1, "Symbol is required")
      .regex(/^[A-Z0-9.]+$/i, "Invalid symbol format"),
    timeframe: z.enum(["1m", "5m", "15m", "1h", "1d"]),
    startDate: z.string().min(1, "Start date is required"),
    endDate: z.string().min(1, "End date is required"),

    // Strategy parameters (Time-Range Breakout)
    rangeStart: z
      .string()
      .regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Must be HH:MM"),
    rangeEnd: z
      .string()
      .regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Must be HH:MM"),
    triggerDeadline: z
      .string()
      .regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Must be HH:MM"),
    timeExit: z
      .string()
      .regex(/^([01]\d|2[0-3]):[0-5]\d$/, "Must be HH:MM"),
    stopLoss: z.coerce.number().positive("Stop Loss must be > 0"),
    takeProfit: z.coerce.number().positive("Take Profit must be > 0"),
    direction: z.enum(["long", "short", "both"]),
    commission: z.coerce.number().min(0, "Commission must be >= 0"),
    slippage: z.coerce.number().min(0, "Slippage must be >= 0"),

    // Capital & sizing
    initialCapital: z.coerce.number().positive("Initial capital must be > 0"),
    sizingMode: z.enum(["risk_percent", "fixed_lot"]),
    riskPercent: z.coerce
      .number()
      .min(0.01, "Risk must be >= 0.01%")
      .max(100, "Risk must be <= 100%")
      .optional(),
    fixedLot: z.coerce.number().positive("Lot size must be > 0").optional(),
  })
  .refine((data) => new Date(data.endDate) > new Date(data.startDate), {
    message: "End date must be after start date",
    path: ["endDate"],
  })
  .refine(
    (data) =>
      data.sizingMode === "risk_percent"
        ? data.riskPercent != null
        : data.fixedLot != null,
    {
      message: "Provide risk % or fixed lot size based on selected sizing mode",
      path: ["riskPercent"],
    }
  );

export type BacktestFormValues = z.infer<typeof backtestFormSchema>;

// ── Default Form Values ──────────────────────────────────────────────────────

export const defaultFormValues: BacktestFormValues = {
  strategy: "time_range_breakout",
  symbol: "XAUUSD",
  timeframe: "1m",
  startDate: "",
  endDate: "",
  rangeStart: "02:00",
  rangeEnd: "06:00",
  triggerDeadline: "12:00",
  timeExit: "20:00",
  stopLoss: 150,
  takeProfit: 175,
  direction: "both",
  commission: 0,
  slippage: 0,
  initialCapital: 10000,
  sizingMode: "risk_percent",
  riskPercent: 1.0,
  fixedLot: undefined,
};

// ── API Response Types ───────────────────────────────────────────────────────

export interface BacktestMetrics {
  total_return_pct: number;
  cagr_pct: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  max_drawdown_pct: number;
  calmar_ratio: number;
  longest_drawdown_days: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  avg_win_pips: number;
  avg_loss_pips: number;
  profit_factor: number;
  avg_r_multiple: number;
  expectancy_pips: number;
  final_balance: number;
}

export interface EquityCurvePoint {
  date: string;
  balance: number;
}

export interface DrawdownCurvePoint {
  date: string;
  drawdown_pct: number;
}

export interface TradeRecord {
  id: number;
  entry_time: string;
  exit_time: string;
  direction: "long" | "short";
  entry_price: number;
  exit_price: number;
  lot_size: number;
  pnl_pips: number;
  pnl_currency: number;
  r_multiple: number;
  exit_reason: string;
  duration_minutes: number;
}

export interface SkippedDay {
  date: string;
  reason: string;
}

export interface BacktestResult {
  metrics: BacktestMetrics;
  equity_curve: EquityCurvePoint[];
  drawdown_curve: DrawdownCurvePoint[];
  trades: TradeRecord[];
  skipped_days: SkippedDay[];
}

// ── localStorage helpers ─────────────────────────────────────────────────────

const STORAGE_KEY = "backtest-config";

export function saveConfigToStorage(config: BacktestFormValues): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {
    // Silently fail if localStorage is unavailable
  }
}

export function loadConfigFromStorage(): BacktestFormValues | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    // Validate against schema - if invalid, return null
    const result = backtestFormSchema.safeParse(parsed);
    return result.success ? result.data : null;
  } catch {
    return null;
  }
}
