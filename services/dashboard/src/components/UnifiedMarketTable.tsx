"use client";

import { useState, useMemo, useEffect, useRef, useCallback } from "react";
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
  CycleEvaluation,
} from "@/lib/api";
import { buildUnifiedMarketRows, liveNetPnl, kalshiMarketUrl, kalshiEventUrl, api } from "@/lib/api";
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
  const normalized = decision?.toUpperCase();
  return normalized === "HOLD" || normalized === "HOLD_NOPROFIT";
}

function buildHoldExplanation(evaluation: CycleEvaluation): string {
  const resultingPosition = (evaluation as any).resultingPosition as { quantity: number; side: string } | undefined;
  const edge = evaluation.prediction?.edge;
  const yesAskPct = evaluation.prediction?.yes_ask != null ? evaluation.prediction.yes_ask * 100 : null;

  if (yesAskPct != null && (yesAskPct <= 3 || yesAskPct >= 97)) {
    return `Holding because market price ${yesAskPct.toFixed(1)}% is outside the normal tradeable range.`;
  }
  if (edge != null && Math.abs(edge) < 3) {
    return `Holding because edge ${edge.toFixed(1)}% is below the minimum trade threshold.`;
  }
  if (resultingPosition) {
    return `Holding ${resultingPosition.quantity} ${resultingPosition.side} because no rebalance was triggered this cycle.`;
  }
  if (edge != null) {
    return `Holding because no order was placed this cycle despite edge ${edge.toFixed(1)}%.`;
  }
  return "Holding because no action was taken this cycle.";
}

