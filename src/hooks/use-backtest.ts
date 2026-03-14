"use client";

import { useState, useRef, useCallback } from "react";
import type { BacktestFormValues, BacktestResult } from "@/lib/backtest-types";

export type BacktestStatus = "idle" | "loading" | "success" | "error";

interface UseBacktestReturn {
  status: BacktestStatus;
  result: BacktestResult | null;
  error: string | null;
  isTimedOut: boolean;
  runBacktest: (config: BacktestFormValues) => Promise<void>;
  cancel: () => void;
}

const TIMEOUT_WARNING_MS = 60_000;

export function useBacktest(): UseBacktestReturn {
  const [status, setStatus] = useState<BacktestStatus>("idle");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isTimedOut, setIsTimedOut] = useState(false);

  const abortControllerRef = useRef<AbortController | null>(null);
  const timeoutTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancel = useCallback(() => {
    abortControllerRef.current?.abort();
    if (timeoutTimerRef.current) {
      clearTimeout(timeoutTimerRef.current);
      timeoutTimerRef.current = null;
    }
    setStatus("idle");
    setIsTimedOut(false);
  }, []);

  const runBacktest = useCallback(
    async (config: BacktestFormValues) => {
      // Cancel any in-progress request
      abortControllerRef.current?.abort();
      if (timeoutTimerRef.current) {
        clearTimeout(timeoutTimerRef.current);
      }

      const controller = new AbortController();
      abortControllerRef.current = controller;

      setStatus("loading");
      setError(null);
      setIsTimedOut(false);

      // Set timeout warning
      timeoutTimerRef.current = setTimeout(() => {
        setIsTimedOut(true);
      }, TIMEOUT_WARNING_MS);

      try {
        const response = await fetch("/api/backtest", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(config),
          signal: controller.signal,
        });

        if (!response.ok) {
          const body = await response.json().catch(() => ({}));
          throw new Error(
            body.error || `Backtest failed with status ${response.status}`
          );
        }

        const data: BacktestResult = await response.json();
        setResult(data);
        setStatus("success");
      } catch (err) {
        if ((err as Error).name === "AbortError") {
          setStatus("idle");
          return;
        }
        setError(
          err instanceof Error ? err.message : "An unexpected error occurred"
        );
        setStatus("error");
      } finally {
        if (timeoutTimerRef.current) {
          clearTimeout(timeoutTimerRef.current);
          timeoutTimerRef.current = null;
        }
        setIsTimedOut(false);
      }
    },
    []
  );

  return { status, result, error, isTimedOut, runBacktest, cancel };
}
