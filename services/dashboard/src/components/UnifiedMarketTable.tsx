"use client";

import { Fragment, useState, useMemo, useEffect, useRef, useCallback } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from "recharts";
import type {
  ApiClient,
  Trade,
  Market,
  Position,
  UnifiedMarketRow,
  PriceHistoryPoint,
  ModelRun,
  PendingOrder,
} from "@/lib/api";
import { buildUnifiedMarketRows, liveNetPnl, totalPnl, kalshiMarketUrl, kalshiEventUrl, api } from "@/lib/api";
import { pnlCls, fmtDollar, fmtTime, TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE, CHART_COLORS } from "@/lib/utils";

// ── Shared helpers ──────────────────────────────────────────

function shortModelName(name: string): string {
  const parts = name.split(":");
  const hasMarket = parts.length >= 3 && ["market", "mkt", "prices"].includes(parts[parts.length - 1].toLowerCase());
  const raw = hasMarket ? parts[parts.length - 2] : parts[parts.length - 1];
  const short = raw
    .replace(/-preview$/, "")
    .replace(/^gemini-/, "g-")
    .replace(/^gpt-/, "")
    .replace(/^claude-/, "c-")
    .slice(0, 14);
  return hasMarket ? `${short}*` : short;
}

const MODEL_COLORS = [
  "text-blue-400",
  "text-amber-400",
  "text-emerald-400",
  "text-purple-400",
  "text-rose-400",
  "text-cyan-400",
];
const TIMELINE_MODEL_CLASS = "text-accent";

const CATEGORY_COLORS: Record<string, string> = {
  POLITICS:      "bg-blue-900/40 border-blue-700/50 text-blue-300",
  ECONOMICS:     "bg-emerald-900/40 border-emerald-700/50 text-emerald-300",
  FINANCIALS:    "bg-green-900/40 border-green-700/50 text-green-300",
  SPORTS:        "bg-orange-900/40 border-orange-700/50 text-orange-300",
  ENTERTAINMENT: "bg-purple-900/40 border-purple-700/50 text-purple-300",
  TECHNOLOGY:    "bg-cyan-900/40 border-cyan-700/50 text-cyan-300",
  SCIENCE:       "bg-teal-900/40 border-teal-700/50 text-teal-300",
  WEATHER:       "bg-sky-900/40 border-sky-700/50 text-sky-300",
  CRYPTO:        "bg-yellow-900/40 border-yellow-700/50 text-yellow-300",
  MENTIONS:      "bg-red-900/50 border-red-600/60 text-red-300",
};
function categoryChipClass(cat: string | null) {
  if (!cat) return "bg-t-panel-alt border-t-border/60 text-txt-muted";
  return CATEGORY_COLORS[cat.toUpperCase()] ?? "bg-t-panel-alt border-t-border/60 text-txt-muted";
}

function normalizedMarketStatus(status: string | null | undefined): string {
  return (status ?? "").trim().toLowerCase();
}

function marketStatusChipClass(status: string | null | undefined, result: string | null | undefined): string {
  const normalizedResult = (result ?? "").trim().toLowerCase();
  if (normalizedResult === "yes" || normalizedResult === "no") {
    return "bg-accent-dim border-accent/40 text-accent";
  }

  switch (normalizedMarketStatus(status)) {
    case "closed":
      return "bg-loss-dim border-loss/30 text-loss";
    case "inactive":
      return "bg-warn-dim border-warn/30 text-warn";
    case "active":
    case "open":
      return "bg-profit-dim border-profit/30 text-profit";
    default:
      return "bg-t-panel-alt border-t-border/60 text-txt-muted";
  }
}

function marketStatusLabel(status: string | null | undefined, result: string | null | undefined): string | null {
  const normalizedResult = (result ?? "").trim().toLowerCase();
  if (normalizedResult === "yes" || normalizedResult === "no") {
    return `resolved ${normalizedResult}`;
  }

  const normalizedStatus = normalizedMarketStatus(status);
  if (!normalizedStatus || normalizedStatus === "active" || normalizedStatus === "open") {
    return null;
  }
  return normalizedStatus;
}

function sideToneClass(side: string | null | undefined, dim = false): string {
  const normalized = side?.toUpperCase();
  if (normalized === "YES") return dim ? "text-profit font-bold" : "text-profit font-bold";
  if (normalized === "NO") return dim ? "text-loss font-bold" : "text-loss font-bold";
  return dim ? "text-txt-muted" : "text-txt-primary";
}

function holdEdgeToneClass(): string {
  return "text-yellow-400/70";
}

function formatPriceCents(priceCents: number | null | undefined): string | null {
  if (priceCents == null || Number.isNaN(priceCents) || priceCents <= 0) return null;
  return `@ ${Math.round(priceCents)}c`;
}

function isHoldLikeDecision(decision: string | null | undefined): boolean {
  return decision?.toUpperCase() === "HOLD";
}

function isSkipLikeDecision(decision: string | null | undefined): boolean {
  const upper = decision?.toUpperCase() ?? "";
  return upper === "SKIP" || upper === "CYCLE_SKIPPED" || upper === "NO_PREDICTION";
}

function normalizedDecisionLabel(decision: string | null | undefined): "BUY" | "SELL" | "HOLD" | "SKIP" {
  const upper = decision?.toUpperCase() ?? "";
  if (upper.startsWith("SELL")) return "SELL";
  if (upper.startsWith("BUY")) return "BUY";
  if (isSkipLikeDecision(upper)) return "SKIP";
  return "HOLD";
}

function decisionSide(decision: string | null | undefined): string | null {
  const upper = decision?.toUpperCase() ?? "";
  if (upper.endsWith("_YES")) return "YES";
  if (upper.endsWith("_NO")) return "NO";
  return null;
}

function hasLivePendingExposure(row: UnifiedMarketRow): boolean {
  return (row.pending_orders ?? []).some((order) => (order.count - order.filled_shares) > 0);
}

function pendingOrderMatchesRun(order: PendingOrder, run: ModelRun): boolean {
  const runSide = decisionSide(run.decision);
  const runAction = normalizedDecisionLabel(run.decision);
  if (!runSide || (runAction !== "BUY" && runAction !== "SELL")) return false;
  const orderSide = order.side?.toUpperCase() ?? "";
  const orderAction = order.action?.toUpperCase() ?? "";
  if (runSide !== orderSide || runAction !== orderAction) return false;
  const runTs = new Date(run.timestamp).getTime();
  const orderTs = new Date(order.created_at).getTime();
  if (Number.isNaN(runTs) || Number.isNaN(orderTs)) return false;
  return Math.abs(runTs - orderTs) <= 15 * 60 * 1000;
}

function rowHasActivity(row: UnifiedMarketRow): boolean {
  return (
    row.has_position
    || row.has_trades
    || row.trade_count > 0
    || (row.pending_shares != null && row.pending_shares > 0)
    || hasLivePendingExposure(row)
  );
}

function tradeFeeTotal(row: UnifiedMarketRow): number {
  return row.position?.fees_paid ?? row.fees_paid_total ?? row.trades.reduce((sum, trade) => sum + (trade.fee_paid || 0), 0);
}

function totalInvestment(row: UnifiedMarketRow): number {
  return row.position ? (row.position.total_cost ?? row.position.capital) + tradeFeeTotal(row) : 0;
}

function formatFeeLabel(fee: number): string {
  return `fee: ${fmtDollar(fee)}`;
}

function formatInvestmentSplit(base: number, fee: number): string {
  return `cost:${fmtDollar(base)} fee:${fmtDollar(fee)}`;
}

function formatPendingDelta(delta: number): string {
  const rounded = Math.round(delta);
  if (rounded > 0) return `+${rounded} pending`;
  if (rounded < 0) return `${rounded} pending`;
  return "0 pending";
}

function pendingHoldDeltaForDisplay(row: UnifiedMarketRow): number | null {
  if (row.pending_shares == null || row.pending_shares <= 0) return null;

  if (row.position && row.target_shares != null) {
    const holdDelta = row.target_shares - row.position.quantity;
    if (Math.round(holdDelta) !== 0 || row.pending_delta_shares == null) {
      return holdDelta;
    }
  }

  if (!row.position && row.target_shares != null) {
    return row.target_shares;
  }

  return row.pending_delta_shares;
}

function sameEdgeValue(a: number | null | undefined, b: number | null | undefined): boolean {
  return a != null && b != null && Math.abs(a - b) <= 0.0005;
}

function isSyntheticTrade(trade: Trade): boolean {
  const source = trade.prediction?.source?.toLowerCase() ?? "";
  return source.startsWith("kalshi:");
}

function isDeferredFlipTrade(trade: Trade): boolean {
  return trade.synthetic_kind === "DEFERRED_FLIP";
}

function tradePendingNote(trade: Trade): string | null {
  if (!isDeferredFlipTrade(trade)) return null;
  return trade.pending_reason ?? "Queued as the next leg after the sell fills.";
}

function tradeModeBadge(trade: Trade): { label: string; className: string } {
  if (isDeferredFlipTrade(trade)) {
    return {
      label: "NEXT",
      className: "bg-accent-dim text-accent",
    };
  }
  if (trade.dry_run) {
    return {
      label: "DRY",
      className: "bg-warn-dim text-warn",
    };
  }
  return {
    label: "LIVE",
    className: "bg-profit-dim text-profit",
  };
}

function formatShareLabel(count: number): string {
  return `${count} share${Math.abs(count) === 1 ? "" : "s"}`;
}

type SortKey =
  | "title"
  | "yes_ask"
  | "predicted"
  | "edge"
  | "position"
  | "avg_price"
  | "unrealized"
  | "total_pnl"
  | "capital"
  | "last_trade"
  | "return"
  | "expiration";

type FilterMode = "all" | "has_position";
type ViewMode = "markets" | "events";
type EventSortKey = "title" | "market_count" | "position_count" | "total_trade_count" | "total_capital" | "total_pnl" | "last_trade_time";
type EventGroupRow = {
  event_key: string;
  event_ticker: string;
  title: string;
  category: string | null;
  rows: UnifiedMarketRow[];
  market_count: number;
  position_count: number;
  total_pnl: number;
  total_capital: number;
  total_trade_count: number;
  last_trade_time: string | null;
};

const PAGE_SIZE = 25;

// ── Main Component ──────────────────────────────────────────

