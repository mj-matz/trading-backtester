"use client";

import { useState, useMemo } from "react";
import { format, parseISO } from "date-fns";
import { ArrowUpDown, ChevronLeft, ChevronRight, EyeOff, Eye } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { TradeRecord, SkippedDay } from "@/lib/backtest-types";

interface TradeListTableProps {
  trades: TradeRecord[];
  skippedDays?: SkippedDay[];
}

type SortField = "entry_time" | "pnl_pips" | "duration_minutes";
type SortDir = "asc" | "desc";

type Row =
  | { kind: "trade"; data: TradeRecord }
  | { kind: "skipped"; data: SkippedDay };

const PAGE_SIZE = 50;

function formatDate(dateStr: string): string {
  try {
    return format(parseISO(dateStr), "MMM dd, yyyy HH:mm");
  } catch {
    return dateStr;
  }
}

function formatDateShort(dateStr: string): string {
  try {
    return format(parseISO(dateStr), "MMM dd, yyyy");
  } catch {
    return dateStr;
  }
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours < 24) return `${hours}h ${mins}m`;
  const days = Math.floor(hours / 24);
  const remainHours = hours % 24;
  return `${days}d ${remainHours}h`;
}

const REASON_LABELS: Record<string, string> = {
  NO_BARS: "No bars",
  NO_RANGE_BARS: "No range bars",
  FLAT_RANGE: "Flat range",
  NO_SIGNAL_BAR: "No signal bar",
  DEADLINE_MISSED: "Deadline missed",
};

