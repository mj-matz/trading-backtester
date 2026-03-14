"use client";

import {
  AlertCircle,
  BarChart3,
  Loader2,
  Clock,
  BookmarkPlus,
} from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

import { MetricsSummaryCard } from "@/components/backtest/metrics-summary-card";
import { EquityCurveChart } from "@/components/backtest/equity-curve-chart";
import { DrawdownChart } from "@/components/backtest/drawdown-chart";
import { TradeListTable } from "@/components/backtest/trade-list-table";

import type { BacktestResult } from "@/lib/backtest-types";
import type { BacktestStatus } from "@/hooks/use-backtest";

interface ResultsPanelProps {
  status: BacktestStatus;
  result: BacktestResult | null;
  error: string | null;
  isTimedOut: boolean;
  onCancel: () => void;
  initialCapital: number;
}

function EmptyState() {
  return (
    <Card className="border-gray-800 bg-[#111118]">
      <CardContent className="flex flex-col items-center justify-center py-20">
        <BarChart3 className="mb-4 h-12 w-12 text-gray-600" />
        <h3 className="text-lg font-medium text-gray-300">No Results Yet</h3>
        <p className="mt-2 text-center text-sm text-gray-500">
          Configure your backtest parameters and click &quot;Run Backtest&quot;
          to see results here.
        </p>
      </CardContent>
    </Card>
  );
}

interface LoadingStateProps {
  isTimedOut: boolean;
  onCancel: () => void;
}

function LoadingState({ isTimedOut, onCancel }: LoadingStateProps) {
  return (
    <Card className="border-gray-800 bg-[#111118]">
      <CardContent className="flex flex-col items-center justify-center py-20">
        <Loader2 className="mb-4 h-10 w-10 animate-spin text-blue-500" />
        <h3 className="text-lg font-medium text-gray-300">
          Running backtest...
        </h3>
        <p className="mt-2 text-sm text-gray-500">
          Processing your configuration. This may take a moment.
        </p>
        {isTimedOut && (
          <div className="mt-6 text-center">
            <div className="mb-3 flex items-center justify-center gap-2">
              <Clock className="h-4 w-4 text-yellow-400" />
              <span className="text-sm text-yellow-400">
                This is taking longer than expected...
              </span>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={onCancel}
              className="border-gray-700 text-gray-300 hover:bg-gray-800"
              aria-label="Cancel backtest"
            >
              Cancel
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

interface ErrorStateProps {
  error: string;
}

function ErrorState({ error }: ErrorStateProps) {
  return (
    <Alert
      variant="destructive"
      className="border-red-900/50 bg-red-950/30 text-red-300"
    >
      <AlertCircle className="h-4 w-4" />
      <AlertTitle>Backtest Failed</AlertTitle>
      <AlertDescription className="mt-2 text-red-300/80">
        {error}
      </AlertDescription>
    </Alert>
  );
}

function NoTradesState() {
  return (
    <Card className="border-gray-800 bg-[#111118]">
      <CardContent className="flex flex-col items-center justify-center py-16">
        <BarChart3 className="mb-4 h-10 w-10 text-gray-600" />
        <h3 className="text-lg font-medium text-gray-300">No Trades Found</h3>
        <p className="mt-2 text-center text-sm text-gray-500">
          No trades were generated for this period and configuration. Try
          adjusting your parameters or date range.
        </p>
      </CardContent>
    </Card>
  );
}

export function ResultsPanel({
  status,
  result,
  error,
  isTimedOut,
  onCancel,
  initialCapital,
}: ResultsPanelProps) {
  if (status === "loading") {
    return <LoadingState isTimedOut={isTimedOut} onCancel={onCancel} />;
  }

  if (status === "error" && error) {
    return <ErrorState error={error} />;
  }

  if (!result) {
    return <EmptyState />;
  }

  if (result.trades.length === 0) {
    return <NoTradesState />;
  }

  return (
    <div className="space-y-6">
      {/* Metrics Summary (always visible) */}
      <MetricsSummaryCard metrics={result.metrics} initialCapital={initialCapital} />

      {/* Tabbed content: Charts and Trade List */}
      <Tabs defaultValue="charts" className="w-full">
        <TabsList className="border-gray-800 bg-[#111118]">
          <TabsTrigger
            value="charts"
            className="data-[state=active]:bg-gray-800 data-[state=active]:text-white"
          >
            Charts
          </TabsTrigger>
          <TabsTrigger
            value="trades"
            className="data-[state=active]:bg-gray-800 data-[state=active]:text-white"
          >
            Trades ({result.trades.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="charts" className="mt-4 space-y-6">
          <EquityCurveChart data={result.equity_curve} />
          <DrawdownChart data={result.drawdown_curve} />
        </TabsContent>

        <TabsContent value="trades" className="mt-4">
          <TradeListTable trades={result.trades} skippedDays={result.skipped_days ?? []} />
        </TabsContent>
      </Tabs>

      {/* Save Run placeholder (PROJ-9) */}
      <div className="flex justify-end">
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="outline"
                disabled
                className="border-gray-700 text-gray-500"
                aria-label="Save run - coming soon"
              >
                <BookmarkPlus className="mr-2 h-4 w-4" />
                Save Run
                <Badge
                  variant="secondary"
                  className="ml-2 bg-gray-800 text-gray-500"
                >
                  Coming Soon
                </Badge>
              </Button>
            </TooltipTrigger>
            <TooltipContent className="border-gray-700 bg-gray-900 text-gray-300">
              <p>Backtest history will be available in a future update.</p>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    </div>
  );
}