export function UnifiedMarketTable({
  markets,
  positions,
  trades,
  apiClient,
  instanceCacheKey,
  scrollToMarketId,
  onScrollComplete,
}: {
  markets: Market[];
  positions: Position[];
  trades: Trade[];
  apiClient: ApiClient;
  instanceCacheKey: string;
  scrollToMarketId?: string | null;
  onScrollComplete?: () => void;
}) {
  const [sortKeys, setSortKeys] = useState<Array<{ key: SortKey; asc: boolean }>>(
    [{ key: "unrealized", asc: false }]
  );
  const [eventSortKeys, setEventSortKeys] = useState<Array<{ key: EventSortKey; asc: boolean }>>(
    [{ key: "total_pnl", asc: false }]
  );
  const [search, setSearch] = useState("");
  const [viewMode, setViewMode] = useState<ViewMode>("markets");
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [categoryFilter, setCategoryFilter] = useState<Set<string>>(new Set());
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const toggleCategory = (cat: string) => {
    setCategoryFilter((prev) => {
      const next = new Set(prev);
      next.has(cat) ? next.delete(cat) : next.add(cat);
      return next;
    });
    setVisibleCount(PAGE_SIZE);
  };
  const [expandedMarketId, setExpandedMarketId] = useState<string | null>(null);
  const [expandedEventKey, setExpandedEventKey] = useState<string | null>(null);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());

  const rows = useMemo(
    () => buildUnifiedMarketRows(markets, positions, trades),
    [markets, positions, trades]
  );

  const allCategories = useMemo(() => {
    const cats = new Set<string>();
    rows.forEach((r) => { if (r.category) cats.add(r.category.toUpperCase()); });
    return Array.from(cats).sort();
  }, [rows]);

  const handleSort = (key: SortKey, multi: boolean) => {
    setSortKeys((prev) => {
      const idx = prev.findIndex((s) => s.key === key);
      if (multi) {
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = { key, asc: !prev[idx].asc };
          return updated;
        }
        return [...prev, { key, asc: false }];
      }
      if (idx === 0 && prev.length === 1) return [{ key, asc: !prev[0].asc }];
      return [{ key, asc: false }];
    });
  };

  const handleEventSort = (key: EventSortKey, multi: boolean) => {
    setEventSortKeys((prev) => {
      const idx = prev.findIndex((s) => s.key === key);
      if (multi) {
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = { key, asc: !prev[idx].asc };
          return updated;
        }
        return [...prev, { key, asc: false }];
      }
      if (idx === 0 && prev.length === 1) return [{ key, asc: !prev[0].asc }];
      return [{ key, asc: false }];
    });
  };

  const computeMarketDiff = useCallback((key: SortKey, a: UnifiedMarketRow, b: UnifiedMarketRow): number | null => {
    switch (key) {
      case "title": return a.title.localeCompare(b.title);
      case "yes_ask": return (a.yes_ask ?? 0) - (b.yes_ask ?? 0);
      case "predicted": return (a.aggregated_p_yes ?? 0) - (b.aggregated_p_yes ?? 0);
      case "edge": return Math.abs(a.edge ?? 0) - Math.abs(b.edge ?? 0);
      case "position": return (a.position?.quantity ?? 0) - (b.position?.quantity ?? 0);
      case "avg_price": return (a.position?.avg_price ?? 0) - (b.position?.avg_price ?? 0);
      case "unrealized": {
        const pa = liveNetPnl(a);
        const pb = liveNetPnl(b);
        if (pa == null && pb == null) return 0;
        if (pa == null) return null;
        if (pb == null) return null;
        return pa - pb;
      }
      case "total_pnl": {
        const pa = totalPnl(a);
        const pb = totalPnl(b);
        if (pa == null && pb == null) return 0;
        if (pa == null) return null;
        if (pb == null) return null;
        return pa - pb;
      }
      case "capital": return totalInvestment(a) - totalInvestment(b);
      case "last_trade":
        return (a.last_trade_time ? new Date(a.last_trade_time).getTime() : 0) -
               (b.last_trade_time ? new Date(b.last_trade_time).getTime() : 0);
      case "expiration": {
        if (!a.expiration && !b.expiration) return 0;
        if (!a.expiration) return null;
        if (!b.expiration) return null;
        return new Date(a.expiration).getTime() - new Date(b.expiration).getTime();
      }
      case "return": {
        const ra = a.position ? (liveNetPnl(a) ?? 0) / (totalInvestment(a) || 1) : 0;
        const rb = b.position ? (liveNetPnl(b) ?? 0) / (totalInvestment(b) || 1) : 0;
        return ra - rb;
      }
    }
  }, []);

  // Sort and prioritize markets with actual trades or pending orders
  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      // FIRST PRIORITY: Markets with actual trades OR pending orders come before inactive markets
      const aIsActive = rowHasActivity(a);
      const bIsActive = rowHasActivity(b);
      if (aIsActive && !bIsActive) return -1;
      if (!aIsActive && bIsActive) return 1;

      // SECOND PRIORITY: Apply normal sorting within each group
      for (const { key, asc } of sortKeys) {
        const diff = computeMarketDiff(key, a, b);
        // null means "sort to bottom regardless of direction"
        if (diff === null) {
          const pa = key === "unrealized" ? liveNetPnl(a) : key === "total_pnl" ? totalPnl(a) : key === "expiration" ? a.expiration : null;
          const pb = key === "unrealized" ? liveNetPnl(b) : key === "total_pnl" ? totalPnl(b) : key === "expiration" ? b.expiration : null;
          if (pa == null && pb != null) return 1;
          if (pa != null && pb == null) return -1;
          continue;
        }
        if (diff !== 0) return asc ? diff : -diff;
      }
      return 0;
    });
  }, [rows, sortKeys, computeMarketDiff]);

  // Scroll-to-market from alerts / heatmap clicks
  useEffect(() => {
    if (!scrollToMarketId) return;
    setViewMode("markets");
    setSearch("");
    setFilterMode("all");
    setCategoryFilter(new Set());
    const targetIndex = sorted.findIndex((row) => row.market_id === scrollToMarketId);
    if (targetIndex >= 0) {
      setVisibleCount(Math.max(PAGE_SIZE, targetIndex + 1));
    }
    setExpandedMarketId(scrollToMarketId);
    // Wait for the row to become visible before scrolling.
    requestAnimationFrame(() => {
      requestAnimationFrame(() => {
        const el = rowRefs.current.get(scrollToMarketId);
        if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
        onScrollComplete?.();
      });
    });
  }, [scrollToMarketId, onScrollComplete, sorted]);

  // Filter
  const filtered = useMemo(() => {
    let result = sorted;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter(
        (r) =>
          r.title.toLowerCase().includes(q) ||
          r.ticker.toLowerCase().includes(q) ||
          r.event_ticker.toLowerCase().includes(q)
      );
    }
    switch (filterMode) {
      case "has_position":
        result = result.filter((r) => r.has_position);
        break;
    }
    if (categoryFilter.size > 0) {
      result = result.filter((r) => r.category && categoryFilter.has(r.category.toUpperCase()));
    }
    return result;
  }, [sorted, search, filterMode, categoryFilter]);

  const visible = filtered.slice(0, visibleCount);
  const hasMore = visibleCount < filtered.length;

  const eventGroups = useMemo(() => {
    const groups = new Map<string, EventGroupRow>();

    for (const row of rows) {
      const eventKey = row.event_ticker || `market:${row.market_id}`;
      const eventTitle = row.event_ticker
        ? row.title.split(":")[0]?.trim() || row.title
        : row.title;
      const pnl = liveNetPnl(row) ?? 0;
      const capital = totalInvestment(row);

      const existing = groups.get(eventKey);
      if (existing) {
        existing.rows.push(row);
        existing.market_count += 1;
        existing.position_count += row.has_position ? 1 : 0;
        existing.total_pnl += pnl;
        existing.total_capital += capital;
        existing.total_trade_count += row.trade_count;
        if (row.last_trade_time && (!existing.last_trade_time || new Date(row.last_trade_time) > new Date(existing.last_trade_time))) {
          existing.last_trade_time = row.last_trade_time;
        }
      } else {
        groups.set(eventKey, {
          event_key: eventKey,
          event_ticker: row.event_ticker,
          title: eventTitle,
          category: row.category,
          rows: [row],
          market_count: 1,
          position_count: row.has_position ? 1 : 0,
          total_pnl: pnl,
          total_capital: capital,
          total_trade_count: row.trade_count,
          last_trade_time: row.last_trade_time,
        });
      }
    }

    return Array.from(groups.values()).map((group) => ({
        ...group,
        rows: [...group.rows].sort((a, b) => (liveNetPnl(b) ?? Number.NEGATIVE_INFINITY) - (liveNetPnl(a) ?? Number.NEGATIVE_INFINITY)),
      }));
  }, [rows]);

  const sortedEventGroups = useMemo(() => {
    return [...eventGroups].sort((a, b) => {
      for (const { key, asc } of eventSortKeys) {
        let diff = 0;
        switch (key) {
          case "title": diff = a.title.localeCompare(b.title); break;
          case "market_count": diff = a.market_count - b.market_count; break;
          case "position_count": diff = a.position_count - b.position_count; break;
          case "total_trade_count": diff = a.total_trade_count - b.total_trade_count; break;
          case "total_capital": diff = a.total_capital - b.total_capital; break;
          case "total_pnl": diff = a.total_pnl - b.total_pnl; break;
          case "last_trade_time":
            diff = (a.last_trade_time ? new Date(a.last_trade_time).getTime() : 0)
              - (b.last_trade_time ? new Date(b.last_trade_time).getTime() : 0);
            break;
        }
        if (diff !== 0) return asc ? diff : -diff;
      }
      return 0;
    });
  }, [eventGroups, eventSortKeys]);

  const filteredEventGroups = useMemo(() => {
    let result = sortedEventGroups;
    if (search) {
      const q = search.toLowerCase();
      result = result.filter((group) =>
        group.title.toLowerCase().includes(q) ||
        group.event_ticker.toLowerCase().includes(q) ||
        group.rows.some((row) => row.title.toLowerCase().includes(q) || row.ticker.toLowerCase().includes(q))
      );
    }
    if (filterMode === "has_position") {
      result = result.filter((group) => group.position_count > 0);
    }
    if (categoryFilter.size > 0) {
      result = result.filter((group) => group.category && categoryFilter.has(group.category.toUpperCase()));
    }
    return result;
  }, [sortedEventGroups, search, filterMode, categoryFilter]);

  const visibleEventGroups = filteredEventGroups.slice(0, visibleCount);
  const hasMoreEventGroups = visibleCount < filteredEventGroups.length;

  if (rows.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No market data available
      </div>
    );
  }

  const posCount = rows.filter((r) => r.has_position).length;
  const predCount = rows.filter((r) => r.has_prediction).length;
  const countLabel = viewMode === "events"
    ? `${filteredEventGroups.length} events · ${filtered.length} markets · ${posCount} positions`
    : `${filtered.length} markets · ${posCount} positions · ${predCount} predictions`;

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Toolbar */}
      <div className="px-3 py-2 border-b border-t-border flex flex-wrap items-center gap-2">
        <input
          type="text"
          placeholder="Search markets..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setVisibleCount(PAGE_SIZE); }}
          className="bg-t-bg border border-t-border rounded px-2 py-1 text-xs text-txt-primary placeholder-txt-muted focus:outline-none focus:border-accent w-48 font-mono"
        />
        <div className="flex items-center gap-1">
          {(["markets", "events"] as ViewMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => {
                setViewMode(mode);
                setVisibleCount(PAGE_SIZE);
              }}
              className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
                viewMode === mode
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {mode === "markets" ? "Markets" : "Events"}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1">
          {(["all", "has_position"] as FilterMode[]).map((mode) => {
            const labels: Record<FilterMode, string> = {
              all: "All",
              has_position: "Positions",
            };
            return (
              <button
                key={mode}
                onClick={() => { setFilterMode(mode); setVisibleCount(PAGE_SIZE); }}
                className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
                  filterMode === mode
                    ? "bg-accent/20 text-accent"
                    : "text-txt-muted hover:text-txt-secondary"
                }`}
              >
                {labels[mode]}
              </button>
            );
          })}
        </div>
        {/* Category filter chips */}
        {allCategories.length > 0 && (
          <div className="flex items-center gap-1 flex-wrap">
            {allCategories.map((cat) => {
              const active = categoryFilter.has(cat);
              return (
                <button
                  key={cat}
                  onClick={() => toggleCategory(cat)}
                  className={`px-1.5 py-px rounded border text-[8px] uppercase tracking-wider transition-opacity ${
                    active ? categoryChipClass(cat) : "bg-t-panel-alt border-t-border/40 text-txt-muted opacity-50 hover:opacity-75"
                  }`}
                  title={cat === "MENTIONS" ? "MENTIONS markets are excluded from betting" : undefined}
                >
                  {cat}{cat === "MENTIONS" ? " ⊘" : ""}
                </button>
              );
            })}
            {categoryFilter.size > 0 && (
              <button
                onClick={() => { setCategoryFilter(new Set()); setVisibleCount(PAGE_SIZE); }}
                className="text-[8px] text-txt-muted hover:text-txt-secondary px-1"
              >
                ✕ clear
              </button>
            )}
          </div>
        )}
        <span className="text-[9px] text-txt-muted ml-auto font-mono">
          {countLabel}
        </span>
        <span className="text-[9px] text-txt-muted font-mono border border-t-border/60 rounded px-1.5 py-0.5 whitespace-nowrap">
          <kbd className="font-sans">⇧</kbd> click to multi-sort
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        {viewMode === "markets" ? (
          <table className="w-full table-fixed text-xs">
            <colgroup>
              <col />
              <col className="w-[140px]" />
              <col className="w-[130px]" />
              <col className="w-[110px]" />
              <col className="w-[110px]" />
              <col className="w-[140px]" />
              <col className="w-[110px]" />
              <col className="w-[110px]" />
              <col className="w-[130px]" />
              <col className="w-[160px]" />
              <col className="w-[160px]" />
            </colgroup>
            <thead>
              <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
                <Th k="title" sortKeys={sortKeys} onClick={handleSort} align="left" info="Prediction market name and ticker">Market</Th>
                <th className="px-3 py-2 text-left font-medium">Category</th>
                <Th k="yes_ask" sortKeys={sortKeys} onClick={handleSort} align="right" info="Current live Yes / No ask prices on Kalshi">Live Mkt Price</Th>
                <Th k="predicted" sortKeys={sortKeys} onClick={handleSort} align="right" info="Model probability (p_yes from the prediction model)">Model P</Th>
                <Th k="edge" sortKeys={sortKeys} onClick={handleSort} align="right" info="Model edge at the last prediction step. Computed from the model probability and market ask at prediction time, not from the current live market price.">Edge</Th>
                <Th k="position" sortKeys={sortKeys} onClick={handleSort} align="center" info="Current open position: side (YES/NO) and number of contracts">Position</Th>
                <Th k="avg_price" sortKeys={sortKeys} onClick={handleSort} align="right" info="Weighted average price paid per contract">Avg Entry</Th>
                <Th k="unrealized" sortKeys={sortKeys} onClick={handleSort} align="right" info="Unrealized P&L on current open position only. Open value = quantity × current bid minus cost basis.">P&L</Th>
                <Th k="total_pnl" sortKeys={sortKeys} onClick={handleSort} align="right" info="Total P&L across all trades: realized (from completed sells) + unrealized (from current open position).">Total P&L</Th>
                <Th k="capital" sortKeys={sortKeys} onClick={handleSort} align="right" info="Kalshi-backed cost basis plus fees paid for the open position.">Investment</Th>
                <Th k="last_trade" sortKeys={sortKeys} onClick={handleSort} align="right" info="Timestamp of the most recent trade in this market">Last Trade</Th>
                <Th k="expiration" sortKeys={sortKeys} onClick={handleSort} align="right" info="Market close/expiration date">Closes</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-t-border/40">
              {visible.map((row, index) => {
                const isExpanded = expandedMarketId === row.market_id;
                const hasMultipleModels = row.model_predictions.length > 1;

                // Check if this is the first inactive market after active markets
                const prevIsActive = index > 0 && rowHasActivity(visible[index - 1]);
                const currIsInactive = !rowHasActivity(row);
                const isFirstInactive = index > 0 && prevIsActive && currIsInactive;

                return (
                  <Fragment key={row.market_id}>
                    {/* Add divider row between active and inactive markets */}
                    {isFirstInactive && (
                      <tr key={`divider-${row.market_id}`} className="bg-t-bg-secondary/30">
                        <td colSpan={12} className="px-3 py-2 text-center">
                          <div className="flex items-center justify-center gap-2">
                            <div className="h-px bg-t-border flex-1" />
                            <span className="text-[10px] text-txt-muted font-medium uppercase tracking-wider">
                              Markets Without Activity
                            </span>
                            <div className="h-px bg-t-border flex-1" />
                          </div>
                        </td>
                      </tr>
                    )}
                    <MarketRow
                      row={row}
                      isExpanded={isExpanded}
                      hasMultipleModels={hasMultipleModels}
                      apiClient={apiClient}
                      instanceCacheKey={instanceCacheKey}
                      onToggle={() => setExpandedMarketId(isExpanded ? null : row.market_id)}
                      rowRef={(el) => {
                        if (el) rowRefs.current.set(row.market_id, el);
                        else rowRefs.current.delete(row.market_id);
                      }}
                    />
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
                <EventTh k="title" sortKeys={eventSortKeys} onClick={handleEventSort} align="left">Event</EventTh>
                <EventTh k="market_count" sortKeys={eventSortKeys} onClick={handleEventSort} align="right">Markets</EventTh>
                <EventTh k="position_count" sortKeys={eventSortKeys} onClick={handleEventSort} align="right">Positions</EventTh>
                <EventTh k="total_trade_count" sortKeys={eventSortKeys} onClick={handleEventSort} align="right">Trades</EventTh>
                <EventTh k="total_capital" sortKeys={eventSortKeys} onClick={handleEventSort} align="right">Investment</EventTh>
                <EventTh k="total_pnl" sortKeys={eventSortKeys} onClick={handleEventSort} align="right">P&amp;L</EventTh>
                <EventTh k="last_trade_time" sortKeys={eventSortKeys} onClick={handleEventSort} align="right">Last Trade</EventTh>
              </tr>
            </thead>
            <tbody className="divide-y divide-t-border/40">
              {visibleEventGroups.map((group) => (
                <EventGroupRowView
                  key={group.event_key}
                  group={group}
                  isExpanded={expandedEventKey === group.event_key}
                  onToggle={() => setExpandedEventKey(expandedEventKey === group.event_key ? null : group.event_key)}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {((viewMode === "markets" && (hasMore || filtered.length > PAGE_SIZE))
        || (viewMode === "events" && (hasMoreEventGroups || filteredEventGroups.length > PAGE_SIZE))) && (
        <div className="px-3 py-2 border-t border-t-border flex items-center justify-between text-[10px] text-txt-muted">
          <span>
            {viewMode === "markets"
              ? `Showing ${visible.length} of ${filtered.length}`
              : `Showing ${visibleEventGroups.length} of ${filteredEventGroups.length}`}
          </span>
          {(viewMode === "markets" ? hasMore : hasMoreEventGroups) && (
            <button
              onClick={() => setVisibleCount((c) => c + PAGE_SIZE)}
              className="text-accent hover:text-accent/80 font-medium"
            >
              Show more
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function EventGroupRowView({
  group,
  isExpanded,
  onToggle,
}: {
  group: EventGroupRow;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr className="hover:bg-t-panel-hover transition-colors cursor-pointer" onClick={onToggle}>
        <td className="px-3 py-2">
          <div className="flex flex-col gap-0.5">
            {group.event_ticker ? (
              <a
                href={kalshiEventUrl(group.event_ticker)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-txt-primary hover:text-accent transition-colors font-medium truncate text-xs"
                onClick={(e) => e.stopPropagation()}
              >
                {group.title}
              </a>
            ) : (
              <span className="text-txt-primary font-medium truncate text-xs">
                {group.title}
              </span>
            )}
            <div className="flex items-center gap-1.5 text-[9px] font-mono text-txt-muted">
              {group.event_ticker && <span>{group.event_ticker}</span>}
              <span className="text-[8px] text-txt-muted">
                {isExpanded ? "\u25B2" : "\u25BC"}
              </span>
            </div>
          </div>
        </td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">{group.market_count}</td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">{group.position_count}</td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">{group.total_trade_count}</td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">{fmtDollar(group.total_capital)}</td>
        <td className={`px-3 py-2 text-right font-mono font-medium ${pnlCls(group.total_pnl)}`}>
          {fmtDollar(group.total_pnl)}
        </td>
        <td className="px-3 py-2 text-right font-mono text-txt-muted text-[10px]">
          {group.last_trade_time ? fmtTime(group.last_trade_time) : "--"}
        </td>
      </tr>
      {isExpanded && (
        <tr>
          <td colSpan={7} className="bg-t-bg/50 px-3 py-3">
            <div className="overflow-x-auto">
              <table className="w-full text-[10px]">
                <thead>
                  <tr className="border-b border-t-border/40 text-txt-muted text-[9px] uppercase tracking-widest">
                    <th className="px-2 py-1.5 text-left font-medium">Market</th>
                    <th className="px-2 py-1.5 text-right font-medium">Ticker</th>
                    <th className="px-2 py-1.5 text-right font-medium">Investment</th>
                    <th className="px-2 py-1.5 text-right font-medium">P&amp;L</th>
                    <th className="px-2 py-1.5 text-right font-medium">Last Trade</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-t-border/20">
                  {group.rows.map((row) => {
                    const net = liveNetPnl(row);
                    return (
                      <tr key={row.market_id} className="hover:bg-t-panel-hover/50">
                        <td className="px-2 py-1.5">
                          <a
                            href={kalshiMarketUrl(row.event_ticker || row.ticker)}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-txt-primary hover:text-accent transition-colors"
                          >
                            {row.title}
                          </a>
                        </td>
                        <td className="px-2 py-1.5 text-right font-mono text-txt-muted">{row.ticker}</td>
                        <td className="px-2 py-1.5 text-right font-mono text-txt-primary">
                          <div>{fmtDollar(totalInvestment(row))}</div>
                          <div className="text-[9px] text-warn">
                            {formatInvestmentSplit(row.position?.capital ?? 0, tradeFeeTotal(row))}
                          </div>
                        </td>
                        <td className={`px-2 py-1.5 text-right font-mono font-medium ${net != null ? pnlCls(net) : "text-txt-muted"}`}>
                          {net != null ? fmtDollar(net) : "--"}
                        </td>
                        <td className="px-2 py-1.5 text-right font-mono text-txt-muted">
                          {row.last_trade_time ? fmtTime(row.last_trade_time) : "--"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function EventTh({
  k,
  sortKeys,
  onClick,
  align = "left",
  children,
}: {
  k: EventSortKey;
  sortKeys: Array<{ key: EventSortKey; asc: boolean }>;
  onClick: (k: EventSortKey, multi: boolean) => void;
  align?: "left" | "right";
  children: React.ReactNode;
}) {
  const idx = sortKeys.findIndex((s) => s.key === k);
  const active = idx >= 0;
  const asc = active ? sortKeys[idx].asc : false;
  const multi = sortKeys.length > 1;
  return (
    <th
      className={`px-3 py-2 font-medium cursor-pointer select-none hover:text-txt-secondary transition-colors ${align === "right" ? "text-right" : "text-left"}`}
      onClick={(e) => onClick(k, e.shiftKey)}
    >
      <span className={`inline-flex items-center gap-1 ${align === "right" ? "justify-end w-full" : ""}`}>
        {children}
        {active && (
          <>
            {multi && <span className="text-[7px] text-txt-muted">{idx + 1}</span>}
            <span className="text-[8px]">{asc ? "\u25B2" : "\u25BC"}</span>
          </>
        )}
      </span>
    </th>
  );
}

// ── Market Row ──────────────────────────────────────────────

function MarketRow({
  row,
  isExpanded,
  hasMultipleModels,
  apiClient,
  instanceCacheKey,
  onToggle,
  rowRef,
}: {
  row: UnifiedMarketRow;
  isExpanded: boolean;
  hasMultipleModels: boolean;
  apiClient: ApiClient;
  instanceCacheKey: string;
  onToggle: () => void;
  rowRef: (el: HTMLTableRowElement | null) => void;
}) {
  const pos = row.position;
  const isYes = pos?.contract.toLowerCase() === "yes";
  const lifecycleLabel = marketStatusLabel(row.market_status, row.market_result);
  const latestModelDecision = [...row.model_predictions]
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())[0]?.decision?.toUpperCase() ?? null;
  const showHeldEdgeStyle = isHoldLikeDecision(latestModelDecision);

  return (
    <>
      <tr
        ref={rowRef}
        className="hover:bg-t-panel-hover transition-colors cursor-pointer"
        onClick={onToggle}
      >
        {/* Market */}
        <td className="px-3 py-2 max-w-[260px] overflow-hidden">
          <div className="flex flex-col gap-0.5">
            {row.event_ticker ? (
              <a
                href={kalshiMarketUrl(row.event_ticker)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-txt-primary hover:text-accent transition-colors font-medium truncate text-xs"
                onClick={(e) => e.stopPropagation()}
              >
                {row.title}
              </a>
            ) : (
              <span className="text-txt-primary font-medium truncate text-xs">
                {row.title}
              </span>
            )}
            <div className="flex items-center gap-1.5 text-[9px] font-mono text-txt-muted overflow-hidden">
              <span className="shrink-0">{row.ticker}</span>
              {row.event_ticker && (
                <>
                  <span className="text-t-border-light shrink-0">/</span>
                  <a
                    href={kalshiEventUrl(row.event_ticker)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:text-accent transition-colors truncate"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {row.event_ticker}
                  </a>
                </>
              )}
            </div>
          </div>
        </td>

        {/* Category */}
        <td className="px-3 py-2">
          {row.category ? (
            <div className="flex flex-col gap-0.5 items-start">
              <span className={`px-1 py-px rounded border text-[8px] uppercase tracking-wider ${categoryChipClass(row.category)}`}>
                {row.category}
              </span>
              {lifecycleLabel && (
                <span className={`px-1 py-px rounded border text-[8px] uppercase tracking-wider ${marketStatusChipClass(row.market_status, row.market_result)}`}>
                  {lifecycleLabel}
                </span>
              )}
              {row.category.toUpperCase() === "MENTIONS" && (
                <span className="text-[8px] text-red-400 italic">no betting</span>
              )}
            </div>
          ) : (
            lifecycleLabel ? (
              <span className={`px-1 py-px rounded border text-[8px] uppercase tracking-wider ${marketStatusChipClass(row.market_status, row.market_result)}`}>
                {lifecycleLabel}
              </span>
            ) : (
              <span className="text-txt-muted text-[9px]">—</span>
            )
          )}
        </td>

        {/* Mkt Price */}
        <td className="px-3 py-2 text-right font-mono">
          <span className={pos && isYes ? "text-profit font-semibold" : "text-txt-muted"}>
            {row.yes_ask != null ? `${(row.yes_ask * 100).toFixed(0)}c` : "--"}
          </span>
          <span className="text-txt-muted mx-0.5">/</span>
          <span className={pos && !isYes ? "text-loss font-semibold" : "text-txt-muted"}>
            {row.no_ask != null ? `${(row.no_ask * 100).toFixed(0)}c` : "--"}
          </span>
        </td>

        {/* Model P */}
        <td className="px-3 py-2 text-right font-mono text-accent">
          <span className="flex items-center justify-end gap-1">
            {row.aggregated_p_yes != null
              ? `${(row.aggregated_p_yes * 100).toFixed(1)}%`
              : "--"}
            {hasMultipleModels && (
              <span className="text-[8px] text-txt-muted">
                {isExpanded ? "\u25B2" : "\u25BC"}
              </span>
            )}
          </span>
        </td>

        {/* Edge */}
        <td
          className={`px-3 py-2 text-right font-mono font-medium ${
            row.edge != null
              ? (showHeldEdgeStyle ? holdEdgeToneClass() : pnlCls(row.edge))
              : "text-txt-muted"
          }`}
        >
          {row.edge != null
            ? `${row.edge >= 0 ? "+" : ""}${(row.edge * 100).toFixed(1)}pp`
            : "--"}
        </td>

        {/* Position */}
        <td className="px-3 py-2 text-center">
          {pos ? (
            <span className="flex flex-col items-center gap-0.5">
              <span className="flex items-center gap-1.5">
                <span className="font-mono text-txt-primary" title={`Filled position: ${pos.quantity} contracts`}>
                  {pos.quantity}
                </span>
                <span
                  className={`inline-block px-1.5 py-px rounded text-[9px] font-bold tracking-wider ${
                    pos.contract.toLowerCase() === "yes"
                      ? "bg-profit-dim text-profit"
                      : "bg-loss-dim text-loss"
                  }`}
                >
                  {pos.contract.toUpperCase()}
                </span>
              </span>
              {pendingHoldDeltaForDisplay(row) != null && (
                <span className="text-[9px] font-mono text-yellow-400" title="Open Kalshi orders captured during the latest sync">
                  {formatPendingDelta(pendingHoldDeltaForDisplay(row)!)}
                </span>
              )}
              {row.target_shares != null && row.target_shares !== pos.quantity && (
                <span className={`text-[8px] font-mono ${row.target_shares < pos.quantity ? "text-loss" : "text-profit"}`}
                  title={`Projected position after current Kalshi pending orders: ${row.target_shares} from ${pos.quantity}`}>
                  → {row.target_shares} ({row.target_shares > pos.quantity ? "+" : "-"}{Math.abs(row.target_shares - pos.quantity)})
                </span>
              )}
            </span>
          ) : row.edge && Math.abs(row.edge) > 0.01 ? (
            <span className="flex flex-col items-center gap-0.5">
              <span className="font-mono text-txt-muted">0</span>
              {pendingHoldDeltaForDisplay(row) != null && (
                <span className="text-[9px] font-mono text-yellow-400" title="Open Kalshi orders captured during the latest sync">
                  {formatPendingDelta(pendingHoldDeltaForDisplay(row)!)}
                </span>
              )}
              {row.target_shares != null && (
                <span className="text-[8px] font-mono text-txt-muted"
                  title={`Projected position after current Kalshi pending orders: ${row.target_shares}`}>
                  projected: {row.target_shares}
                </span>
              )}
            </span>
          ) : (
            <span className="text-txt-muted">--</span>
          )}
        </td>

        {/* Entry */}
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          {pos ? `${(pos.avg_price * 100).toFixed(0)}c` : "--"}
        </td>

        {/* Live Net P&L (unrealized) */}
        {(() => { const net = liveNetPnl(row); return (
          <td className={`px-3 py-2 text-right font-mono font-medium ${net != null ? pnlCls(net) : "text-txt-muted"}`}>
            {net != null ? fmtDollar(net) : "--"}
          </td>
        ); })()}

        {/* Total P&L (realized + unrealized) */}
        {(() => { const tp = totalPnl(row); return (
          <td className={`px-3 py-2 text-right font-mono font-medium ${tp != null ? pnlCls(tp) : "text-txt-muted"}`}>
            {tp != null ? fmtDollar(tp) : "--"}
          </td>
        ); })()}

        {/* Capital */}
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          <div>{pos ? fmtDollar(totalInvestment(row)) : "--"}</div>
          {pos && (
            <div className="text-[9px] text-warn">
              {formatInvestmentSplit(pos.capital, tradeFeeTotal(row))}
            </div>
          )}
        </td>

        {/* Last Trade */}
        <td className="px-3 py-2 text-right font-mono text-txt-muted text-[10px]">
          {row.last_trade_time ? fmtTime(row.last_trade_time) : "--"}
        </td>

        {/* Closes */}
        <td className="px-3 py-2 text-right font-mono text-txt-muted text-[10px]">
          {row.expiration ? fmtTime(row.expiration) : "--"}
        </td>
      </tr>

      {/* Expanded detail panel */}
      {isExpanded && (
        <tr>
          <td colSpan={12} className="p-0">
            <ExpandedPanel
              row={row}
              apiClient={apiClient}
              instanceCacheKey={instanceCacheKey}
            />
          </td>
        </tr>
      )}
    </>
  );
}

// ── Expanded Panel with Tabs ────────────────────────────────

function ExpandedPanel({
  row,
  apiClient,
  instanceCacheKey,
}: {
  row: UnifiedMarketRow;
  apiClient: ApiClient;
  instanceCacheKey: string;
}) {
  const [activeTab, setActiveTab] = useState<"timeline" | "trades" | "models">("trades");
  const [modelRuns, setModelRuns] = useState<ModelRun[] | null>(null);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const modelRunsCacheRef = useRef<Map<string, ModelRun[]>>(new Map());

  useEffect(() => {
    const cacheKey = `runs:${instanceCacheKey}:${row.market_id}`;
    const cachedRuns = modelRunsCacheRef.current.get(cacheKey);
    if (cachedRuns) {
      setModelRuns(cachedRuns);
      return;
    }

    let cancelled = false;
    setLoadingRuns(true);
    apiClient.getMarketModelRuns(row.market_id).then((data) => {
      if (cancelled) return;
      modelRunsCacheRef.current.set(cacheKey, data);
      setModelRuns(data);
      setLoadingRuns(false);
    }).catch(() => {
      if (cancelled) return;
      setModelRuns([]);
      setLoadingRuns(false);
    });
    return () => { cancelled = true; };
  }, [apiClient, instanceCacheKey, row.market_id]);

  const timelineCount = useMemo(() => {
    if (!modelRuns) return row.trade_count;
    const chronTrades = [...row.trades]
      .filter((trade) => !isSyntheticTrade(trade))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    const chronRuns = [...modelRuns].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    const tradesWithRuns = matchTradesToRuns(chronTrades, chronRuns, row);
    return chronTrades.length + unmatchedTimelineRuns(tradesWithRuns, chronRuns, row.pending_orders ?? []).length;
  }, [row.trade_count, row.trades, row.pending_orders, modelRuns]);
  const tradesTabCount = useMemo(
    () => row.trades.filter((trade) => !isSyntheticTrade(trade) && shouldShowInTradesTab(trade)).length,
    [row.trades]
  );

  const tabs: { key: typeof activeTab; label: string; count?: number }[] = [
    { key: "trades", label: "Trades", count: tradesTabCount },
    { key: "timeline", label: "Timeline", count: timelineCount },
    { key: "models", label: "Models", count: row.model_predictions.length },
  ];

  return (
    <div className="bg-t-bg/50 border-t border-t-border/40">
      {/* Tab bar */}
      <div className="px-4 py-1.5 border-b border-t-border/40 flex items-center gap-1">
        {tabs.map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={`px-2.5 py-1 rounded text-[10px] font-medium transition-colors ${
              activeTab === tab.key
                ? "bg-accent/20 text-accent"
                : "text-txt-muted hover:text-txt-secondary"
            }`}
          >
            {tab.label}
            {tab.count != null && tab.count > 0 && (
              <span className="ml-1 text-[8px] opacity-60">({tab.count})</span>
            )}
          </button>
        ))}

        {/* Quick stats */}
        <div className="ml-auto flex items-center gap-3 text-[9px] font-mono text-txt-muted">
          {row.position && (
            <>
              <span title="Your open position: quantity and average entry price">
                {row.position.contract.toUpperCase()} {row.position.quantity} avg {(row.position.avg_price * 100).toFixed(0)}c
              </span>
            </>
          )}
          {row.volume_24h != null && (
            <span>Vol: {row.volume_24h.toLocaleString()}</span>
          )}
        </div>
      </div>

      {/* Tab content */}
      <div className="px-4 py-3">
        {activeTab === "timeline" && (
          <SubmittedTradesTimelineTab
            row={row}
            modelRuns={modelRuns}
            loadingRuns={loadingRuns}
          />
        )}
        {activeTab === "trades" && <TradesTab row={row} modelRuns={modelRuns} />}
        {activeTab === "models" && (
          <ModelsTab
            row={row}
            instanceCacheKey={instanceCacheKey}
            apiClient={apiClient}
            modelRuns={modelRuns}
            loadingRuns={loadingRuns}
          />
        )}
      </div>
    </div>
  );
}

// ── Tab 1: Activity Timeline ────────────────────────────────

type SubmittedTradeTimelineItem = {
  trade: Trade;
  qty: number;
  isSell: boolean;
  fee: number;
  price: number;
  cashFlow: number | null;
};

type TimelinePositionState = {
  quantity: number;
  side: string;
} | null;

type RunDisplayContext = {
  pYes: number | null;
  edge: number | null;
};

type TradeDisplayContext = {
  pYes: number | null;
  mktAsk: number | null;
  edge: number | null;
};

type TradeStepGroup = {
  key: string;
  sortTs: number;
  lines: TimelineTradeItem[];
};

function getExecutedTradeQuantity(trade: Trade): number {
  const status = trade.status?.toUpperCase() ?? "";
  if ((trade.filled_shares ?? 0) > 0) return trade.filled_shares;
  if (status === "FILLED" || status === "DRY_RUN") return trade.count;
  return 0;
}

function submittedTradeTimelineItem(trade: Trade): SubmittedTradeTimelineItem {
  const qty = getExecutedTradeQuantity(trade);
  const price = trade.price_cents / 100;
  const fee = trade.fee_paid || 0;
  const isSell = trade.action?.toUpperCase() === "SELL";
  const cashFlow = qty > 0
    ? (isSell ? (qty * price) - fee : -((qty * price) + fee))
    : null;
  return { trade, qty, isSell, fee, price, cashFlow };
}

function buildRunDisplayContext(chronRuns: ModelRun[], row: UnifiedMarketRow): Map<number, RunDisplayContext> {
  const lastPYesByModel = new Map<string, number>();
  const context = new Map<number, RunDisplayContext>();

  chronRuns.forEach((run) => {
    const fallbackPYes = lastPYesByModel.get(run.model_name) ?? row.aggregated_p_yes ?? null;
    const pYes = run.p_yes ?? fallbackPYes;
    if (run.p_yes != null) lastPYesByModel.set(run.model_name, run.p_yes);
    const edge = pYes != null && row.yes_ask != null ? pYes - row.yes_ask : row.edge;
    context.set(run.id, { pYes, edge });
  });

  return context;
}

function resolveTradeDisplayContext(
  trade: Trade,
  matchedRun: ModelRun | null,
  row: UnifiedMarketRow,
  runDisplayContext: Map<number, RunDisplayContext>,
): TradeDisplayContext {
  const matchedContext = matchedRun ? runDisplayContext.get(matchedRun.id) : null;
  const pYes = trade.prediction?.p_yes ?? matchedContext?.pYes ?? null;
  const mktAsk = trade.prediction?.yes_ask ?? row.yes_ask ?? null;
  const edge = pYes != null && mktAsk != null ? pYes - mktAsk : matchedContext?.edge ?? row.edge;
  return { pYes, mktAsk, edge };
}

function resolveTradeStepLineContext(
  item: TimelineTradeItem,
  row: UnifiedMarketRow,
  runDisplayContext: Map<number, RunDisplayContext>,
  peerItems: TimelineTradeItem[] = [],
): TradeDisplayContext {
  if (item.trade.prediction || item.matchedRun) {
    return resolveTradeDisplayContext(item.trade, item.matchedRun, row, runDisplayContext);
  }

  const peer = peerItems.find((candidate) => candidate.trade.id !== item.trade.id && (candidate.trade.prediction || candidate.matchedRun));
  if (peer) {
    return resolveTradeDisplayContext(peer.trade, peer.matchedRun, row, runDisplayContext);
  }

  return resolveTradeDisplayContext(item.trade, item.matchedRun, row, runDisplayContext);
}

function isLikelySameRebalanceStep(current: TimelineTradeItem, next: TimelineTradeItem): boolean {
  if (current.matchedRun && next.matchedRun) {
    return current.matchedRun.id === next.matchedRun.id;
  }

  const currentPred = current.trade.prediction;
  const nextPred = next.trade.prediction;
  if (!currentPred || !nextPred) {
    return true;
  }

  const sameSource = currentPred.source === nextPred.source;
  const samePYes = Math.abs(currentPred.p_yes - nextPred.p_yes) <= 0.0005;
  const sameYesAsk = Math.abs(currentPred.yes_ask - nextPred.yes_ask) <= 0.0005;
  const sameNoAsk = Math.abs(currentPred.no_ask - nextPred.no_ask) <= 0.0005;
  return sameSource && samePYes && sameYesAsk && sameNoAsk;
}

function isCrossSideRebalancePair(current: TimelineTradeItem, next?: TimelineTradeItem): next is TimelineTradeItem {
  if (!next) return false;
  const currentTs = new Date(current.trade.created_at).getTime();
  const nextTs = new Date(next.trade.created_at).getTime();
  return (
    current.trade.action?.toUpperCase() === "SELL"
    && next.trade.action?.toUpperCase() === "BUY"
    && current.trade.side?.toUpperCase() !== next.trade.side?.toUpperCase()
    && Math.abs(nextTs - currentTs) <= SAME_ACTION_WINDOW_MS
    && isLikelySameRebalanceStep(current, next)
  );
}

function buildTradeStepGroups(tradesWithRuns: TimelineTradeItem[]): TradeStepGroup[] {
  const groups: TradeStepGroup[] = [];

  for (let i = 0; i < tradesWithRuns.length; i++) {
    const current = tradesWithRuns[i];
    const next = tradesWithRuns[i + 1];

    if (isCrossSideRebalancePair(current, next)) {
      groups.push({
        key: `step-${current.trade.id}-${next.trade.id}`,
        sortTs: new Date(next.trade.created_at).getTime(),
        lines: [current, next],
      });
      i += 1;
      continue;
    }

    groups.push({
      key: `step-${current.trade.id}`,
      sortTs: new Date(current.trade.created_at).getTime(),
      lines: [current],
    });
  }

  return groups.sort((a, b) => b.sortTs - a.sortTs);
}

function SubmittedTradesTimelineTab({
  row,
  modelRuns,
  loadingRuns,
}: {
  row: UnifiedMarketRow;
  modelRuns: ModelRun[] | null;
  loadingRuns: boolean;
}) {
  const [expandedTradeId, setExpandedTradeId] = useState<number | null>(null);
  const [showPnLChart, setShowPnLChart] = useState(false);

  const chronTrades = useMemo(
    () => [...row.trades]
      .filter((trade) => !isSyntheticTrade(trade))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [row.trades]
  );
  const chronRuns = useMemo(
    () => [...(modelRuns ?? [])].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()),
    [modelRuns]
  );
  const tradesWithRuns = useMemo(() => matchTradesToRuns(chronTrades, chronRuns, row), [chronTrades, chronRuns, row]);
  const unmatchedRuns = useMemo(
    () => unmatchedTimelineRuns(tradesWithRuns, chronRuns, row.pending_orders ?? []),
    [tradesWithRuns, chronRuns, row.pending_orders]
  );
  const replayedPosition = useMemo(() => replayedTimelinePosition(chronTrades), [chronTrades]);
  const syncedPosition = useMemo(() => syncedTimelinePosition(row), [row]);
  const hasHistoryMismatch = useMemo(
    () => !sameTimelinePosition(replayedPosition, syncedPosition),
    [replayedPosition, syncedPosition]
  );
  const runDisplayContext = useMemo(() => buildRunDisplayContext(chronRuns, row), [chronRuns, row]);
  const tradeDisplayContext = useCallback(
    (trade: Trade, matchedRun: ModelRun | null) => resolveTradeDisplayContext(trade, matchedRun, row, runDisplayContext),
    [row, runDisplayContext]
  );

  const cyclePnLData = useMemo(() => {
    const cycleMap = new Map<number, { timestamp: number; pnl: number; tradeCount: number }>();

    chronTrades.forEach((trade) => {
      const entry = submittedTradeTimelineItem(trade);
      const tradeTs = new Date(trade.created_at).getTime();
      const cycleTs = Math.floor(tradeTs / (60 * 60 * 1000)) * (60 * 60 * 1000);
      const cycle = cycleMap.get(cycleTs) ?? { timestamp: cycleTs, pnl: 0, tradeCount: 0 };
      cycle.pnl += entry.cashFlow ?? 0;
      cycle.tradeCount += 1;
      cycleMap.set(cycleTs, cycle);
    });

    let cumulativePnL = 0;
    return Array.from(cycleMap.values())
      .sort((a, b) => a.timestamp - b.timestamp)
      .map((cycle) => {
        cumulativePnL += cycle.pnl;
        return {
          time: new Date(cycle.timestamp).toLocaleTimeString("en-US", {
            hour: "numeric",
            minute: "2-digit",
            month: "short",
            day: "numeric",
          }),
          timestamp: cycle.timestamp,
          cyclePnL: cycle.pnl,
          cumulativePnL,
          tradeCount: cycle.tradeCount,
        };
      });
  }, [chronTrades]);

  type SubmittedTimelineEvent =
    | { type: "trade"; key: string; sortTs: number; item: TimelineTradeItem }
    | { type: "switch"; key: string; sortTs: number; sell: TimelineTradeItem; buy: TimelineTradeItem }
    | { type: "run"; key: string; sortTs: number; run: ModelRun };

  const mergedEvents = useMemo<SubmittedTimelineEvent[]>(() => {
    const events: SubmittedTimelineEvent[] = [];
    const consumed = new Set<number>();

    for (let i = 0; i < tradesWithRuns.length; i++) {
      if (consumed.has(i)) continue;
      const current = tradesWithRuns[i];
      const next = tradesWithRuns[i + 1];
      const currentTs = new Date(current.trade.created_at).getTime();
      const nextTs = next ? new Date(next.trade.created_at).getTime() : Number.NaN;
      const isFlipPair = !consumed.has(i + 1) && isCrossSideRebalancePair(current, next);

      if (isFlipPair) {
        events.push({
          type: "switch",
          key: `switch-${current.trade.id}-${next!.trade.id}`,
          sortTs: nextTs,
          sell: current,
          buy: next!,
        });
        consumed.add(i + 1);
        continue;
      }

      events.push({
        type: "trade",
        key: `trade-${current.trade.id}`,
        sortTs: currentTs,
        item: current,
      });
    }

    unmatchedRuns.forEach((run) => {
      events.push({
        type: "run",
        key: `run-${run.id}`,
        sortTs: new Date(run.timestamp).getTime(),
        run,
      });
    });

    return events.sort((a, b) => b.sortTs - a.sortTs);
  }, [tradesWithRuns, unmatchedRuns]);

  if (mergedEvents.length === 0) {
    return <div className="text-[10px] text-txt-muted">No timeline activity for this market</div>;
  }

  return (
    <div>
      {cyclePnLData.length > 0 && (
        <div className="mb-3">
          <button
            onClick={() => setShowPnLChart(!showPnLChart)}
            className="text-[10px] font-medium text-accent hover:text-accent/80 transition-colors"
          >
            {showPnLChart ? "▼" : "▶"} Submitted Trade Cash Flow ({cyclePnLData.length} cycles)
          </button>
        </div>
      )}

      {showPnLChart && cyclePnLData.length > 0 && (
        <div className="mb-4 p-3 bg-t-bg-secondary/30 rounded">
          <div className="text-[9px] text-txt-muted uppercase tracking-widest font-medium mb-2">
            Submitted Trade Cash Flow by Cycle
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={cyclePnLData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" vertical={false} />
              <XAxis dataKey="time" tick={{ fontSize: 8, fill: "#888" }} angle={-45} textAnchor="end" height={60} />
              <YAxis tick={{ fontSize: 8, fill: "#888" }} width={40} tickFormatter={(v) => `$${v.toFixed(0)}`} />
              <Tooltip
                contentStyle={{ backgroundColor: "#1a1a1a", border: "1px solid #333", borderRadius: "4px" }}
                labelStyle={{ fontSize: 10, color: "#ccc" }}
                formatter={(value: any) => {
                  const num = Number(value);
                  return [`${num >= 0 ? "+" : ""}$${num.toFixed(2)}`, ""];
                }}
              />
              <Line type="stepAfter" dataKey="cumulativePnL" stroke="#22c55e" strokeWidth={2} dot={{ r: 3, fill: "#22c55e" }} name="Cumulative Cash Flow" />
              <Line type="monotone" dataKey="cyclePnL" stroke="#888" strokeWidth={1} strokeDasharray="5 5" dot={{ r: 2, fill: "#888" }} name="Cycle Cash Flow" />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {hasHistoryMismatch && (
        <div className="mb-3 rounded border border-warn/40 bg-warn/10 px-3 py-2 text-[10px] leading-relaxed text-warn">
          Timeline history mismatch detected. Current synced hold:{" "}
          <span className={sideToneClass(syncedPosition?.side, true)}>{formatTimelinePosition(syncedPosition)}</span>.{" "}
          Reconstructed history ends at{" "}
          <span className={sideToneClass(replayedPosition?.side, true)}>{formatTimelinePosition(replayedPosition)}</span>.{" "}
          Older timeline rows may be incomplete or mislabeled.
        </div>
      )}

      <div className="relative pl-4 max-h-[400px] overflow-y-auto">
        <div className="absolute left-[5px] top-2 bottom-2 w-px bg-t-border" />
        {loadingRuns && (
          <div className="mb-2 text-[9px] text-txt-muted italic">Loading prediction history...</div>
        )}
        {mergedEvents.map((event) => {
          if (event.type === "run") {
            const { run } = event;
            const isExpanded = expandedTradeId === -run.id;
            const isHold = isHoldLikeDecision(run.decision);
            const isSkip = isSkipLikeDecision(run.decision);
            const label = normalizedDecisionLabel(run.decision);
            const side = decisionSide(run.decision);
            const showDecisionBadge = !side || isHold || isSkip;
            const displayContext = runDisplayContext.get(run.id);
            const pYes = displayContext?.pYes ?? null;
            const edge = displayContext?.edge ?? null;

            // Compute hold rationale when model p_yes falls within spread dead zone
            let holdRationale: string | null = null;
            if (isHold && pYes != null && row.yes_ask != null && row.no_ask != null) {
              const BUFFER = 0.02;
              const lowerBound = Math.max(0, 1.0 - row.no_ask - BUFFER);
              const upperBound = Math.min(1.0, row.yes_ask + BUFFER);
              if (pYes >= lowerBound && pYes <= upperBound) {
                holdRationale =
                  `Model probability ${(pYes * 100).toFixed(0)}% is within the market spread dead zone ` +
                  `[${(lowerBound * 100).toFixed(0)}%, ${(upperBound * 100).toFixed(0)}%] ` +
                  `(yes_ask=${(row.yes_ask * 100).toFixed(0)}c, no_ask=${(row.no_ask * 100).toFixed(0)}c, buffer=2%). ` +
                  `Edge of ${edge != null ? (edge * 100).toFixed(0) : "?"}pp is too small to justify trading.`;
              }
            }

            const hasDetail = !!(holdRationale || run.reasoning || (run.sources && run.sources.length > 0));
            return (
              <div key={event.key} className="relative py-1.5">
                <div className={`absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full border-2 border-t-bg z-10 ${
                  isSkip ? "bg-txt-muted" : isHold ? "bg-orange-400" : "bg-accent/70"
                }`} />
                <button
                  type="button"
                  className={`w-full text-left rounded px-1 -mx-1 transition-colors ${hasDetail ? "cursor-pointer hover:bg-t-panel-hover/40" : ""}`}
                  onClick={() => {
                    if (!hasDetail) return;
                    setExpandedTradeId(isExpanded ? null : -run.id);
                  }}
                >
                  <div className="flex items-start gap-3">
                    <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                      {fmtTime(run.timestamp)}
                    </div>
                    <div className="flex-1 min-w-0 overflow-hidden">
                      <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-mono">
                        {showDecisionBadge && (
                          <span className={`text-[9px] px-1 py-px rounded font-bold ${
                            isSkip ? "bg-t-border/50 text-txt-muted" : isHold ? "bg-yellow-900/30 text-yellow-500" : "bg-accent-dim text-accent"
                          }`}>
                            {label}
                          </span>
                        )}
                        {side && (
                          <span className={`text-[9px] px-1 py-px rounded font-bold ${
                            side === "YES" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"
                          }`}>
                            {side}
                          </span>
                        )}
                        <span className={TIMELINE_MODEL_CLASS}>
                          {shortModelName(run.model_name)}
                        </span>
                        {pYes != null && <span className="text-accent">model: {(pYes * 100).toFixed(0)}%</span>}
                        {row.yes_ask != null && <span className="text-txt-secondary">mkt: {(row.yes_ask * 100).toFixed(0)}c</span>}
                        {edge != null && <span className={pnlCls(edge)}>edge: {edge >= 0 ? "+" : ""}{(edge * 100).toFixed(0)}pp</span>}
                        {isSkip && run.reasoning && (
                          <span className="text-txt-muted break-words">
                            why: {run.reasoning}
                          </span>
                        )}
                        {hasDetail && <span className="text-[8px] text-txt-muted ml-auto">{isExpanded ? "▲" : "▼"}</span>}
                      </div>
                      {isExpanded && hasDetail && (
                        <div className="pt-2 pb-1 space-y-2">
                          {holdRationale && (
                            <div className="rounded border border-yellow-700/40 bg-yellow-900/15 px-2.5 py-1.5">
                              <div className="text-[9px] font-medium text-yellow-500 mb-0.5">Hold Rationale</div>
                              <p className="text-[10px] text-yellow-200/80 leading-relaxed">{holdRationale}</p>
                            </div>
                          )}
                          <RationalePanel reasoning={run.reasoning} sources={run.sources ?? []} />
                        </div>
                      )}
                    </div>
                  </div>
                </button>
              </div>
            );
          }

          if (event.type === "switch") {
            const sellEntry = submittedTradeTimelineItem(event.sell.trade);
            const buyEntry = submittedTradeTimelineItem(event.buy.trade);
            const stepItems = [event.sell, event.buy];
            const sellContext = resolveTradeStepLineContext(event.sell, row, runDisplayContext, stepItems);
            const buyContext = resolveTradeStepLineContext(event.buy, row, runDisplayContext, stepItems);
            const sharedEdge = sameEdgeValue(sellContext.edge, buyContext.edge) ? buyContext.edge : null;
            const resultingPosition = event.buy.resultingPosition;
            const buyPendingNote = tradePendingNote(event.buy.trade);
            // Surface reasoning from the matched model run on either leg
            const switchRun = event.buy.matchedRun ?? event.sell.matchedRun;
            const switchRationale =
              event.buy.trade.prediction?.reasoning ??
              event.sell.trade.prediction?.reasoning ??
              switchRun?.reasoning ?? null;
            const switchSources =
              event.buy.trade.prediction?.sources ??
              event.sell.trade.prediction?.sources ??
              switchRun?.sources ?? [];
            const switchHasDetail = !!(switchRationale || switchSources.length > 0);
            // Use a negative offset of the sell trade id to avoid collisions with regular trade ids
            const switchExpandKey = -(event.sell.trade.id + 1000000);
            const switchIsExpanded = expandedTradeId === switchExpandKey;
            return (
              <div key={event.key} className="relative py-1.5">
                <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-purple-500 border-2 border-t-bg z-10" />
                <button
                  type="button"
                  className={`w-full text-left rounded px-1 -mx-1 transition-colors ${switchHasDetail ? "cursor-pointer hover:bg-t-panel-hover/40" : ""}`}
                  onClick={() => {
                    if (!switchHasDetail) return;
                    setExpandedTradeId(switchIsExpanded ? null : switchExpandKey);
                  }}
                >
                <div className="flex items-start gap-3">
                  <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                    <div className="flex flex-col gap-1">
                      <span>{fmtTime(event.sell.trade.created_at)}</span>
                      <span className="text-[8px] text-txt-muted">{fmtTime(event.buy.trade.created_at)}</span>
                    </div>
                  </div>
                  <div className="flex-1 min-w-0 overflow-hidden">
                    <div className="mb-1 flex flex-wrap items-center gap-2 text-[9px] font-mono">
                      <span className="text-txt-muted">
                        {event.sell.trade.side.toUpperCase()} {"->"} {event.buy.trade.side.toUpperCase()}
                      </span>
                      {switchHasDetail && <span className="text-[8px] text-txt-muted ml-auto">{switchIsExpanded ? "▲" : "▼"}</span>}
                    </div>
                    <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-mono rounded border border-t-border/40 bg-t-panel-hover/20 px-2 py-1.5">
                      <span className="text-[9px] px-1 py-px rounded font-bold bg-warn-dim text-warn">SELL</span>
                      <span className={sideToneClass(event.sell.trade.side)}>{event.sell.trade.side.toUpperCase()} {formatShareLabel(event.sell.trade.count)}</span>
                      <span className="text-txt-muted">@ {(sellEntry.price * 100).toFixed(0)}c</span>
                      <span className="text-warn">{formatFeeLabel(sellEntry.fee)}</span>
                      {sellEntry.cashFlow != null && <span className={pnlCls(sellEntry.cashFlow)}>cash: {sellEntry.cashFlow >= 0 ? "+" : ""}{fmtDollar(sellEntry.cashFlow)}</span>}
                      {sellContext.mktAsk != null && <span className="text-txt-secondary">mkt: {(sellContext.mktAsk * 100).toFixed(0)}c</span>}
                      {(sharedEdge != null || sellContext.edge != null) && (
                        <span className={pnlCls(sharedEdge ?? sellContext.edge ?? 0)}>
                          edge: {(sharedEdge ?? sellContext.edge ?? 0) >= 0 ? "+" : ""}{(((sharedEdge ?? sellContext.edge ?? 0) as number) * 100).toFixed(0)}pp
                        </span>
                      )}
                      <StatusBadge status={event.sell.trade.status} />
                    </div>
                    <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-mono mt-1 ml-4 rounded border border-t-border/30 bg-t-panel-hover/10 px-2 py-1.5">
                      <span className="text-accent">then</span>
                      <span className="text-[9px] px-1 py-px rounded font-bold bg-profit-dim text-profit">BUY</span>
                      <span className={sideToneClass(event.buy.trade.side)}>{event.buy.trade.side.toUpperCase()} {formatShareLabel(event.buy.trade.count)}</span>
                      <span className="text-txt-muted">@ {(buyEntry.price * 100).toFixed(0)}c</span>
                      <span className="text-warn">{formatFeeLabel(buyEntry.fee)}</span>
                      {buyEntry.cashFlow != null && <span className={pnlCls(buyEntry.cashFlow)}>cash: {buyEntry.cashFlow >= 0 ? "+" : ""}{fmtDollar(buyEntry.cashFlow)}</span>}
                      {buyContext.mktAsk != null && <span className="text-txt-secondary">mkt: {(buyContext.mktAsk * 100).toFixed(0)}c</span>}
                      {sharedEdge == null && buyContext.edge != null && <span className={pnlCls(buyContext.edge)}>edge: {buyContext.edge >= 0 ? "+" : ""}{(buyContext.edge * 100).toFixed(0)}pp</span>}
                      <StatusBadge status={event.buy.trade.status} />
                      {buyPendingNote && <span className="text-accent">{buyPendingNote}</span>}
                      {resultingPosition ? (
                        <span className="text-txt-muted">
                          hold: <span className={sideToneClass(resultingPosition.side, true)}>{resultingPosition.quantity} {resultingPosition.side}</span>
                        </span>
                      ) : (
                        <span className="text-txt-muted">hold: flat</span>
                      )}
                    </div>
                    {switchIsExpanded && switchHasDetail && (
                      <div className="pt-2 pb-1">
                        <RationalePanel reasoning={switchRationale} sources={switchSources} />
                      </div>
                    )}
                  </div>
                </div>
                </button>
              </div>
            );
          }

          const { trade } = event.item;
          const { qty, isSell, fee, price, cashFlow } = submittedTradeTimelineItem(trade);
          const matchedRun = event.item.matchedRun;
          const rationale = trade.prediction?.reasoning ?? matchedRun?.reasoning ?? null;
          const sources = trade.prediction?.sources ?? matchedRun?.sources ?? [];
          const hasDetail = !!(rationale || sources.length > 0);
          const isExpanded = expandedTradeId === trade.id;
          const { pYes, mktAsk, edge } = tradeDisplayContext(trade, matchedRun);
          const resultingPosition = event.item.resultingPosition;
          const pendingNote = tradePendingNote(trade);
          const modeBadge = tradeModeBadge(trade);

          return (
            <div key={trade.id} className="relative py-1.5">
              <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-accent border-2 border-t-bg z-10" />
              <button
                type="button"
                className={`w-full text-left rounded px-1 -mx-1 transition-colors ${hasDetail ? "cursor-pointer hover:bg-t-panel-hover/40" : ""}`}
                onClick={() => {
                  if (!hasDetail) return;
                  setExpandedTradeId(isExpanded ? null : trade.id);
                }}
              >
                <div className="flex items-start gap-3">
                  <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                    {fmtTime(trade.created_at)}
                  </div>
                  <div className="flex-1 min-w-0 overflow-hidden">
                    <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-mono">
                      {isSell && <span className="text-[9px] px-1 py-px rounded font-bold bg-warn-dim text-warn">SELL</span>}
                      <span className={`text-[9px] px-1 py-px rounded font-bold ${trade.side.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                        {trade.side.toUpperCase()}
                      </span>
                      <span className="text-txt-primary">
                        {formatShareLabel(trade.count)}
                        {qty > 0 && qty !== trade.count ? ` · ${qty} filled` : ""}
                      </span>
                      <span className="text-txt-muted">@ {(price * 100).toFixed(0)}c</span>
                      <span className="text-warn">{formatFeeLabel(fee)}</span>
                      <span className={cashFlow != null ? pnlCls(cashFlow) : "text-txt-muted"}>
                        cash: {cashFlow != null ? `${cashFlow >= 0 ? "+" : ""}${fmtDollar(cashFlow)}` : "--"}
                      </span>
                      {resultingPosition ? (
                        <span className="text-txt-muted">
                          hold: <span className={sideToneClass(resultingPosition.side, true)}>{resultingPosition.quantity} {resultingPosition.side}</span>
                        </span>
                      ) : (
                        <span className="text-txt-muted">hold: flat</span>
                      )}
                      {pYes != null && <span className="text-accent">model: {(pYes * 100).toFixed(0)}%</span>}
                      {mktAsk != null && <span className="text-txt-secondary">mkt: {(mktAsk * 100).toFixed(0)}c</span>}
                      {edge != null && <span className={pnlCls(edge)}>edge: {edge >= 0 ? "+" : ""}{(edge * 100).toFixed(0)}pp</span>}
                      <StatusBadge status={trade.status} />
                      {pendingNote && <span className="text-accent">{pendingNote}</span>}
                      <span className={`text-[9px] font-bold px-1 py-px rounded ${modeBadge.className}`}>
                        {modeBadge.label}
                      </span>
                      {hasDetail && <span className="text-[8px] text-txt-muted ml-auto">{isExpanded ? "▲" : "▼"}</span>}
                    </div>
                    {isExpanded && hasDetail && (
                      <div className="pt-2 pb-1">
                        <RationalePanel reasoning={rationale} sources={sources} />
                      </div>
                    )}
                  </div>
                </div>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const SAME_ACTION_WINDOW_MS = 5 * 60 * 1000; // 5 min — allows delayed second leg of one rebalance step

type TimelineTradeItem = {
  trade: Trade;
  matchedRun: ModelRun | null;
  resultingPosition: TimelinePositionState;
};

function syncedTimelinePosition(row: UnifiedMarketRow): TimelinePositionState {
  if (!row.position || row.position.quantity <= 0) return null;
  return {
    quantity: row.position.quantity,
    side: row.position.contract.toUpperCase(),
  };
}

function replayedTimelinePosition(chronTrades: Trade[]): TimelinePositionState {
  let currentSignedQuantity = 0;

  chronTrades.forEach((trade) => {
    const qty = getExecutedTradeQuantity(trade);
    const side = trade.side?.toUpperCase() ?? null;
    const isSell = trade.action?.toUpperCase() === "SELL";
    if (qty <= 0 || !side) return;
    const signedDelta = side === "YES" ? qty : -qty;
    currentSignedQuantity += isSell ? -signedDelta : signedDelta;
  });

  if (currentSignedQuantity === 0) return null;
  return {
    quantity: Math.abs(currentSignedQuantity),
    side: currentSignedQuantity > 0 ? "YES" : "NO",
  };
}

function sameTimelinePosition(a: TimelinePositionState, b: TimelinePositionState): boolean {
  if (!a && !b) return true;
  if (!a || !b) return false;
  return a.side === b.side && Math.abs(a.quantity - b.quantity) < 0.0005;
}

function formatTimelinePosition(position: TimelinePositionState): string {
  if (!position) return "flat";
  return `${position.quantity} ${position.side}`;
}

function matchTradesToRuns(
  chronTrades: Trade[],
  chronRuns: ModelRun[],
  row: UnifiedMarketRow,
): TimelineTradeItem[] {
  const matchWindowMs = 15 * 60 * 1000;
  let currentSignedQuantity = 0;

  const items = chronTrades.map((trade) => {
    const pred = trade.prediction;
    const tradeTs = new Date(trade.created_at).getTime();
    let matchedRun: ModelRun | null = null;

    if (pred) {
      const candidates = chronRuns.filter((run) => {
        if (run.model_name !== pred.source) return false;
        if (run.p_yes == null) return false;
        if (Math.abs(run.p_yes - pred.p_yes) > 0.0005) return false;
        const runTs = new Date(run.timestamp).getTime();
        return !isNaN(runTs) && Math.abs(runTs - tradeTs) <= matchWindowMs;
      });

      if (candidates.length > 0) {
        matchedRun = candidates.reduce((best, run) => {
          const bestDiff = Math.abs(new Date(best.timestamp).getTime() - tradeTs);
          const runDiff = Math.abs(new Date(run.timestamp).getTime() - tradeTs);
          return runDiff < bestDiff ? run : best;
        });
      }
    }

    // Fallback: if no prediction link (e.g. rebalance orders with missing
    // signal_id), match the nearest model run by timestamp alone so that
    // reasoning is still surfaced.
    if (!matchedRun) {
      const nearbyRuns = chronRuns.filter((run) => {
        if (run.p_yes == null) return false;
        const runTs = new Date(run.timestamp).getTime();
        return !isNaN(runTs) && Math.abs(runTs - tradeTs) <= matchWindowMs;
      });
      if (nearbyRuns.length > 0) {
        matchedRun = nearbyRuns.reduce((best, run) => {
          const bestDiff = Math.abs(new Date(best.timestamp).getTime() - tradeTs);
          const runDiff = Math.abs(new Date(run.timestamp).getTime() - tradeTs);
          return runDiff < bestDiff ? run : best;
        });
      }
    }

    const qty = getExecutedTradeQuantity(trade);
    const side = trade.side?.toUpperCase() ?? null;
    const isSell = trade.action?.toUpperCase() === "SELL";
    if (qty > 0 && side) {
      const signedDelta = side === "YES" ? qty : -qty;
      currentSignedQuantity += isSell ? -signedDelta : signedDelta;
    }

    const resultingPosition = currentSignedQuantity === 0
      ? null
      : {
          quantity: Math.abs(currentSignedQuantity),
          side: currentSignedQuantity > 0 ? "YES" : "NO",
        };

    return {
      trade,
      matchedRun,
      resultingPosition,
    };
  });

  if (items.length > 0) {
    // The sync-backed position is authoritative for the current end state even
    // when older snapshot-derived order history is noisy or incomplete.
    items[items.length - 1] = {
      ...items[items.length - 1],
      resultingPosition: syncedTimelinePosition(row),
    };
  }

  return items;
}

function unmatchedTimelineRuns(
  tradesWithRuns: TimelineTradeItem[],
  chronRuns: ModelRun[],
  pendingOrders: PendingOrder[] = []
): ModelRun[] {
  const matchedIds = new Set(
    tradesWithRuns
      .map((item) => item.matchedRun?.id)
      .filter((id): id is number => id != null)
  );
  return chronRuns.filter((run) => {
    if (matchedIds.has(run.id)) return false;
    return !pendingOrders.some((order) => pendingOrderMatchesRun(order, run));
  });
}

function shouldShowInTradesTab(trade: Trade): boolean {
  return (trade.status?.toUpperCase() ?? "") !== "REJECTED";
}

// ── Tab 2: Trade History ────────────────────────────────────

function TradesTab({ row, modelRuns }: { row: UnifiedMarketRow; modelRuns: ModelRun[] | null }) {
  const chronTrades = useMemo(
    () => [...row.trades]
      .filter((trade) => !isSyntheticTrade(trade) && shouldShowInTradesTab(trade))
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [row.trades]
  );
  const chronRuns = useMemo(
    () => [...(modelRuns ?? [])].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()),
    [modelRuns]
  );
  const runDisplayContext = useMemo(() => buildRunDisplayContext(chronRuns, row), [chronRuns, row]);
  const tradesWithRuns = useMemo(() => matchTradesToRuns(chronTrades, chronRuns, row), [chronTrades, chronRuns, row]);
  const tradeStepGroups = useMemo(() => buildTradeStepGroups(tradesWithRuns), [tradesWithRuns]);

  if (tradeStepGroups.length === 0) {
    return <div className="text-[10px] text-txt-muted">No trades for this market</div>;
  }

  const totalCashFlow = tradeStepGroups.reduce(
    (sum, group) => sum + group.lines.reduce((groupSum, item) => groupSum + (submittedTradeTimelineItem(item.trade).cashFlow ?? 0), 0),
    0
  );

  const pos = row.position;
  const currentBid = pos
    ? pos.contract.toLowerCase() === "yes"
      ? (row.yes_bid ?? (row.no_ask != null ? 1.0 - row.no_ask : null))
      : (row.no_bid ?? (row.yes_ask != null ? 1.0 - row.yes_ask : null))
    : null;
  const openValue = pos && currentBid != null ? pos.quantity * currentBid : null;
  const totalNet = openValue != null ? totalCashFlow + openValue : null;

  return (
    <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
      <table className="w-full text-[10px]">
        <thead>
          <tr className="text-txt-muted text-[9px] uppercase tracking-widest border-b border-t-border/40">
            <th className="px-2 py-1.5 text-left font-medium">Time</th>
            <th className="px-2 py-1.5 text-center font-medium">Side</th>
            <th className="px-2 py-1.5 text-right font-medium">Qty</th>
            <th className="px-2 py-1.5 text-right font-medium">Price</th>
            <th className="px-2 py-1.5 text-right font-medium">Fee</th>
            <th className="px-2 py-1.5 text-right font-medium">Cash</th>
            <th className="px-2 py-1.5 text-right font-medium">Edge</th>
            <th className="px-2 py-1.5 text-center font-medium">End Hold</th>
            <th className="px-2 py-1.5 text-center font-medium">Status</th>
            <th className="px-2 py-1.5 text-center font-medium">Mode</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-t-border/20">
          {tradeStepGroups.map((group) => {
            const latestTrade = group.lines[group.lines.length - 1].trade;
            const firstTrade = group.lines[0].trade;
            const lastTrade = group.lines[group.lines.length - 1].trade;
            const isRebalanceStep = group.lines.length > 1;

            return (
              <tr key={group.key} className={`${isRebalanceStep ? "bg-t-panel-hover/20" : ""} hover:bg-t-panel-hover/50 align-top`}>
                <td className="px-2 py-1.5 font-mono text-txt-muted whitespace-nowrap">
                  <div className="flex flex-col gap-1">
                    <span>{fmtTime(latestTrade.created_at)}</span>
                    {isRebalanceStep && (
                      <span className="inline-flex w-fit items-center gap-1 rounded border border-accent/30 bg-accent/10 px-1.5 py-0.5 text-[8px] font-medium">
                        <span className={sideToneClass(firstTrade.side, true)}>{firstTrade.side.toUpperCase()}</span>
                        <span className="text-txt-muted">-&gt;</span>
                        <span className={sideToneClass(lastTrade.side, true)}>{lastTrade.side.toUpperCase()}</span>
                        <span className="text-accent">rebalance</span>
                      </span>
                    )}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-center">
                  <div className="flex flex-col">
                    {group.lines.map((item, index) => {
                      const trade = item.trade;
                      const isSell = trade.action?.toUpperCase() === "SELL";
                      return (
                        <div
                          key={trade.id}
                          className={`flex items-center justify-center gap-1 ${index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}`}
                        >
                          <span className={`text-[8px] px-1 py-px rounded font-bold ${isSell ? "bg-warn-dim text-warn" : "bg-profit-dim text-profit"}`}>
                            {isSell ? "SELL" : "BUY"}
                          </span>
                          <span className={`inline-block px-1 py-px rounded text-[8px] font-bold ${
                            trade.side.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"
                          }`}>
                            {trade.side.toUpperCase()}
                          </span>
                        </div>
                      );
                    })}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-txt-primary">
                  <div className="flex flex-col items-end">
                    {group.lines.map((item, index) => {
                      const entry = submittedTradeTimelineItem(item.trade);
                      const isExecuted = entry.qty > 0;
                      return (
                        <div key={item.trade.id} className={index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}>
                          {item.trade.count}
                          {!isExecuted && item.trade.filled_shares > 0 ? (
                            <span className="ml-1 text-txt-muted">({item.trade.filled_shares} filled)</span>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-txt-primary">
                  <div className="flex flex-col items-end">
                    {group.lines.map((item, index) => {
                      const entry = submittedTradeTimelineItem(item.trade);
                      return (
                        <div key={item.trade.id} className={index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}>
                          {Math.round(entry.price * 100)}c
                        </div>
                      );
                    })}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-warn">
                  <div className="flex flex-col items-end">
                    {group.lines.map((item, index) => {
                      const entry = submittedTradeTimelineItem(item.trade);
                      return (
                        <div key={item.trade.id} className={index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}>
                          {fmtDollar(entry.fee)}
                        </div>
                      );
                    })}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono font-medium">
                  <div className="flex flex-col items-end">
                    {group.lines.map((item, index) => {
                      const entry = submittedTradeTimelineItem(item.trade);
                      return (
                        <div
                          key={item.trade.id}
                          className={`${entry.cashFlow != null ? pnlCls(entry.cashFlow) : "text-txt-muted"} ${index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}`}
                        >
                          {entry.cashFlow != null ? `${entry.cashFlow >= 0 ? "+" : ""}${fmtDollar(entry.cashFlow)}` : "--"}
                        </div>
                      );
                    })}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-right font-mono">
                  <div className="flex flex-col items-end">
                    {group.lines.map((item, index) => {
                      const context = resolveTradeStepLineContext(item, row, runDisplayContext, group.lines);
                      return (
                        <div
                          key={item.trade.id}
                          className={`${context.edge != null ? pnlCls(context.edge) : "text-txt-muted"} ${index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}`}
                        >
                          {context.edge != null ? `${context.edge >= 0 ? "+" : ""}${(context.edge * 100).toFixed(1)}pp` : "--"}
                        </div>
                      );
                    })}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-center font-mono text-txt-muted">
                  <div className="flex flex-col items-center">
                    {group.lines.map((item, index) => (
                      <div key={item.trade.id} className={index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}>
                        {item.resultingPosition ? (
                          <span className={sideToneClass(item.resultingPosition.side, true)}>
                            {item.resultingPosition.quantity} {item.resultingPosition.side}
                          </span>
                        ) : (
                          <span className="text-txt-muted">flat</span>
                        )}
                      </div>
                    ))}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-center">
                  <div className="flex flex-col items-center">
                    {group.lines.map((item, index) => (
                      <div key={item.trade.id} className={index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}>
                        <StatusBadge status={item.trade.status} />
                      </div>
                    ))}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-center">
                  <div className="flex flex-col items-center">
                    {group.lines.map((item, index) => (
                      <div key={item.trade.id} className={index > 0 ? "mt-1 border-t border-t-border/20 pt-1" : ""}>
                        <span className={`text-[8px] font-bold px-1 py-px rounded ${tradeModeBadge(item.trade).className}`}>
                          {tradeModeBadge(item.trade).label}
                        </span>
                      </div>
                    ))}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
        <tfoot>
          <tr className="border-t border-t-border/60 text-[9px] text-txt-muted">
            <td colSpan={7} className="px-2 py-1.5 font-medium">
              {pos && currentBid != null
                ? `Exit value: ${pos.quantity} ${pos.contract.toUpperCase()} × ${Math.round(currentBid * 100)}c bid`
                : "No open position"}
            </td>
            <td className="px-2 py-1.5 text-right font-mono">
              <div className="flex flex-col items-end gap-0.5">
                <span className="text-txt-muted" title="Net cash spent/received from all trades">
                  spent: <span className={pnlCls(totalCashFlow)}>{totalCashFlow >= 0 ? "+" : ""}{fmtDollar(totalCashFlow)}</span>
                </span>
                {openValue != null && (
                  <span className="text-txt-muted" title="Current market value of remaining position at bid price">
                    mkt value: <span className="text-txt-secondary">+{fmtDollar(openValue)}</span>
                  </span>
                )}
                {totalNet != null && (
                  <span className={`font-bold ${pnlCls(totalNet)}`} title="Net P&L = spent + current position value">
                    net P&L: {totalNet >= 0 ? "+" : ""}{fmtDollar(totalNet)}
                  </span>
                )}
              </div>
            </td>
            <td colSpan={2} />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const s: Record<string, string> = {
    FILLED: "bg-profit-dim text-profit",
    DRY_RUN: "bg-warn-dim text-warn",
    PENDING: "bg-accent-dim text-accent",
    REJECTED: "bg-loss-dim text-loss",
    CANCELLED: "bg-t-border text-txt-muted",
    ERROR: "bg-loss-dim text-loss",
  };
  return (
    <span className={`inline-block px-1 py-px rounded text-[8px] font-medium ${s[status] ?? "bg-t-border text-txt-muted"}`}>
      {status}
    </span>
  );
}

function RationalePanel({
  reasoning,
  sources,
}: {
  reasoning: string | null | undefined;
  sources: Array<{ url: string; title: string }> | null | undefined;
}) {
  if (!reasoning && (!sources || sources.length === 0)) return null;
  return (
    <div className="mt-2 w-full min-w-0 overflow-hidden rounded border border-t-border/40 bg-t-bg/60 p-2.5 space-y-2">
      {reasoning && (
        <p className="text-[10px] text-txt-secondary leading-relaxed break-words whitespace-pre-wrap">{reasoning}</p>
      )}
      {sources && sources.length > 0 && (
        <div className="min-w-0 space-y-0.5">
          <div className="text-[8px] uppercase tracking-widest text-txt-muted font-medium mb-1">Sources</div>
          {sources.map((s, i) => (
            <a
              key={i}
              href={s.url}
              target="_blank"
              rel="noopener noreferrer"
              className="group flex w-full min-w-0 items-start gap-1.5 overflow-hidden text-[9px] text-accent transition-colors hover:text-accent/80"
              title={s.url}
            >
              <span className="text-txt-muted group-hover:text-txt-secondary mt-px">↗</span>
              <span className="min-w-0 truncate">{s.title || s.url}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}


function ModelsTab({
  row,
  apiClient,
  instanceCacheKey,
  modelRuns,
  loadingRuns,
}: {
  row: UnifiedMarketRow;
  apiClient: ApiClient;
  instanceCacheKey: string;
  modelRuns: ModelRun[] | null;
  loadingRuns: boolean;
}) {
  const [priceHistory, setPriceHistory] = useState<PriceHistoryPoint[] | null>(null);
  const [loadingChart, setLoadingChart] = useState(false);
  const priceHistoryCacheRef = useRef<Map<string, PriceHistoryPoint[]>>(new Map());

  useEffect(() => {
    const cacheKey = `${instanceCacheKey}:${row.market_id}`;

    // Fetch price history
    const cachedChart = priceHistoryCacheRef.current.get(cacheKey);
    if (cachedChart) {
      setPriceHistory(cachedChart);
    } else {
      let cancelled = false;
      setLoadingChart(true);
      apiClient.getPriceHistory(row.market_id).then((data) => {
        if (cancelled) return;
        const nextData = Array.isArray(data) ? data : [];
        priceHistoryCacheRef.current.set(cacheKey, nextData);
        setPriceHistory(nextData);
        setLoadingChart(false);
      }).catch(() => {
        if (cancelled) return;
        setPriceHistory([]);
        setLoadingChart(false);
      });
      return () => { cancelled = true; };
    }
  }, [apiClient, instanceCacheKey, row.market_id]);

  const preds = row.model_predictions;
  const modelSummary = useMemo(() => {
    const names = [
      ...preds.map((pred) => pred.model_name),
      ...(modelRuns ?? []).map((run) => run.model_name),
      ...(priceHistory ?? []).map((point) => point.model_name).filter((name): name is string => !!name),
    ].filter(Boolean);
    const unique = Array.from(new Set(names));
    if (unique.length === 0) return null;
    if (unique.length === 1) return shortModelName(unique[0]);
    return unique.map(shortModelName).join(", ");
  }, [preds, modelRuns, priceHistory]);

  return (
    <div className="space-y-4">
      {/* Current cycle per-model breakdown */}
      {preds.length > 0 ? (
        <div>
          <div className="flex items-center gap-4 text-[9px] text-txt-muted uppercase tracking-wider mb-2">
            <span>Models</span>
          </div>
          <div className="grid gap-2">
            {preds.map((pred, i) => (
              <div key={pred.model_name} className="flex items-center gap-3 text-[10px] font-mono">
                <span className={`min-w-0 truncate font-medium ${MODEL_COLORS[i % MODEL_COLORS.length]}`}>
                  {shortModelName(pred.model_name)}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <div className="text-[10px] text-txt-muted">
          {modelSummary ?? "Model"}
        </div>
      )}

      {/* Price history chart */}
      {loadingChart && (
        <div className="text-[10px] text-txt-muted">Loading price history...</div>
      )}
      {priceHistory && priceHistory.length > 0 && (
        <PriceHistoryChart data={priceHistory} />
      )}
    </div>
  );
}

// ── Price History Chart ─────────────────────────────────────

function PriceHistoryChart({ data }: { data: PriceHistoryPoint[] }) {
  const chartData = useMemo(() => {
    return data.map((p) => ({
      time: fmtTime(p.timestamp),
      yesAsk: p.yes_ask != null ? Math.round(p.yes_ask * 100) : null,
      modelP: p.model_p_yes != null ? Math.round(p.model_p_yes * 100) : null,
    }));
  }, [data]);

  const priceUnchanged = useMemo(() => {
    if (chartData.length < 2) return true;
    const first = chartData[0].yesAsk;
    return chartData.every((p) => p.yesAsk === first);
  }, [chartData]);

  return (
    <div>
      <div className="flex items-center gap-2 mb-1">
        <div className="text-[9px] text-txt-muted uppercase tracking-widest font-medium">
          Market Price vs Model Probability
        </div>
        {priceUnchanged && (
          <span className="text-[9px] text-txt-muted italic">
            — price unchanged, no new predictions generated
          </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={160}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke={CHART_COLORS.grid} vertical={false} />
          <XAxis
            dataKey="time"
            stroke="transparent"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            tick={{ fill: CHART_COLORS.muted }}
          />
          <YAxis
            stroke="transparent"
            fontSize={9}
            tickLine={false}
            axisLine={false}
            tick={{ fill: CHART_COLORS.muted }}
            width={30}
            tickFormatter={(v) => `${v}`}
          />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={TOOLTIP_LABEL_STYLE}
            formatter={(value: number, name: string) => {
              const labels: Record<string, string> = { yesAsk: "Market", modelP: "Model" };
              return [`${value}c`, labels[name] ?? name];
            }}
          />
          <Line
            type="monotone"
            dataKey="yesAsk"
            stroke={CHART_COLORS.loss}
            strokeWidth={1.5}
            dot={false}
            connectNulls
            name="yesAsk"
          />
          <Line
            type="monotone"
            dataKey="modelP"
            stroke={CHART_COLORS.accent}
            strokeWidth={1.5}
            dot={false}
            connectNulls
            name="modelP"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

// ── Info Button ──────────────────────────────────────────────

function InfoButton({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  const handleEnter = useCallback(() => {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      const centerX = r.left + r.width / 2;
      // Clamp so the popover (max 240px) stays within viewport
      const half = 120;
      const clampedLeft = Math.max(half + 8, Math.min(centerX, window.innerWidth - half - 8));
      setPos({ top: r.top - 8, left: clampedLeft });
    }
    setShow(true);
  }, []);

  return (
    <span
      ref={ref}
      className="relative inline-flex items-center justify-center w-3.5 h-3.5 ml-1 rounded border border-txt-muted/30 text-[7px] text-txt-muted cursor-help align-middle hover:border-accent hover:text-accent transition-colors"
      onMouseEnter={handleEnter}
      onMouseLeave={() => setShow(false)}
      onClick={(e) => e.stopPropagation()}
    >
      ?
      {show && (
        <span
          className="fixed -translate-x-1/2 -translate-y-full w-max max-w-[240px] whitespace-normal rounded border border-t-border bg-[#141a22] px-3 py-2 text-[10px] text-left font-mono font-normal normal-case tracking-normal leading-snug text-txt-primary shadow-xl z-[9999] pointer-events-none"
          style={{ top: pos.top, left: pos.left }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

// ── Sortable Header ─────────────────────────────────────────

function Th({
  children,
  k,
  sortKeys,
  onClick,
  align,
  info,
}: {
  children: React.ReactNode;
  k: SortKey;
  sortKeys: Array<{ key: SortKey; asc: boolean }>;
  onClick: (k: SortKey, multi: boolean) => void;
  align: "left" | "center" | "right";
  info?: string;
}) {
  const idx = sortKeys.findIndex((s) => s.key === k);
  const active = idx >= 0;
  const asc = active ? sortKeys[idx].asc : false;
  const multi = sortKeys.length > 1;
  const cls = align === "left" ? "text-left" : align === "right" ? "text-right" : "text-center";
  return (
    <th
      className={`px-3 py-2 font-medium cursor-pointer select-none hover:text-txt-primary transition-colors ${cls} ${active ? "text-txt-primary" : ""}`}
      onClick={(e) => onClick(k, e.shiftKey)}
    >
      {children}
      {info && <InfoButton text={info} />}
      {active && (
        <span className="ml-0.5 text-accent text-[8px]">
          {multi && <span className="text-[7px] text-txt-muted mr-0.5">{idx + 1}</span>}
          {asc ? "\u25B2" : "\u25BC"}
        </span>
      )}
    </th>
  );
}