export function TradeListTable({ trades, skippedDays = [] }: TradeListTableProps) {
  const [sortField, setSortField] = useState<SortField>("entry_time");
  const [sortDir, setSortDir] = useState<SortDir>("asc");
  const [page, setPage] = useState(0);
  const [showNoTrade, setShowNoTrade] = useState(true);

  const sortedTrades = useMemo(() => {
    return [...trades].sort((a, b) => {
      const aVal = a[sortField];
      const bVal = b[sortField];
      if (typeof aVal === "string" && typeof bVal === "string") {
        return sortDir === "asc"
          ? aVal.localeCompare(bVal)
          : bVal.localeCompare(aVal);
      }
      const numA = Number(aVal);
      const numB = Number(bVal);
      return sortDir === "asc" ? numA - numB : numB - numA;
    });
  }, [trades, sortField, sortDir]);

  // Merge trades and skipped days into chronological list (only when sorting by entry_time)
  const mergedRows = useMemo<Row[]>(() => {
    if (sortField !== "entry_time" || !showNoTrade || skippedDays.length === 0) {
      return sortedTrades.map((t) => ({ kind: "trade", data: t }));
    }

    const tradeRows: Row[] = sortedTrades.map((t) => ({ kind: "trade", data: t }));
    const skippedRows: Row[] = skippedDays.map((s) => ({ kind: "skipped", data: s }));

    const all = [...tradeRows, ...skippedRows].sort((a, b) => {
      const dateA = a.kind === "trade" ? a.data.entry_time : a.data.date;
      const dateB = b.kind === "trade" ? b.data.entry_time : b.data.date;
      const cmp = dateA.localeCompare(dateB);
      return sortDir === "asc" ? cmp : -cmp;
    });

    return all;
  }, [sortedTrades, skippedDays, sortField, sortDir, showNoTrade]);

  // Only real trades count for pagination page sizing; skipped days fill the view
  const tradeOnlyRows = mergedRows.filter((r) => r.kind === "trade");
  const totalPages = Math.ceil(tradeOnlyRows.length / PAGE_SIZE);

  // Paginate by trade index: show PAGE_SIZE trades per page plus any skipped days between them
  const paginatedRows = useMemo(() => {
    if (sortField !== "entry_time" || !showNoTrade || skippedDays.length === 0) {
      return mergedRows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    }

    // Find the entry_time range of trades on this page
    const pageTradeStart = page * PAGE_SIZE;
    const pageTradeEnd = Math.min((page + 1) * PAGE_SIZE, tradeOnlyRows.length);
    if (pageTradeStart >= tradeOnlyRows.length) return [];

    const firstTradeTime =
      (tradeOnlyRows[pageTradeStart].data as TradeRecord).entry_time;
    const lastTradeTime =
      (tradeOnlyRows[pageTradeEnd - 1].data as TradeRecord).entry_time;

    return mergedRows.filter((row) => {
      const t = row.kind === "trade" ? row.data.entry_time : row.data.date;
      return sortDir === "asc"
        ? t >= firstTradeTime && t <= lastTradeTime
        : t <= firstTradeTime && t >= lastTradeTime;
    });
  }, [mergedRows, tradeOnlyRows, page, sortField, sortDir, showNoTrade, skippedDays.length]);

  function toggleSort(field: SortField) {
    if (sortField === field) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortField(field);
      setSortDir("asc");
    }
    setPage(0);
  }

  return (
    <Card className="border-gray-800 bg-[#111118]">
      <CardHeader className="pb-3">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base text-gray-100">
            Trade List ({trades.length} trades
            {skippedDays.length > 0 && (
              <span className="ml-1 text-gray-500 font-normal">
                · {skippedDays.length} no-trade days
              </span>
            )}
            )
          </CardTitle>
          <div className="flex items-center gap-1">
            {skippedDays.length > 0 && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => { setShowNoTrade((v) => !v); setPage(0); }}
                className="h-7 px-2 text-xs text-gray-400 hover:text-white"
                aria-label={showNoTrade ? "Hide no-trade days" : "Show no-trade days"}
              >
                {showNoTrade ? (
                  <EyeOff className="mr-1 h-3 w-3" />
                ) : (
                  <Eye className="mr-1 h-3 w-3" />
                )}
                {showNoTrade ? "Hide NT" : "Show NT"}
              </Button>
            )}
            <Button
              variant="ghost"
              size="sm"
              onClick={() => toggleSort("entry_time")}
              className="h-7 px-2 text-xs text-gray-400 hover:text-white"
              aria-label="Sort by date"
            >
              Date <ArrowUpDown className="ml-1 h-3 w-3" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => toggleSort("pnl_pips")}
              className="h-7 px-2 text-xs text-gray-400 hover:text-white"
              aria-label="Sort by PnL"
            >
              PnL <ArrowUpDown className="ml-1 h-3 w-3" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => toggleSort("duration_minutes")}
              className="h-7 px-2 text-xs text-gray-400 hover:text-white"
              aria-label="Sort by duration"
            >
              Duration <ArrowUpDown className="ml-1 h-3 w-3" />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow className="border-gray-800 hover:bg-transparent">
                <TableHead className="text-gray-400">#</TableHead>
                <TableHead className="text-gray-400">Date</TableHead>
                <TableHead className="text-gray-400">Dir</TableHead>
                <TableHead className="text-right text-gray-400">Entry</TableHead>
                <TableHead className="text-right text-gray-400">Exit</TableHead>
                <TableHead className="text-right text-gray-400">Lot</TableHead>
                <TableHead className="text-right text-gray-400">PnL (pips)</TableHead>
                <TableHead className="text-right text-gray-400">PnL ($)</TableHead>
                <TableHead className="text-right text-gray-400">R</TableHead>
                <TableHead className="text-gray-400">Exit Reason</TableHead>
                <TableHead className="text-right text-gray-400">Duration</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {paginatedRows.map((row) => {
                if (row.kind === "skipped") {
                  const s = row.data;
                  return (
                    <TableRow
                      key={`skipped-${s.date}`}
                      className="border-gray-800/50 opacity-60 hover:opacity-80"
                    >
                      <TableCell className="text-gray-600">—</TableCell>
                      <TableCell className="whitespace-nowrap text-sm text-gray-500">
                        {formatDateShort(s.date)}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className="border-gray-700 text-gray-500 text-[10px] px-1"
                        >
                          NT
                        </Badge>
                      </TableCell>
                      <TableCell />
                      <TableCell />
                      <TableCell />
                      <TableCell />
                      <TableCell />
                      <TableCell />
                      <TableCell>
                        <span className="text-xs text-gray-600 italic">
                          {REASON_LABELS[s.reason] ?? s.reason}
                        </span>
                      </TableCell>
                      <TableCell />
                    </TableRow>
                  );
                }

                const trade = row.data;
                return (
                  <TableRow
                    key={trade.id}
                    className="border-gray-800 hover:bg-gray-900/50"
                  >
                    <TableCell className="text-gray-500">{trade.id}</TableCell>
                    <TableCell className="whitespace-nowrap text-sm text-gray-300">
                      {formatDate(trade.entry_time)}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant={trade.direction === "long" ? "default" : "secondary"}
                        className={
                          trade.direction === "long"
                            ? "bg-green-900/50 text-green-300 hover:bg-green-900/50"
                            : "bg-red-900/50 text-red-300 hover:bg-red-900/50"
                        }
                      >
                        {trade.direction === "long" ? "L" : "S"}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right text-sm text-gray-300">
                      {trade.entry_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right text-sm text-gray-300">
                      {trade.exit_price.toFixed(2)}
                    </TableCell>
                    <TableCell className="text-right text-sm text-gray-300">
                      {trade.lot_size.toFixed(2)}
                    </TableCell>
                    <TableCell
                      className={`text-right text-sm font-medium ${
                        trade.pnl_pips >= 0 ? "text-green-400" : "text-red-400"
                      }`}
                    >
                      {trade.pnl_pips >= 0 ? "+" : ""}
                      {trade.pnl_pips.toFixed(1)}
                    </TableCell>
                    <TableCell
                      className={`text-right text-sm font-medium ${
                        trade.pnl_currency >= 0 ? "text-green-400" : "text-red-400"
                      }`}
                    >
                      {trade.pnl_currency >= 0 ? "+" : ""}
                      {trade.pnl_currency.toFixed(2)}
                    </TableCell>
                    <TableCell
                      className={`text-right text-sm ${
                        trade.r_multiple >= 0 ? "text-green-400" : "text-red-400"
                      }`}
                    >
                      {trade.r_multiple >= 0 ? "+" : ""}
                      {trade.r_multiple.toFixed(2)}R
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className="border-gray-700 text-gray-400"
                      >
                        {trade.exit_reason}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-right text-sm text-gray-400">
                      {formatDuration(trade.duration_minutes)}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="mt-4 flex items-center justify-between">
            <p className="text-sm text-gray-500">
              Page {page + 1} of {totalPages}
            </p>
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.max(0, p - 1))}
                disabled={page === 0}
                className="border-gray-700 text-gray-300 hover:bg-gray-800"
                aria-label="Previous page"
              >
                <ChevronLeft className="mr-1 h-4 w-4" />
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage((p) => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="border-gray-700 text-gray-300 hover:bg-gray-800"
                aria-label="Next page"
              >
                Next
                <ChevronRight className="ml-1 h-4 w-4" />
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