type SortKey =
  | "title"
  | "yes_ask"
  | "predicted"
  | "edge"
  | "position"
  | "avg_price"
  | "unrealized"
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
        if (pa == null) return null; // nulls to bottom
        if (pb == null) return null;
        return pa - pb;
      }
      case "capital": return (a.position?.capital ?? 0) - (b.position?.capital ?? 0);
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
        const ra = a.position ? (liveNetPnl(a) ?? 0) / (a.position.capital || 1) : 0;
        const rb = b.position ? (liveNetPnl(b) ?? 0) / (b.position.capital || 1) : 0;
        return ra - rb;
      }
    }
  }, []);

  // Sort and prioritize markets with actual trades or pending orders
  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      // FIRST PRIORITY: Markets with actual trades OR pending orders come before inactive markets
      const aIsActive = a.has_trades || a.trade_count > 0 || (a.pending_shares && a.pending_shares > 0);
      const bIsActive = b.has_trades || b.trade_count > 0 || (b.pending_shares && b.pending_shares > 0);
      if (aIsActive && !bIsActive) return -1;
      if (!aIsActive && bIsActive) return 1;

      // SECOND PRIORITY: Apply normal sorting within each group
      for (const { key, asc } of sortKeys) {
        const diff = computeMarketDiff(key, a, b);
        // null means "sort to bottom regardless of direction"
        if (diff === null) {
          const pa = key === "unrealized" ? liveNetPnl(a) : key === "expiration" ? a.expiration : null;
          const pb = key === "unrealized" ? liveNetPnl(b) : key === "expiration" ? b.expiration : null;
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
      const capital = row.position?.capital ?? 0;

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
                <Th k="yes_ask" sortKeys={sortKeys} onClick={handleSort} align="right" info="Current Yes / No ask prices on Kalshi">Mkt Price</Th>
                <Th k="predicted" sortKeys={sortKeys} onClick={handleSort} align="right" info="Model probability (p_yes from the prediction model)">Model P</Th>
                <Th k="edge" sortKeys={sortKeys} onClick={handleSort} align="right" info="Edge = Agg P − Yes Ask. Positive = model thinks YES is underpriced">Edge</Th>
                <Th k="position" sortKeys={sortKeys} onClick={handleSort} align="center" info="Current open position: side (YES/NO) and number of contracts">Position</Th>
                <Th k="avg_price" sortKeys={sortKeys} onClick={handleSort} align="right" info="Weighted average price paid per contract">Avg Entry</Th>
                <Th k="unrealized" sortKeys={sortKeys} onClick={handleSort} align="right" info="Net P&L = cash flow + open value. Cash flow = Σ(SELL proceeds) − Σ(BUY costs) from fill prices. Open value = quantity × current bid (1 − no_ask for YES, 1 − yes_ask for NO). Fully live — recalculated on every refresh.">P&L</Th>
                <Th k="capital" sortKeys={sortKeys} onClick={handleSort} align="right" info="Total capital invested: avg entry price × quantity">Investment</Th>
                <Th k="last_trade" sortKeys={sortKeys} onClick={handleSort} align="right" info="Timestamp of the most recent trade in this market">Last Trade</Th>
                <Th k="expiration" sortKeys={sortKeys} onClick={handleSort} align="right" info="Market close/expiration date">Closes</Th>
              </tr>
            </thead>
            <tbody className="divide-y divide-t-border/40">
              {visible.map((row, index) => {
                const isExpanded = expandedMarketId === row.market_id;
                const hasMultipleModels = row.model_predictions.length > 1;

                // Check if this is the first inactive market after active markets
                const prevIsActive = index > 0 && (
                  visible[index - 1].has_trades ||
                  visible[index - 1].trade_count > 0 ||
                  (visible[index - 1].pending_shares != null && visible[index - 1].pending_shares! > 0)
                );
                const currIsInactive = !(
                  row.has_trades ||
                  row.trade_count > 0 ||
                  (row.pending_shares != null && row.pending_shares! > 0)
                );
                const isFirstInactive = index > 0 && prevIsActive && currIsInactive;

                return (
                  <>
                    {/* Add divider row between active and inactive markets */}
                    {isFirstInactive && (
                      <tr key={`divider-${row.market_id}`} className="bg-t-bg-secondary/30">
                        <td colSpan={11} className="px-3 py-2 text-center">
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
                      key={row.market_id}
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
                  </>
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
                          {fmtDollar(row.position?.capital ?? 0)}
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
              {row.category.toUpperCase() === "MENTIONS" && (
                <span className="text-[8px] text-red-400 italic">no betting</span>
              )}
            </div>
          ) : (
            <span className="text-txt-muted text-[9px]">—</span>
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
              {row.pending_shares != null && row.pending_shares > 0 && (
                <span className="text-[9px] font-mono text-yellow-400" title="Orders placed but not yet filled">
                  +{row.pending_shares} pending
                </span>
              )}
              {row.target_shares != null && row.target_shares !== pos.quantity && (
                <span className={`text-[8px] font-mono ${row.target_shares < pos.quantity ? "text-loss" : "text-profit"}`}
                  title={`Rebalancing: Target ${row.target_shares} - Current ${pos.quantity} = ${row.target_shares > pos.quantity ? "Buy" : "Sell"} ${Math.abs(row.target_shares - pos.quantity)}`}>
                  → {row.target_shares} ({row.target_shares > pos.quantity ? "+" : "-"}{Math.abs(row.target_shares - pos.quantity)})
                </span>
              )}
            </span>
          ) : row.edge && Math.abs(row.edge) > 0.01 ? (
            <span className="flex flex-col items-center gap-0.5">
              <span className="font-mono text-txt-muted">0</span>
              {row.pending_shares != null && row.pending_shares > 0 && (
                <span className="text-[9px] font-mono text-yellow-400" title="Orders placed but not yet filled">
                  +{row.pending_shares} pending
                </span>
              )}
              {row.target_shares != null && (
                <span className="text-[8px] font-mono text-txt-muted"
                  title={`Target position based on edge: ${row.target_shares}`}>
                  target: {row.target_shares}
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

        {/* Live Net P&L */}
        {(() => { const net = liveNetPnl(row); return (
          <td className={`px-3 py-2 text-right font-mono font-medium ${net != null ? pnlCls(net) : "text-txt-muted"}`}>
            {net != null ? fmtDollar(net) : "--"}
          </td>
        ); })()}

        {/* Capital */}
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          {pos ? fmtDollar(pos.capital) : "--"}
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
          <td colSpan={11} className="p-0">
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

  // Cycle evaluations state (moved up from TimelineTab)
  const [cycleEvaluations, setCycleEvaluations] = useState<CycleEvaluation[]>([]);
  const [totalCycleEvaluations, setTotalCycleEvaluations] = useState(0);
  const [loadingEvaluations, setLoadingEvaluations] = useState(false);
  const [evaluationsOffset, setEvaluationsOffset] = useState(0);
  const EVALUATIONS_BATCH_SIZE = 10;

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

  // Fetch cycle evaluations
  const fetchCycleEvaluations = useCallback((offset: number, append = false) => {
    // Extract ticker from market_id (format: "kalshi:TICKER")
    const ticker = row.market_id?.startsWith('kalshi:')
      ? row.market_id.substring(7)
      : row.ticker || row.event_ticker || '';

    if (!ticker) return;

    setLoadingEvaluations(true);

    apiClient.getCycleEvaluations(ticker, EVALUATIONS_BATCH_SIZE, offset)
      .then((data) => {
        if (append) {
          setCycleEvaluations(prev => [...prev, ...(data.evaluations || [])]);
        } else {
          setCycleEvaluations(data.evaluations || []);
        }
        setTotalCycleEvaluations(data.total || 0);
        setEvaluationsOffset(offset + EVALUATIONS_BATCH_SIZE);
      })
      .catch((error) => {
        console.error('[Timeline] Failed to fetch cycle evaluations:', error);
        if (!append) {
          setCycleEvaluations([]);
        }
      })
      .finally(() => {
        setLoadingEvaluations(false);
      });
  }, [apiClient, row.market_id, row.ticker, row.event_ticker]);

  // Fetch initial evaluations when timeline tab is selected
  useEffect(() => {
    if (activeTab === "timeline" && cycleEvaluations.length === 0 && !loadingEvaluations) {
      fetchCycleEvaluations(0);
    }
  }, [activeTab, cycleEvaluations.length, loadingEvaluations, fetchCycleEvaluations]);

  const timelineCount = useMemo(() => {
    // Use cycle evaluations count if available, otherwise fall back to old calculation
    if (totalCycleEvaluations > 0) {
      return totalCycleEvaluations;
    }
    const chronTrades = [...row.trades].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    const chronRuns = [...(modelRuns ?? [])].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());
    const tradesWithRuns = matchTradesToRuns(chronTrades, chronRuns);
    return tradesWithRuns.length + unmatchedTimelineRuns(tradesWithRuns, chronRuns).length;
  }, [row.trades, modelRuns, totalCycleEvaluations]);

  const tabs: { key: typeof activeTab; label: string; count?: number }[] = [
    { key: "trades", label: "Trades", count: row.trade_count },
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
          <TimelineTab
            row={row}
            modelRuns={modelRuns}
            loadingRuns={loadingRuns}
            cycleEvaluations={cycleEvaluations}
            totalCycleEvaluations={totalCycleEvaluations}
            loadingEvaluations={loadingEvaluations}
            hasMore={cycleEvaluations.length < totalCycleEvaluations}
            onLoadMore={() => fetchCycleEvaluations(evaluationsOffset, true)}
          />
        )}
        {activeTab === "trades" && <TradesTab row={row} />}
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

const CYCLE_INTERVAL_MS = 60 * 60 * 1000; // 1 hour — worker poll interval
const SKIP_THRESHOLD_MS = 90 * 60 * 1000; // 90 min — 1.5× the 1-hour poll interval
const SAME_ACTION_WINDOW_MS = 2 * 60 * 1000; // 2 min — trades within same cycle

type TimelineTradeItem = {
  trade: Trade;
  idx: number;
  matchedRun: ModelRun | null;
};

function matchTradesToRuns(chronTrades: Trade[], chronRuns: ModelRun[]): TimelineTradeItem[] {
  const matchWindowMs = 15 * 60 * 1000;
  return chronTrades.map((trade, idx) => {
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

    return { trade, idx, matchedRun };
  });
}

function unmatchedTimelineRuns(tradesWithRuns: TimelineTradeItem[], chronRuns: ModelRun[]): ModelRun[] {
  const matchedIds = new Set(
    tradesWithRuns
      .map((item) => item.matchedRun?.id)
      .filter((id): id is number => id != null)
  );
  return chronRuns.filter((run) => !matchedIds.has(run.id) && isHoldLikeDecision(run.decision));
}

function TimelineTab({
  row,
  modelRuns,
  loadingRuns,
  cycleEvaluations,
  totalCycleEvaluations,
  loadingEvaluations,
  hasMore,
  onLoadMore,
}: {
  row: UnifiedMarketRow;
  modelRuns: ModelRun[] | null;
  loadingRuns: boolean;
  cycleEvaluations: CycleEvaluation[];
  totalCycleEvaluations: number;
  loadingEvaluations: boolean;
  hasMore: boolean;
  onLoadMore: () => void;
}) {
  const [expandedEntryId, setExpandedEntryId] = useState<string | null>(null);
  const [showPnLChart, setShowPnLChart] = useState(false);

  // Show trades in chronological order (oldest first)
  const chronTrades = useMemo(
    () => [...row.trades].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [row.trades]
  );

  const chronRuns = useMemo(
    () => [...(modelRuns ?? [])].sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()),
    [modelRuns]
  );

  const tradesWithRuns = useMemo(() => matchTradesToRuns(chronTrades, chronRuns), [chronTrades, chronRuns]);
  const unmatchedRuns = useMemo(() => unmatchedTimelineRuns(tradesWithRuns, chronRuns), [tradesWithRuns, chronRuns]);
  const events = useMemo(() => {
    // First sort chronologically to track positions correctly
    const sortedEvaluations = [...cycleEvaluations].sort((a, b) => {
      const aTime = a.timestamp ? new Date(a.timestamp).getTime() : 0;
      const bTime = b.timestamp ? new Date(b.timestamp).getTime() : 0;
      return aTime - bTime; // Oldest first for position tracking
    });

    // Track positions and last orders as we process events
    const positionsByMarket = new Map<string, { quantity: number; side: string }>();
    const lastOrderByMarket = new Map<string, { evaluation: CycleEvaluation; idx: number; filled: boolean }>();

    // First pass: mark superseded orders
    const evaluationsWithSuperseding = sortedEvaluations.map((evaluation, idx) => {
      const marketId = evaluation.market_id;
      const order = evaluation.order;

      // Look ahead to see if this unfilled order gets superseded
      let wasSuperseded = false;
      if (order && order.filled != null && order.filled < order.count) {
        // Check if a later order for the same market supersedes this one
        for (let j = idx + 1; j < sortedEvaluations.length; j++) {
          const laterEval = sortedEvaluations[j];
          if (laterEval.market_id === marketId && laterEval.order && laterEval.action?.type !== 'hold') {
            wasSuperseded = true;
            break;
          }
        }
      }

      return {
        ...evaluation,
        wasSuperseded,
      } as any;
    });

    const getExecutedCount = (order: CycleEvaluation["order"] | null): number => {
      if (!order) return 0;
      return order.filled != null ? order.filled : order.count;
    };

    const applyPositionChange = (
      currentPos: { quantity: number; side: string } | undefined,
      actionType: string | undefined,
      orderSide: string | null,
      order: CycleEvaluation["order"] | null,
    ): { quantity: number; side: string } | undefined => {
      if (!order || !orderSide) return currentPos;

      const executedCount = getExecutedCount(order);
      if (executedCount <= 0) return currentPos;

      if (actionType === "buy") {
        if (currentPos?.side === orderSide) {
          return {
            quantity: currentPos.quantity + executedCount,
            side: orderSide,
          };
        }

        return {
          quantity: executedCount,
          side: orderSide,
        };
      }

      if (actionType === "sell" && currentPos?.side === orderSide) {
        const remaining = Math.max(0, currentPos.quantity - executedCount);
        if (remaining === 0) return undefined;
        return {
          quantity: remaining,
          side: orderSide,
        };
      }

      return currentPos;
    };

    // Process each evaluation and detect position adjustments
    const evaluationEvents = evaluationsWithSuperseding.map((evaluation, idx) => {
      const marketId = evaluation.market_id;
      const order = evaluation.order;
      const orderSide = evaluation.action?.description?.match(/\b(YES|NO)\b/i)?.[1]?.toUpperCase();
      const normalizedActionType = evaluation.action?.type?.toLowerCase();

      let modifiedEvaluation = evaluation;

      // Check if there was a previous unfilled order for this market
      const lastOrder = lastOrderByMarket.get(marketId);
      if (lastOrder && !lastOrder.filled && order && evaluation.action?.type !== 'hold') {
        // Mark the previous order as superseded/cancelled
        // We'll handle this by modifying the description of the current order
        modifiedEvaluation = {
          ...evaluation,
          previousUnfilledOrder: lastOrder.evaluation.order,
        } as any;
      }

      // Check if this is a position adjustment
      if (order && marketId && orderSide && normalizedActionType === 'buy') {
        const currentPos = positionsByMarket.get(marketId);

        if (currentPos && currentPos.side === orderSide) {
          // This is increasing an existing position - mark as adjustment
          modifiedEvaluation = {
            ...modifiedEvaluation,
            action: {
              ...evaluation.action,
              type: "adjustment" as any,
              description: `BUY ${orderSide} ${order.count}`,
              originalDescription: evaluation.action?.description,
            },
          };
        }
      }

      const currentPos = marketId ? positionsByMarket.get(marketId) : undefined;
      const resultingPos = applyPositionChange(currentPos, normalizedActionType, orderSide, order);

      if (resultingPos) {
        positionsByMarket.set(marketId, resultingPos);
      } else if (marketId && currentPos) {
        positionsByMarket.delete(marketId);
      }

      if (resultingPos) {
        modifiedEvaluation = {
          ...modifiedEvaluation,
          resultingPosition: resultingPos,
        } as any;
      }

      // Track this order for future superseding detection
      if (order && marketId) {
        const isFilled = order.filled != null && order.filled === order.count;
        lastOrderByMarket.set(marketId, {
          evaluation,
          idx,
          filled: isFilled,
        });
      }

      return {
        type: "evaluation" as const,
        key: `eval-${evaluation.id}-${idx}`,
        sortTs: evaluation.timestamp ? new Date(evaluation.timestamp).getTime() : Date.now(),
        evaluation: modifiedEvaluation,
      };
    });

    // Group events by timestamp - events within 1 minute are considered same timestep
    const grouped = new Map<number, typeof evaluationEvents[0][]>();

    evaluationEvents.forEach(event => {
      // Round to nearest minute
      const roundedTs = Math.floor(event.sortTs / 60000) * 60000;

      if (!grouped.has(roundedTs)) {
        grouped.set(roundedTs, []);
      }
      grouped.get(roundedTs)!.push(event);
    });

    // Convert grouped events to final list
    const finalEvents: typeof evaluationEvents = [];

    grouped.forEach((eventsAtTime, timestamp) => {
      if (eventsAtTime.length === 1) {
        // Single event at this timestamp
        finalEvents.push(eventsAtTime[0]);
      } else {
        // Multiple events at same timestamp - could be a complex adjustment
        const sellEvent = eventsAtTime.find(e => e.evaluation.action?.type === 'sell');
        const buyEvent = eventsAtTime.find(e => e.evaluation.action?.type === 'buy' || e.evaluation.action?.type === 'adjustment');

        if (sellEvent && buyEvent) {
          // This is a SELL + BUY adjustment - create a combined event
          finalEvents.push({
            ...sellEvent,
            type: "evaluation" as const,
            key: `adjustment-${timestamp}`,
            evaluation: {
              ...sellEvent.evaluation,
              action: {
                type: "adjustment" as any,
                description: `SELL ${sellEvent.evaluation.action?.description?.match(/\b(YES|NO)\b/i)?.[1]?.toUpperCase() || ""} ${sellEvent.evaluation.order?.count || 0} → BUY ${buyEvent.evaluation.action?.description?.match(/\b(YES|NO)\b/i)?.[1]?.toUpperCase() || ""} ${buyEvent.evaluation.order?.count || 0}`,
                reason: "Position flip",
              },
              // Combine order information
              adjustment: {
                sell: sellEvent.evaluation,
                buy: buyEvent.evaluation,
              },
              resultingPosition: (buyEvent.evaluation as any).resultingPosition,
            } as any,
          });
        } else {
          // Not a sell+buy adjustment, keep all events
          finalEvents.push(...eventsAtTime);
        }
      }
    });

    const hasCurrentPendingExposure = (row.pending_shares ?? 0) > 0;
    const hasCurrentOpenPosition = (row.position?.quantity ?? 0) > 0;

    const hasActionableEvaluation = finalEvents.some((event) => {
      const actionType = event.evaluation.action?.type?.toLowerCase();
      return actionType != null && actionType !== "hold";
    });
    const hasTradeHistory = row.trades.length > 0;

    // Sort all events by timestamp, newest first, then drop phantom rows:
    // - HOLD evaluations on dead markets
    // - zero-fill action rows once there is no longer any live pending exposure
    return finalEvents
      .sort((a, b) => b.sortTs - a.sortTs)
      .filter((event) => {
        const actionType = event.evaluation.action?.type?.toLowerCase();
        const order = event.evaluation.order;
        const filledCount = order ? (order.filled ?? order.count) : 0;

        if (actionType !== "hold") {
          if (!order) return true;
          if (filledCount > 0) return true;
          return hasCurrentPendingExposure;
        }

        const resultingPosition = (event.evaluation as any).resultingPosition;
        if (!hasActionableEvaluation && !hasTradeHistory) return true;
        if (!resultingPosition) return false;
        if (hasCurrentPendingExposure) return true;
        if (hasCurrentOpenPosition) return true;

        return false;
      });
  }, [cycleEvaluations, row.pending_shares, row.position?.quantity, row.trades]);

  type TradeEvent = { type: "trade"; key: string; sortTs: number; item: TimelineTradeItem };
  type PredEvent = { type: "prediction"; key: string; sortTs: number; run: ModelRun };
  type EvalEvent = { type: "evaluation"; key: string; sortTs: number; evaluation: CycleEvaluation };
  type TradeGroupEvent = { type: "trade-group"; key: string; sortTs: number; items: TradeEvent[] };
  type GroupedTimelineEvent = TradeEvent | PredEvent | EvalEvent | TradeGroupEvent;

  const groupedEvents = useMemo((): GroupedTimelineEvent[] => {
    // Since we only have evaluation events, just return them directly
    // Later we can group SELL+BUY adjustments if needed
    return events as EvalEvent[];
  }, [events]);

  // Sorted prediction timestamps (oldest first, deduplicated by exact timestamp)
  const predTimes = useMemo(() => {
    const unique = new Set<number>();
    for (const run of [
      ...tradesWithRuns.map((item) => item.matchedRun).filter((run): run is ModelRun => run != null),
      ...unmatchedRuns,
    ]) {
      const ts = new Date(run.timestamp).getTime();
      if (!isNaN(ts)) unique.add(ts);
    }
    return Array.from(unique).sort((a, b) => a - b);
  }, [tradesWithRuns, unmatchedRuns]);

  // Build skip gap markers between consecutive predictions based on CYCLE_SKIPPED events
  const skipGaps = useMemo(() => {
    const gaps: { afterMs: number; skippedCycles: number; actualCount: boolean }[] = [];

    // Count CYCLE_SKIPPED runs between predictions
    if (modelRuns) {
      const sortedRuns = [...modelRuns].sort((a, b) =>
        new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime()
      );

      let skipCount = 0;
      let lastPredTime: number | null = null;

      for (const run of sortedRuns) {
        const runTime = new Date(run.timestamp).getTime();

        if (run.decision === "CYCLE_SKIPPED") {
          skipCount++;
        } else if (skipCount > 0) {
          // Found a non-skip run after skips
          if (lastPredTime) {
            gaps.push({
              afterMs: lastPredTime,
              skippedCycles: skipCount,
              actualCount: true
            });
          }
          skipCount = 0;
          lastPredTime = runTime;
        } else {
          lastPredTime = runTime;
        }
      }

      // Handle trailing skips (up to current time)
      if (skipCount > 0 && lastPredTime) {
        gaps.push({
          afterMs: lastPredTime,
          skippedCycles: skipCount,
          actualCount: true
        });
      }
    } else {
      // Fallback to time-based estimation if no model runs available
      const checkpoints = [...predTimes, Date.now()];
      for (let i = 0; i + 1 < checkpoints.length; i++) {
        const gap = checkpoints[i + 1] - checkpoints[i];
        if (gap > SKIP_THRESHOLD_MS) {
          const estimatedCycles = Math.floor(gap / CYCLE_INTERVAL_MS);
          gaps.push({
            afterMs: checkpoints[i],
            skippedCycles: estimatedCycles,
            actualCount: false
          });
        }
      }
    }

    return gaps;
  }, [predTimes, modelRuns]);

  const skipGapsByAfterMs = useMemo(() => {
    const map = new Map<number, Array<{ afterMs: number; skippedCycles: number; actualCount: boolean }>>();
    for (const gap of skipGaps) {
      const existing = map.get(gap.afterMs) ?? [];
      existing.push(gap);
      map.set(gap.afterMs, existing);
    }
    return map;
  }, [skipGaps]);

  // Pagination state for timeline events
  const [visibleEventCount, setVisibleEventCount] = useState(10);
  const paginatedGroupedEvents = useMemo(
    () => groupedEvents.slice(0, visibleEventCount),
    [groupedEvents, visibleEventCount]
  );
  const hasMoreEvents = groupedEvents.length > visibleEventCount;

  // Calculate P&L data per cycle for the chart
  const cyclePnLData = useMemo(() => {
    // Group trades by cycle timestamp (rounded to nearest hour since cycles run hourly)
    const cycleMap = new Map<number, { timestamp: number; trades: Trade[]; pnl: number }>();

    // Process all trades and group them by cycle
    chronTrades.forEach(trade => {
      const tradeTs = new Date(trade.created_at).getTime();
      // Round to nearest hour (cycle boundary)
      const cycleTs = Math.floor(tradeTs / (60 * 60 * 1000)) * (60 * 60 * 1000);

      if (!cycleMap.has(cycleTs)) {
        cycleMap.set(cycleTs, { timestamp: cycleTs, trades: [], pnl: 0 });
      }

      const cycle = cycleMap.get(cycleTs)!;
      cycle.trades.push(trade);

      // Calculate P&L for this trade
      const qty = trade.filled_shares || trade.count;
      const price = trade.price_cents / 100;
      const isSell = trade.action?.toUpperCase() === "SELL";
      // BUY = negative cash flow (spending), SELL = positive cash flow (receiving)
      const cashFlow = isSell ? qty * price : -(qty * price);
      cycle.pnl += cashFlow;
    });

    // Convert to array and sort by timestamp
    const cycles = Array.from(cycleMap.values()).sort((a, b) => a.timestamp - b.timestamp);

    // Calculate cumulative P&L
    let cumulativePnL = 0;
    const chartData = cycles.map(cycle => {
      cumulativePnL += cycle.pnl;
      return {
        time: new Date(cycle.timestamp).toLocaleTimeString('en-US', {
          hour: 'numeric',
          minute: '2-digit',
          month: 'short',
          day: 'numeric'
        }),
        timestamp: cycle.timestamp,
        cyclePnL: cycle.pnl,
        cumulativePnL: cumulativePnL,
        tradeCount: cycle.trades.length
      };
    });

    return chartData;
  }, [chronTrades]);

  // Show both cycle evaluations AND old timeline together
  return (
    <div>
      {/* P&L Chart Toggle */}
      {cyclePnLData.length > 0 && (
        <div className="mb-3">
          <button
            onClick={() => setShowPnLChart(!showPnLChart)}
            className="text-[10px] font-medium text-accent hover:text-accent/80 transition-colors"
          >
            {showPnLChart ? '▼' : '▶'} P&L per Cycle ({cyclePnLData.length} cycles)
          </button>
        </div>
      )}

      {/* P&L Chart */}
      {showPnLChart && cyclePnLData.length > 0 && (
        <div className="mb-4 p-3 bg-t-bg-secondary/30 rounded">
          <div className="text-[9px] text-txt-muted uppercase tracking-widest font-medium mb-2">
            Cumulative P&L per Trading Cycle
          </div>
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={cyclePnLData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#333" vertical={false} />
              <XAxis
                dataKey="time"
                tick={{ fontSize: 8, fill: "#888" }}
                angle={-45}
                textAnchor="end"
                height={60}
              />
              <YAxis
                tick={{ fontSize: 8, fill: "#888" }}
                width={40}
                tickFormatter={(v) => `$${v.toFixed(0)}`}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#1a1a1a",
                  border: "1px solid #333",
                  borderRadius: "4px",
                }}
                labelStyle={{ fontSize: 10, color: "#ccc" }}
                formatter={(value: any, name: string) => {
                  if (name === "Cumulative P&L") {
                    const num = Number(value);
                    return [`$${num.toFixed(2)}`, name];
                  }
                  if (name === "Cycle P&L") {
                    const num = Number(value);
                    const sign = num >= 0 ? '+' : '';
                    return [`${sign}$${num.toFixed(2)}`, name];
                  }
                  return [value, name];
                }}
              />
              <Line
                type="stepAfter"
                dataKey="cumulativePnL"
                stroke="#22c55e"
                strokeWidth={2}
                dot={{ r: 3, fill: "#22c55e" }}
                name="Cumulative P&L"
              />
              <Line
                type="monotone"
                dataKey="cyclePnL"
                stroke="#888"
                strokeWidth={1}
                strokeDasharray="5 5"
                dot={{ r: 2, fill: "#888" }}
                name="Cycle P&L"
              />
            </LineChart>
          </ResponsiveContainer>
          <div className="mt-2 text-[9px] text-txt-muted">
            Each point represents net P&L from trades executed in that cycle.
            Current total: <span className={cyclePnLData.length > 0 ? pnlCls(cyclePnLData[cyclePnLData.length - 1].cumulativePnL) : ''}>
              ${cyclePnLData.length > 0 ? cyclePnLData[cyclePnLData.length - 1].cumulativePnL.toFixed(2) : '0.00'}
            </span>
          </div>
        </div>
      )}

      <div className="relative pl-4 max-h-[400px] overflow-y-auto">
      <div className="absolute left-[5px] top-2 bottom-2 w-px bg-t-border" />

      {loadingEvaluations && (
        <div className="mb-2 text-[9px] text-txt-muted italic">Loading cycle evaluations...</div>
      )}
      {loadingRuns && !loadingEvaluations && (
        <div className="mb-2 text-[9px] text-txt-muted italic">Loading prediction history...</div>
      )}

      {/* Show "No predictions yet" if no events */}
      {!loadingEvaluations && !loadingRuns && events.length === 0 && (
        <div className="text-[10px] text-txt-muted italic py-4">No predictions yet</div>
      )}

      {/* Unified Timeline Section - Trades, Model Predictions and Cycle Evaluations */}
      {events.length > 0 && (
        <>

      {paginatedGroupedEvents.map((event, eventIdx) => {
        const currentRunTs = event.type === "prediction"
          ? new Date(event.run.timestamp).getTime()
          : event.type === "evaluation"
            ? event.evaluation.timestamp ? new Date(event.evaluation.timestamp).getTime() : null
            : event.type === "trade-group"
              ? event.items[event.items.length - 1].sortTs
              : event.item.matchedRun
                ? new Date(event.item.matchedRun.timestamp).getTime()
                : null;
        const nextRunTs = (() => {
          for (const nextEvent of paginatedGroupedEvents.slice(eventIdx + 1)) {
            if (nextEvent.type === "prediction") return new Date(nextEvent.run.timestamp).getTime();
            if (nextEvent.type === "evaluation" && nextEvent.evaluation.timestamp) return new Date(nextEvent.evaluation.timestamp).getTime();
            if (nextEvent.type === "trade-group") return nextEvent.items[nextEvent.items.length - 1].sortTs;
            if (nextEvent.type === "trade" && nextEvent.item.matchedRun) return new Date(nextEvent.item.matchedRun.timestamp).getTime();
          }
          return null;
        })();
        const showGapAfterEvent = currentRunTs != null && currentRunTs !== nextRunTs;

        if (event.type === "trade-group") {
          return (
            <div key={event.key}>
              {(() => {
                // Shared rationale: first trade in group that has reasoning/sources
                const groupReasoning = event.items.map(s => s.item.trade.prediction?.reasoning ?? s.item.matchedRun?.reasoning ?? null).find(r => r) ?? null;
                const groupSources = event.items.map(s => s.item.trade.prediction?.sources ?? s.item.matchedRun?.sources ?? []).find(s => s.length > 0) ?? [];
                const hasGroupDetail = !!(groupReasoning || groupSources.length > 0);
                const isGroupExpanded = expandedEntryId === event.key;

                // Pre-compute display values for each sub-trade
                const subRows = event.items.map((subEvent) => {
                  const { trade, idx } = subEvent.item;
                  const qty = trade.filled_shares || trade.count;
                  const isSell = trade.action?.toUpperCase() === "SELL";
                  const cost = (trade.price_cents / 100) * qty;
                  let netShares = 0, totalCost = 0, sellPnl = 0;
                  for (let i = 0; i <= idx; i++) {
                    const t = chronTrades[i];
                    const tQty = t.filled_shares || t.count;
                    const tSell = (t.action ?? "BUY").toUpperCase() === "SELL";
                    let tPrice = t.price_cents / 100;
                    if (tPrice > 1.0) tPrice /= 100;
                    const isYes = t.side.toLowerCase() === "yes";
                    if (tSell) {
                      const avgAtSell = Math.abs(netShares) > 0.001 ? Math.abs(totalCost / netShares) : 0;
                      sellPnl = (tPrice - avgAtSell) * tQty;
                      if (isYes) { netShares -= tQty; totalCost -= avgAtSell * tQty; }
                      else { netShares += tQty; totalCost += avgAtSell * tQty; }
                      if (Math.abs(netShares) < 0.001) { netShares = 0; totalCost = 0; }
                    } else {
                      if (isYes) { netShares += tQty; totalCost += tQty * tPrice; }
                      else { netShares -= tQty; totalCost -= tQty * tPrice; }
                      sellPnl = 0;
                    }
                  }
                  return { trade, qty, isSell, cost, currentSellPnl: isSell ? sellPnl : 0, cumulativeQty: Math.abs(netShares), cumulativeSide: netShares > 0 ? "YES" : netShares < 0 ? "NO" : null, totalCost };
                });

                return (
                  <div className="relative py-1.5">
                    <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-accent border-2 border-t-bg z-10" />
                    <div
                      role="button"
                      className={`rounded px-1 -mx-1 transition-colors ${hasGroupDetail ? "cursor-pointer hover:bg-t-panel-hover/40" : ""}`}
                      onClick={() => { if (!hasGroupDetail) return; setExpandedEntryId(isGroupExpanded ? null : event.key); }}
                    >
                      {subRows.map(({ trade, qty, isSell, cost, currentSellPnl, cumulativeQty, cumulativeSide, totalCost }, subIdx) => {
                        const pred = trade.prediction;
                        return (
                          <div key={subIdx} className={`flex items-start gap-3 ${subIdx > 0 ? "mt-0.5" : ""}`}>
                            <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0 text-right pr-1">
                              {subIdx === 0 ? fmtTime(trade.created_at) : "↳"}
                            </div>
                            <div className="flex-1 min-w-0 flex flex-wrap items-center gap-2.5 text-[10px] font-mono">
                              {isSell && <span className="text-[9px] px-1 py-px rounded font-bold bg-warn-dim text-warn">SELL</span>}
                              <span className={`text-[9px] px-1 py-px rounded font-bold ${trade.side.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                                {trade.side.toUpperCase()}
                              </span>
                              <span className="text-txt-primary">{isSell ? "-" : "+"}{qty} @ {trade.price_cents}c</span>
                              <span className="text-txt-muted">{isSell ? "proceeds" : "cost"}: ${cost.toFixed(2)}</span>
                              {isSell && <span className={currentSellPnl >= 0 ? "text-profit" : "text-loss"}>{currentSellPnl >= 0 ? "+" : ""}{currentSellPnl.toFixed(2)}</span>}
                              <span className="text-txt-muted">total: {cumulativeQty}{cumulativeSide ? ` ${cumulativeSide}` : ""} · ${Math.abs(totalCost).toFixed(2)}</span>
                              {pred && (
                                <>
                                  <span className="text-txt-secondary">mkt: {(pred.yes_ask * 100).toFixed(0)}c</span>
                                  <span className="text-accent">model: {(pred.p_yes * 100).toFixed(0)}%</span>
                                  <span className={pnlCls(pred.p_yes - pred.yes_ask)}>edge: {pred.p_yes - pred.yes_ask >= 0 ? "+" : ""}{((pred.p_yes - pred.yes_ask) * 100).toFixed(0)}pp</span>
                                </>
                              )}
                              <span className={`text-[9px] px-1 py-px rounded font-bold ${trade.dry_run ? "bg-warn-dim text-warn" : "bg-profit-dim text-profit"}`}>
                                {trade.dry_run ? "DRY" : "LIVE"}
                              </span>
                              {subIdx === 0 && groupSources.length > 0 && <span className="text-[9px] text-txt-muted">{groupSources.length} source{groupSources.length !== 1 ? "s" : ""}</span>}
                              {subIdx === 0 && hasGroupDetail && <span className="text-[8px] text-txt-muted ml-auto">{isGroupExpanded ? "▲" : "▼"}</span>}
                            </div>
                          </div>
                        );
                      })}
                      {isGroupExpanded && hasGroupDetail && (
                        <div className="flex items-start gap-3 pt-1">
                          <div className="w-[100px] flex-shrink-0" />
                          <div className="flex-1 min-w-0">
                            <RationalePanel reasoning={groupReasoning} sources={groupSources} />
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                );
              })()}
              {showGapAfterEvent && (skipGapsByAfterMs.get(currentRunTs!) ?? []).map((gap, i) => {
                const isOngoing = gap.afterMs === predTimes[predTimes.length - 1] || predTimes.length === 0;
                return (
                  <div key={`${event.key}-gap-${i}`} className="relative flex items-start gap-3 py-1">
                    <div className="absolute left-[-12px] top-[7px] w-[7px] h-[7px] rounded-full bg-t-border border-2 border-t-bg z-10" />
                    <div className="w-[100px] flex-shrink-0" />
                    <div className="text-[9px] text-txt-muted italic">
                      {gap.actualCount ? "" : "~"}{gap.skippedCycles} cycle{gap.skippedCycles !== 1 ? "s" : ""} skipped — price unchanged
                      {isOngoing && " (monitoring continues)"}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        }

        if (event.type === "evaluation") {
          const { evaluation } = event;
          const edge = evaluation.prediction?.edge ?? null;
          const isPositiveEdge = edge != null && edge >= 0;
          const actionType = evaluation.action?.type?.toUpperCase() ?? "HOLD";
          const isHoldAction = isHoldLikeDecision(actionType);
          const holdExplanation = isHoldAction ? buildHoldExplanation(evaluation) : null;
          const modelRationale =
            evaluation.action?.rationale
            || (isHoldAction ? evaluation.action?.reason : null)
            || (!isHoldAction ? evaluation.action?.reason : null)
            || null;
          const isExpanded = expandedEntryId === event.key;
          const hasRationale = !!(modelRationale || holdExplanation || evaluation.action?.description);

          // Extract YES/NO from action description (e.g., "BUY 10 YES" -> "YES")
          const actionDescription = evaluation.action?.description ?? "";
          const sideMatch = actionDescription.match(/\b(YES|NO)\b/i);
          const side = sideMatch ? sideMatch[1].toUpperCase() : null;
          const adjustmentMatch = actionDescription.match(/^(BUY|SELL)\s+(YES|NO)\s+(\d+)/i);
          const adjustmentVerb = adjustmentMatch ? adjustmentMatch[1].toUpperCase() : null;
          const adjustmentSide = adjustmentMatch ? adjustmentMatch[2].toUpperCase() : side;
          const adjustmentCount = adjustmentMatch ? adjustmentMatch[3] : null;
          const orderPriceLabel = formatPriceCents(evaluation.order?.price_cents);

          // Check if this is a position adjustment
          const isAdjustment = actionType === "ADJUSTMENT";
          const adjustment = (evaluation as any).adjustment;

          if (isAdjustment && adjustment) {
            const adjustmentSellSide =
              adjustment.sell.action?.description?.match(/\b(YES|NO)\b/i)?.[1]?.toUpperCase() ?? null;
            const adjustmentBuySide =
              adjustment.buy.action?.description?.match(/\b(YES|NO)\b/i)?.[1]?.toUpperCase() ?? null;

            // Render position adjustment (SELL + BUY in same timestep)
            return (
              <div key={event.key}>
                <div className="relative py-1.5">
                  {/* Purple dot for adjustments */}
                  <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-purple-500 border-2 border-t-bg z-10" />

                  <div className="flex items-start gap-3">
                    <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                      {evaluation.timestamp ? fmtTime(evaluation.timestamp) : "--:--"}
                    </div>

                    <div className="flex-1 min-w-0 overflow-hidden">
                      <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-mono">
                        <span className="text-[9px] px-1 py-px rounded font-bold bg-purple-900/30 text-purple-400">
                          ADJUST
                        </span>

                        {/* Show SELL part with side */}
                        <span className="text-loss font-semibold">SELL</span>
                        {adjustmentSellSide && (
                          <span className={sideToneClass(adjustmentSellSide)}>
                            {adjustmentSellSide} {adjustment.sell.order?.count || 0}
                          </span>
                        )}
                        {formatPriceCents(adjustment.sell.order?.price_cents) && (
                          <span className="text-txt-muted">
                            {formatPriceCents(adjustment.sell.order?.price_cents)}
                          </span>
                        )}
                        {adjustment.sell.order && (
                          <span className={`text-[9px] ${
                            adjustment.sell.order.filled === adjustment.sell.order.count ? 'text-profit-dim' : 'text-txt-muted'
                          }`}>
                            ({adjustment.sell.order.filled}/{adjustment.sell.order.count} filled)
                          </span>
                        )}

                        <span className="text-txt-muted">→</span>

                        {/* Show BUY part with side */}
                        <span className="text-profit font-semibold">BUY</span>
                        {adjustmentBuySide && (
                          <span className={sideToneClass(adjustmentBuySide)}>
                            {adjustmentBuySide} {adjustment.buy.order?.count || 0}
                          </span>
                        )}
                        {formatPriceCents(adjustment.buy.order?.price_cents) && (
                          <span className="text-txt-muted">
                            {formatPriceCents(adjustment.buy.order?.price_cents)}
                          </span>
                        )}
                        {adjustment.buy.order && (
                          <span className={`text-[9px] ${
                            adjustment.buy.order.filled === adjustment.buy.order.count ? 'text-profit-dim' : 'text-txt-muted'
                          }`}>
                            ({adjustment.buy.order.filled}/{adjustment.buy.order.count} filled)
                          </span>
                        )}

                        {(evaluation as any).resultingPosition && (
                          <span>
                            <span className="text-txt-muted">hold:</span>{" "}
                            <span className={sideToneClass((evaluation as any).resultingPosition.side, true)}>
                              {(evaluation as any).resultingPosition.quantity} {(evaluation as any).resultingPosition.side}
                            </span>
                          </span>
                        )}

                        {/* Show edge if available */}
                        {adjustment.buy.prediction?.edge != null && (
                          <span className={adjustment.buy.prediction.edge >= 0 ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                            edge: {adjustment.buy.prediction.edge >= 0 ? '+' : ''}{adjustment.buy.prediction.edge.toFixed(1)}%
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            );
          }

          return (
            <div key={event.key}>
              <div className="relative py-1.5">
                {/* Different colored dot for evaluations - use orange for HOLD, green for BUY, purple for ADJUSTMENT, red for SELL */}
                <div className={`absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full border-2 border-t-bg z-10 ${
                  isHoldAction ? "bg-orange-400" :
                  actionType === "ADJUSTMENT" ? "bg-purple-500" :
                  actionType === "BUY" ? "bg-profit" : "bg-loss"
                }`} />

                <div className="flex items-start gap-3">
                  <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                    {evaluation.timestamp ? fmtTime(evaluation.timestamp) : "--:--"}
                  </div>

                  <div className="flex-1 min-w-0 overflow-hidden">
                    <button
                      type="button"
                      className={`w-full text-left rounded px-1 -mx-1 transition-colors ${
                        hasRationale ? "cursor-pointer hover:bg-t-panel-hover/40" : ""
                      }`}
                      onClick={() => {
                        if (!hasRationale) return;
                        setExpandedEntryId(isExpanded ? null : event.key);
                      }}
                    >
                      <div className="flex flex-wrap items-center gap-2.5 text-[10px] font-mono">
                      {/* Action badge */}
                      <span className={`text-[9px] px-1 py-px rounded font-bold ${
                        isHoldAction ? "bg-yellow-900/30 text-yellow-500" :
                        actionType === "ADJUSTMENT" ? "bg-purple-900/30 text-purple-400" :
                        actionType === "BUY" ? "bg-profit-dim text-profit" :
                        "bg-loss-dim text-loss"
                      }`}>
                        {actionType === "ADJUSTMENT" ? "ADJUST" : isHoldAction ? "HOLD" : actionType}
                      </span>

                      {/* For adjustments, show the change */}
                      {actionType === "ADJUSTMENT" && evaluation.action?.description && (
                        <>
                          <span className={adjustmentVerb === "SELL" ? "text-loss font-semibold" : "text-profit font-semibold"}>
                            {adjustmentVerb ?? "BUY"}
                          </span>
                          {adjustmentSide && adjustmentCount && (
                            <span className={sideToneClass(adjustmentSide)}>
                              {adjustmentSide} {adjustmentCount}
                            </span>
                          )}
                          {orderPriceLabel && (
                            <span className="text-txt-muted">
                              {orderPriceLabel}
                            </span>
                          )}
                        </>
                      )}

                      {(evaluation as any).resultingPosition && actionType === "ADJUSTMENT" && (
                        <span>
                          <span className="text-txt-muted">hold:</span>{" "}
                          <span className={sideToneClass((evaluation as any).resultingPosition.side, true)}>
                            {(evaluation as any).resultingPosition.quantity} {(evaluation as any).resultingPosition.side}
                          </span>
                        </span>
                      )}

                      {evaluation.order && evaluation.order.count > 0 && !isHoldAction && (
                        <>
                          {actionType === "ADJUSTMENT" ? (
                            <span className={`text-[10px] font-mono ${
                              evaluation.order.filled === evaluation.order.count
                                ? 'text-profit font-semibold'
                                : (evaluation.order.filled ?? 0) > 0
                                ? 'text-accent font-semibold'
                                : 'text-txt-muted'
                            }`}>
                              ({evaluation.order.filled ?? 0}/{evaluation.order.count} filled)
                            </span>
                          ) : (
                            <>
                              <span className={sideToneClass(side)}>
                                {side ? `${side} ${evaluation.order.count} shares` : `${evaluation.order.count} shares`}
                              </span>
                              {orderPriceLabel && (
                                <span className="text-txt-muted">
                                  {orderPriceLabel}
                                </span>
                              )}
                              {(evaluation as any).resultingPosition && (
                                <span>
                                  <span className="text-txt-muted">hold:</span>{" "}
                                  <span className={sideToneClass((evaluation as any).resultingPosition.side, true)}>
                                    {(evaluation as any).resultingPosition.quantity} {(evaluation as any).resultingPosition.side}
                                  </span>
                                </span>
                              )}
                              <span className={`text-[10px] font-mono ${
                                evaluation.order.filled != null && evaluation.order.filled === evaluation.order.count
                                  ? 'text-profit font-semibold'
                                  : evaluation.order.filled != null && evaluation.order.filled > 0
                                  ? 'text-accent font-semibold'
                                  : 'text-txt-muted'
                              }`}>
                                {evaluation.order.filled != null ? (
                                  evaluation.order.filled === evaluation.order.count ? (
                                    '✓ filled'
                                  ) : (
                                    (evaluation as any).wasSuperseded ? (
                                      `cancelled`
                                    ) : (
                                      `(${evaluation.order.filled}/${evaluation.order.count} filled)`
                                    )
                                  )
                                ) : (
                                  'ordered'
                                )}
                              </span>
                              {(evaluation as any).previousUnfilledOrder && (
                                <span className="text-[9px] text-txt-muted italic">
                                  (replaces {(evaluation as any).previousUnfilledOrder.count} unfilled)
                                </span>
                              )}
                            </>
                          )}
                        </>
                      )}

                      {/* Model probability */}
                      {evaluation.prediction?.p_yes != null && (
                        <span className="text-accent">
                          model: {(evaluation.prediction.p_yes * 100).toFixed(0)}%
                        </span>
                      )}

                      {/* Market price */}
                      {evaluation.prediction?.yes_ask != null && (
                        <span className="text-txt-secondary">
                          mkt: {(evaluation.prediction.yes_ask * 100).toFixed(0)}c
                        </span>
                      )}

                      {/* Edge with color coding - only show for actionable decisions, not HOLDs */}
                      {edge != null && !isHoldAction && (
                        <span className={isPositiveEdge ? 'text-profit font-semibold' : 'text-loss font-semibold'}>
                          edge: {edge >= 0 ? '+' : ''}{edge.toFixed(1)}%
                        </span>
                      )}
                      {/* For HOLD, show why we're not trading */}
                      {edge != null && isHoldAction && (
                        <span className={`${holdEdgeToneClass()} text-[9px]`}>
                          edge: {edge >= 0 ? '+' : ''}{edge.toFixed(1)}%
                        </span>
                      )}

                      {/* Show expand indicator if has rationale */}
                      {hasRationale && (
                        <span className="text-[8px] text-txt-muted ml-auto">
                          {isExpanded ? "▲" : "▼"}
                        </span>
                      )}
                    </div>
                    </button>

                    {/* Expanded rationale section */}
                    {isExpanded && hasRationale && (
                      <div className="mt-2 p-2 bg-t-panel-hover/30 rounded text-[10px] text-txt-secondary">
                        {isHoldAction && holdExplanation && (
                          <div className="mb-2">
                            <div className="font-semibold mb-1 text-txt-primary">Why Holding:</div>
                            <div className="text-txt-muted whitespace-pre-wrap">
                              {holdExplanation}
                            </div>
                          </div>
                        )}
                        <div className="font-semibold mb-1">Model Rationale:</div>
                        <div className="text-txt-muted whitespace-pre-wrap">
                          {modelRationale || "No rationale available"}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {showGapAfterEvent && (skipGapsByAfterMs.get(currentRunTs!) ?? []).map((gap, i) => {
                const isOngoing = gap.afterMs === predTimes[predTimes.length - 1] || predTimes.length === 0;
                return (
                  <div key={`${event.key}-gap-${i}`} className="relative flex items-start gap-3 py-1">
                    <div className="absolute left-[-12px] top-[7px] w-[7px] h-[7px] rounded-full bg-t-border border-2 border-t-bg z-10" />
                    <div className="w-[100px] flex-shrink-0" />
                    <div className="text-[9px] text-txt-muted italic">
                      {gap.actualCount ? "" : "~"}{gap.skippedCycles} cycle{gap.skippedCycles !== 1 ? "s" : ""} skipped — price unchanged
                      {isOngoing && " (monitoring continues)"}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        }

        if (event.type === "prediction") {
          const { run } = event;
          const detailReasoning = run.reasoning ?? null;
          const detailSources = run.sources ?? [];
          const hasDetail = !!(detailReasoning || detailSources.length > 0);
          const isExpanded = expandedEntryId === event.key;

          return (
            <div key={event.key}>
              <div className="relative py-1.5">
                <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-purple-400 border-2 border-t-bg z-10" />

                <div className="flex items-start gap-3">
                  <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                    {fmtTime(run.timestamp)}
                  </div>

                  <div className="flex-1 min-w-0 overflow-hidden">
                    <button
                      type="button"
                      className={`w-full flex flex-wrap items-center gap-2.5 rounded px-1 -mx-1 text-[10px] font-mono text-left transition-colors ${
                        hasDetail ? "hover:bg-t-panel-hover/40" : ""
                      }`}
                      onClick={() => {
                        if (!hasDetail) return;
                        setExpandedEntryId(isExpanded ? null : event.key);
                      }}
                    >
                      <span className={`font-medium ${MODEL_COLORS[0]}`}>
                        {shortModelName(run.model_name)}
                      </span>
                      <span className="text-accent">
                        p: {run.p_yes != null ? `${(run.p_yes * 100).toFixed(1)}%` : "--"}
                      </span>
                      <span
                        className={`text-[9px] px-1 py-px rounded font-bold ${
                          run.decision === "BUY_YES"
                            ? "bg-profit-dim text-profit"
                            : run.decision === "BUY_NO"
                              ? "bg-loss-dim text-loss"
                              : run.decision === "HOLD_NOPROFIT"
                                ? "bg-yellow-900/30 text-yellow-500"
                                : "bg-t-border/30 text-txt-muted"
                        }`}
                      >
                        {run.decision}
                      </span>
                      {detailSources.length > 0 && (
                        <span className="text-[9px] text-txt-muted">
                          {detailSources.length} source{detailSources.length !== 1 ? "s" : ""}
                        </span>
                      )}
                      {hasDetail && (
                        <span className="text-[8px] text-txt-muted ml-auto">
                          {isExpanded ? "▲" : "▼"}
                        </span>
                      )}
                    </button>
                    {isExpanded && hasDetail && (
                      <div className="pt-1">
                        <RationalePanel reasoning={detailReasoning} sources={detailSources} />
                      </div>
                    )}
                  </div>
                </div>
              </div>

              {showGapAfterEvent && (skipGapsByAfterMs.get(currentRunTs!) ?? []).map((gap, i) => {
                const isOngoing = gap.afterMs === predTimes[predTimes.length - 1] || predTimes.length === 0;
                return (
                  <div key={`${event.key}-gap-${i}`} className="relative flex items-start gap-3 py-1">
                    <div className="absolute left-[-12px] top-[7px] w-[7px] h-[7px] rounded-full bg-t-border border-2 border-t-bg z-10" />
                    <div className="w-[100px] flex-shrink-0" />
                    <div className="text-[9px] text-txt-muted italic">
                      {gap.actualCount ? "" : "~"}{gap.skippedCycles} cycle{gap.skippedCycles !== 1 ? "s" : ""} skipped — price unchanged
                      {isOngoing && " (monitoring continues)"}
                    </div>
                  </div>
                );
              })}
            </div>
          );
        }

        const { trade, idx, matchedRun } = event.item;
        const qty = trade.filled_shares || trade.count;
        const isSell = trade.action?.toUpperCase() === "SELL";
        const cost = (trade.price_cents / 100) * qty;
        const pred = trade.prediction;
        const detailReasoning = pred?.reasoning ?? matchedRun?.reasoning ?? null;
        const detailSources = pred?.sources ?? matchedRun?.sources ?? [];
        const hasDetail = !!(detailReasoning || detailSources.length > 0);
        const isExpanded = expandedEntryId === event.key;

        // Replay trades up to this row using the same signed-share logic as the server.
        let netShares = 0;
        let totalCost = 0;
        let sellPnl = 0;
        for (let i = 0; i <= idx; i++) {
          const t = chronTrades[i];
          const tQty = t.filled_shares || t.count;
          const tSell = (t.action ?? "BUY").toUpperCase() === "SELL";
          let tPrice = t.price_cents / 100;
          if (tPrice > 1.0) tPrice /= 100;
          const isYes = t.side.toLowerCase() === "yes";

          if (tSell) {
            const avgAtSell = Math.abs(netShares) > 0.001 ? Math.abs(totalCost / netShares) : 0;
            sellPnl = (tPrice - avgAtSell) * tQty;
            if (isYes) {
              netShares -= tQty;
              totalCost -= avgAtSell * tQty;
            } else {
              netShares += tQty;
              totalCost += avgAtSell * tQty;
            }
            if (Math.abs(netShares) < 0.001) {
              netShares = 0;
              totalCost = 0;
            }
          } else {
            if (isYes) {
              netShares += tQty;
              totalCost += tQty * tPrice;
            } else {
              netShares -= tQty;
              totalCost -= tQty * tPrice;
            }
            sellPnl = 0;
          }
        }
        const cumulativeQty = Math.abs(netShares);
        const cumulativeSide = netShares > 0 ? "YES" : netShares < 0 ? "NO" : null;
        const currentSellPnl = isSell ? sellPnl : 0;

        return (
          <div key={event.key}>
            <div className="relative py-1.5">
              <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-accent border-2 border-t-bg z-10" />

              <div className="flex items-start gap-3">
                <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                  {fmtTime(trade.created_at)}
                </div>

                <div className="flex-1 min-w-0 overflow-hidden">
                  <button
                    type="button"
                    className={`w-full flex flex-wrap items-center gap-2.5 rounded px-1 -mx-1 text-[10px] font-mono text-left transition-colors ${
                      hasDetail ? "hover:bg-t-panel-hover/40" : ""
                    }`}
                    onClick={() => {
                      if (!hasDetail) return;
                      setExpandedEntryId(isExpanded ? null : event.key);
                    }}
                  >
                    {isSell && (
                      <span className="text-[9px] px-1 py-px rounded font-bold bg-warn-dim text-warn">SELL</span>
                    )}
                    <span
                      className={`text-[9px] px-1 py-px rounded font-bold ${
                        trade.side.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"
                      }`}
                    >
                      {trade.side.toUpperCase()}
                    </span>
                    <span className="text-txt-primary">
                      {isSell ? "-" : "+"}{qty} @ {trade.price_cents}c
                    </span>
                    <span className="text-txt-muted">
                      {isSell ? "proceeds" : "cost"}: ${cost.toFixed(2)}
                    </span>
                    {isSell && (
                      <span className={currentSellPnl >= 0 ? "text-profit" : "text-loss"}>
                        {currentSellPnl >= 0 ? "+" : ""}{currentSellPnl.toFixed(2)}
                      </span>
                    )}
                    <span className="text-txt-muted">
                      total: {cumulativeQty}{cumulativeSide ? ` ${cumulativeSide}` : ""} · ${Math.abs(totalCost).toFixed(2)}
                    </span>
                    {pred && (
                      <>
                        <span className="text-txt-secondary">mkt: {(pred.yes_ask * 100).toFixed(0)}c</span>
                        <span className="text-accent">
                          model: {(pred.p_yes * 100).toFixed(0)}%
                        </span>
                        <span className={pnlCls(pred.p_yes - pred.yes_ask)}>
                          edge: {pred.p_yes - pred.yes_ask >= 0 ? "+" : ""}
                          {((pred.p_yes - pred.yes_ask) * 100).toFixed(0)}pp
                        </span>
                      </>
                    )}
                    <span
                      className={`text-[9px] px-1 py-px rounded font-bold ${
                        trade.dry_run ? "bg-warn-dim text-warn" : "bg-profit-dim text-profit"
                      }`}
                    >
                      {trade.dry_run ? "DRY" : "LIVE"}
                    </span>
                    {detailSources.length > 0 && (
                      <span className="text-[9px] text-txt-muted">
                        {detailSources.length} source{detailSources.length !== 1 ? "s" : ""}
                      </span>
                    )}
                    {hasDetail && (
                      <span className="text-[8px] text-txt-muted ml-auto">
                        {isExpanded ? "▲" : "▼"}
                      </span>
                    )}
                  </button>
                  {isExpanded && hasDetail && (
                    <div className="pt-1">
                      <RationalePanel reasoning={detailReasoning} sources={detailSources} />
                    </div>
                  )}
                </div>
              </div>
            </div>

            {showGapAfterEvent && (skipGapsByAfterMs.get(currentRunTs!) ?? []).map((gap, i) => {
              const isOngoing = gap.afterMs === predTimes[predTimes.length - 1] || predTimes.length === 0;
              return (
                <div key={`${event.key}-gap-${i}`} className="relative flex items-start gap-3 py-1">
                  <div className="absolute left-[-12px] top-[7px] w-[7px] h-[7px] rounded-full bg-t-border border-2 border-t-bg z-10" />
                  <div className="w-[100px] flex-shrink-0" />
                  <div className="text-[9px] text-txt-muted italic">
                    {gap.actualCount ? "" : "~"}{gap.skippedCycles} cycle{gap.skippedCycles !== 1 ? "s" : ""} skipped — price unchanged
                    {isOngoing && " (monitoring continues)"}
                  </div>
                </div>
              );
            })}
          </div>
        );
      })}

          {/* Current state */}
          <div className="relative flex items-start gap-3 py-1.5 mt-1 border-t border-t-border/30">
            <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-txt-secondary border-2 border-t-bg z-10" />
            <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
              NOW
            </div>
        <div className="flex items-center gap-3 text-[10px] font-mono">
          {row.position && row.position.quantity > 0 ? (
            (() => {
              const heldBid = row.position.contract.toLowerCase() === "yes"
                ? (row.yes_bid ?? (row.no_ask != null ? 1.0 - row.no_ask : null))
                : (row.no_bid ?? (row.yes_ask != null ? 1.0 - row.yes_ask : null));
              return (
                <>
              <span className="text-txt-primary">
                Holding {row.position.quantity}{" "}
                <span className={row.position.contract.toLowerCase() === "yes" ? "text-profit" : "text-loss"}>
                  {row.position.contract.toUpperCase()}
                </span>
              </span>
              <span className="text-txt-muted">
                avg: {(row.position.avg_price * 100).toFixed(0)}c
              </span>
              {heldBid != null && (
                <span className="text-txt-muted">
                  mkt: {(heldBid * 100).toFixed(0)}c
                </span>
              )}
              <span className={`font-medium ${pnlCls(liveNetPnl(row) ?? 0)}`}>
                P&L: {liveNetPnl(row) != null ? fmtDollar(liveNetPnl(row)!) : "--"}
              </span>
                </>
              );
            })()
          ) : (
            <span className="text-txt-muted">No open position</span>
          )}
        </div>
      </div>

      {/* Show more button for timeline events */}
      {hasMoreEvents && (
        <div className="mt-3 mb-2 flex justify-center">
          <button
            onClick={() => setVisibleEventCount(prev => prev + 10)}
            className="px-3 py-1 text-[10px] font-medium text-txt-secondary bg-t-bg-secondary/50 hover:bg-t-bg-secondary hover:text-txt-primary rounded transition-colors"
          >
            Show more ({groupedEvents.length - visibleEventCount} remaining)
          </button>
        </div>
      )}

      {/* Show more button for loading more cycle evaluations from API */}
      {hasMore && (
        <div className="mt-1 flex justify-center">
          <button
            onClick={onLoadMore}
            disabled={loadingEvaluations}
            className="px-3 py-1 text-[10px] font-medium text-accent bg-accent/10 hover:bg-accent/20 rounded transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loadingEvaluations ? 'Loading...' : `Load older evaluations (${totalCycleEvaluations - cycleEvaluations.length} remaining)`}
          </button>
        </div>
      )}
        </>
      )}

      {/* Show "No activity" only if both are empty */}
      {!cycleEvaluations.length && !events.length && !loadingEvaluations && !loadingRuns && (
        <div className="text-[10px] text-txt-muted">No activity for this market</div>
      )}
      </div>
    </div>
  );
}

// ── Tab 2: Trade History ────────────────────────────────────

function TradesTab({ row }: { row: UnifiedMarketRow }) {
  if (row.trades.length === 0) {
    return <div className="text-[10px] text-txt-muted">No trades for this market</div>;
  }

  // Sort trades by created_at descending (newest first)
  const sortedTrades = [...row.trades].sort((a, b) =>
    new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  );

  // Cash flow per trade: BUY = negative (money out), SELL = positive (money back)
  const tradeRows = sortedTrades.map((trade) => {
    const qty = trade.filled_shares || trade.count;
    const price = trade.price_cents / 100;
    const isSell = trade.action?.toUpperCase() === "SELL";
    // BUY: you spend -qty×price. SELL: you receive +qty×price.
    const cashFlow = isSell ? qty * price : -(qty * price);
    return { trade, qty, price, isSell, cashFlow };
  });

  const totalCashFlow = tradeRows.reduce((sum, r) => sum + r.cashFlow, 0);

  // Current value of remaining open position
  const pos = row.position;
  const currentBid = pos
    ? pos.contract.toLowerCase() === "yes"
      ? (row.yes_bid ?? (row.no_ask != null ? 1.0 - row.no_ask : null))
      : (row.no_bid ?? (row.yes_ask != null ? 1.0 - row.yes_ask : null))
    : null;
  const openValue = pos && currentBid != null ? pos.quantity * currentBid : null;

  // Total realized + open value
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
            <th className="px-2 py-1.5 text-right font-medium">Cash</th>
            <th className="px-2 py-1.5 text-center font-medium">Status</th>
            <th className="px-2 py-1.5 text-center font-medium">Mode</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-t-border/20">
          {tradeRows.map(({ trade, qty, price, isSell, cashFlow }) => (
            <tr key={trade.id} className="hover:bg-t-panel-hover/50">
              <td className="px-2 py-1.5 font-mono text-txt-muted whitespace-nowrap">
                {fmtTime(trade.created_at)}
              </td>
              <td className="px-2 py-1.5 text-center">
                <span className="flex items-center justify-center gap-1">
                  {isSell && (
                    <span className="text-[8px] px-1 py-px rounded font-bold bg-warn-dim text-warn">SELL</span>
                  )}
                  <span className={`inline-block px-1 py-px rounded text-[8px] font-bold ${
                    trade.side.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"
                  }`}>
                    {trade.side.toUpperCase()}
                  </span>
                </span>
              </td>
              <td className="px-2 py-1.5 text-right font-mono text-txt-primary">{qty}</td>
              <td className="px-2 py-1.5 text-right font-mono text-txt-primary">{Math.round(price * 100)}c</td>
              <td className={`px-2 py-1.5 text-right font-mono font-medium ${pnlCls(cashFlow)}`}>
                {cashFlow >= 0 ? "+" : ""}{fmtDollar(cashFlow)}
              </td>
              <td className="px-2 py-1.5 text-center">
                <StatusBadge status={trade.status} />
              </td>
              <td className="px-2 py-1.5 text-center">
                <span className={`text-[8px] font-bold px-1 py-px rounded ${
                  trade.dry_run ? "bg-warn-dim text-warn" : "bg-profit-dim text-profit"
                }`}>
                  {trade.dry_run ? "DRY" : "LIVE"}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr className="border-t border-t-border/60 text-[9px] text-txt-muted">
            <td colSpan={4} className="px-2 py-1.5 font-medium">
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

// ── Tab 3: Model Predictions ────────────────────────────────

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
