import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { createClient } from "@/lib/supabase/server";

export const maxDuration = 300; // Vercel Pro: up to 300s; Hobby: max 60s

const FASTAPI_URL = process.env.FASTAPI_URL;
const UPSTREAM_TIMEOUT_MS = 300_000; // 5 minutes
const RATE_LIMIT_MAX = 10;
const RATE_LIMIT_WINDOW_SECONDS = 60;

// ── Validation ───────────────────────────────────────────────────────────────

const BacktestRequestSchema = z
  .object({
    strategy: z.string().min(1),
    symbol: z.string().min(1).regex(/^[A-Z0-9.]+$/i),
    timeframe: z.enum(["1m", "5m", "15m", "1h", "1d"]),
    startDate: z.string().min(1),
    endDate: z.string().min(1),
    rangeStart: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
    rangeEnd: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
    triggerDeadline: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
    timeExit: z.string().regex(/^([01]\d|2[0-3]):[0-5]\d$/),
    stopLoss: z.number().positive(),
    takeProfit: z.number().positive(),
    direction: z.enum(["long", "short", "both"]),
    commission: z.number().min(0),
    slippage: z.number().min(0),
    initialCapital: z.number().positive(),
    sizingMode: z.enum(["risk_percent", "fixed_lot"]),
    riskPercent: z.number().min(0.01).max(100).optional(),
    fixedLot: z.number().positive().optional(),
  })
  .refine((data) => new Date(data.endDate) > new Date(data.startDate), {
    message: "End date must be after start date",
  })
  .refine(
    (data) =>
      data.sizingMode === "risk_percent"
        ? data.riskPercent != null
        : data.fixedLot != null,
    {
      message: "Provide risk_percent or fixed_lot based on sizing_mode",
    }
  );

// ── Route handler ────────────────────────────────────────────────────────────

export async function POST(request: NextRequest) {
  // Auth check
  const supabase = await createClient();
  const {
    data: { user },
    error: authError,
  } = await supabase.auth.getUser();

  if (authError || !user) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  // Rate limiting via Supabase (persistent across serverless instances)
  try {
    const { data: allowed, error: rlError } = await supabase.rpc(
      "check_rate_limit",
      {
        p_key: `backtest:${user.id}`,
        p_max_requests: RATE_LIMIT_MAX,
        p_window_seconds: RATE_LIMIT_WINDOW_SECONDS,
      }
    );

    if (rlError) {
      // Fail open: log but don't block the user if the DB check fails
      console.error("Rate limit check failed:", rlError.message);
    } else if (!allowed) {
      return NextResponse.json(
        { error: "Rate limit exceeded. Try again in 60 seconds." },
        {
          status: 429,
          headers: { "Retry-After": String(RATE_LIMIT_WINDOW_SECONDS) },
        }
      );
    }
  } catch (err) {
    // Fail open
    console.error("Rate limit check threw:", err);
  }

  // Parse body
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  // Validate
  const parsed = BacktestRequestSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Validation failed", details: parsed.error.flatten() },
      { status: 400 }
    );
  }

  if (!FASTAPI_URL) {
    return NextResponse.json(
      { error: "FastAPI service URL not configured" },
      { status: 503 }
    );
  }

  // Forward to FastAPI orchestration endpoint
  try {
    const {
      data: { session },
    } = await supabase.auth.getSession();

    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      "X-User-Id": user.id,
    };

    if (session?.access_token) {
      headers["Authorization"] = `Bearer ${session.access_token}`;
    }

    const response = await fetch(`${FASTAPI_URL}/backtest`, {
      method: "POST",
      headers,
      body: JSON.stringify(parsed.data),
      signal: AbortSignal.timeout(UPSTREAM_TIMEOUT_MS),
    });

    const data = await response.json();

    if (!response.ok) {
      return NextResponse.json(
        { error: data.detail || data.error || "Backtest failed" },
        { status: response.status }
      );
    }

    return NextResponse.json(data, { status: 200 });
  } catch (error) {
    if (error instanceof Error && error.name === "TimeoutError") {
      return NextResponse.json(
        { error: "Backtest timed out after 5 minutes. Try a shorter date range." },
        { status: 504 }
      );
    }
    console.error("Backtest orchestration error:", error);
    return NextResponse.json(
      { error: "Failed to connect to backtesting service" },
      { status: 502 }
    );
  }
}
