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
import type { Trade, Market, Position, UnifiedMarketRow, ModelPrediction, PriceHistoryPoint } from "@/lib/api";
import { buildUnifiedMarketRows, kalshiMarketUrl, kalshiEventUrl, api } from "@/lib/api";
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
  return hasMarket ? short : `${short}*`;
}

const MODEL_COLORS = [
  "text-blue-400",
  "text-amber-400",
  "text-emerald-400",
  "text-purple-400",
  "text-rose-400",
  "text-cyan-400",
];

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
  | "return";

type FilterMode = "all" | "has_position" | "large_edge";

const PAGE_SIZE = 25;

// ── Main Component ──────────────────────────────────────────

export function UnifiedMarketTable({
  markets,
  positions,
  trades,
  scrollToMarketId,
  onScrollComplete,
}: {
  markets: Market[];
  positions: Position[];
  trades: Trade[];
  scrollToMarketId?: string | null;
  onScrollComplete?: () => void;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("edge");
  const [sortAsc, setSortAsc] = useState(false);
  const [search, setSearch] = useState("");
  const [filterMode, setFilterMode] = useState<FilterMode>("all");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [expandedMarketId, setExpandedMarketId] = useState<string | null>(null);
  const rowRefs = useRef<Map<string, HTMLTableRowElement>>(new Map());

  const rows = useMemo(
    () => buildUnifiedMarketRows(markets, positions, trades),
    [markets, positions, trades]
  );

  // Scroll-to-market from heatmap click
  useEffect(() => {
    if (!scrollToMarketId) return;
    setExpandedMarketId(scrollToMarketId);
    // Wait for render then scroll
    requestAnimationFrame(() => {
      const el = rowRefs.current.get(scrollToMarketId);
      if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
      onScrollComplete?.();
    });
  }, [scrollToMarketId, onScrollComplete]);

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  // Sort
  const sorted = useMemo(() => {
    return [...rows].sort((a, b) => {
      let diff = 0;
      switch (sortKey) {
        case "title":
          diff = a.title.localeCompare(b.title);
          break;
        case "yes_ask":
          diff = (a.yes_ask ?? 0) - (b.yes_ask ?? 0);
          break;
        case "predicted":
          diff = (a.aggregated_p_yes ?? 0) - (b.aggregated_p_yes ?? 0);
          break;
        case "edge":
          diff = Math.abs(a.edge ?? 0) - Math.abs(b.edge ?? 0);
          break;
        case "position":
          diff = (a.position?.quantity ?? 0) - (b.position?.quantity ?? 0);
          break;
        case "avg_price":
          diff = (a.position?.avg_price ?? 0) - (b.position?.avg_price ?? 0);
          break;
        case "unrealized":
          diff = (a.position?.unrealized_pnl ?? 0) - (b.position?.unrealized_pnl ?? 0);
          break;
        case "capital":
          diff = (a.position?.capital ?? 0) - (b.position?.capital ?? 0);
          break;
        case "last_trade":
          diff = (a.last_trade_time ? new Date(a.last_trade_time).getTime() : 0) -
                 (b.last_trade_time ? new Date(b.last_trade_time).getTime() : 0);
          break;
        case "return":
          diff = (a.position?.return_pct ?? 0) - (b.position?.return_pct ?? 0);
          break;
      }
      return sortAsc ? diff : -diff;
    });
  }, [rows, sortKey, sortAsc]);

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
      case "large_edge":
        result = result.filter((r) => r.edge != null && Math.abs(r.edge) >= 0.05);
        break;
    }
    return result;
  }, [sorted, search, filterMode]);

  const visible = filtered.slice(0, visibleCount);
  const hasMore = visibleCount < filtered.length;

  if (rows.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No market data available
      </div>
    );
  }

  const posCount = rows.filter((r) => r.has_position).length;
  const predCount = rows.filter((r) => r.has_prediction).length;

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
          {(["all", "has_position", "large_edge"] as FilterMode[]).map((mode) => {
            const labels: Record<FilterMode, string> = {
              all: "All",
              has_position: "Positions",
              large_edge: "Edge \u22655pp",
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
        <span className="text-[9px] text-txt-muted ml-auto font-mono">
          {filtered.length} markets · {posCount} positions · {predCount} predictions
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
              <Th k="title" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left" info="Prediction market name and ticker">Market</Th>
              <Th k="yes_ask" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Current Yes / No ask prices on Kalshi">Mkt Price</Th>
              <Th k="predicted" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Aggregated model probability (signed-sum of per-model edges + yes_ask)">Agg P</Th>
              <Th k="edge" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Edge = Agg P − Yes Ask. Positive = model thinks YES is underpriced">Edge</Th>
              <Th k="position" cur={sortKey} asc={sortAsc} onClick={handleSort} align="center" info="Current open position: side (YES/NO) and number of contracts">Position</Th>
              <Th k="avg_price" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Weighted average price paid per contract">Avg Entry</Th>
              <Th k="unrealized" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Unrealized P&L based on current market price vs entry price">P&L</Th>
              <Th k="capital" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Total capital invested: avg entry price × quantity">Investment</Th>
              <Th k="last_trade" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right" info="Timestamp of the most recent trade in this market">Last Trade</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-t-border/40">
            {visible.map((row) => {
              const isExpanded = expandedMarketId === row.market_id;
              const hasMultipleModels = row.model_predictions.length > 1;

              return (
                <MarketRow
                  key={row.market_id}
                  row={row}
                  isExpanded={isExpanded}
                  hasMultipleModels={hasMultipleModels}
                  onToggle={() => setExpandedMarketId(isExpanded ? null : row.market_id)}
                  rowRef={(el) => {
                    if (el) rowRefs.current.set(row.market_id, el);
                    else rowRefs.current.delete(row.market_id);
                  }}
                />
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Pagination */}
      {(hasMore || filtered.length > PAGE_SIZE) && (
        <div className="px-3 py-2 border-t border-t-border flex items-center justify-between text-[10px] text-txt-muted">
          <span>Showing {visible.length} of {filtered.length}</span>
          {hasMore && (
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

// ── Market Row ──────────────────────────────────────────────

function MarketRow({
  row,
  isExpanded,
  hasMultipleModels,
  onToggle,
  rowRef,
}: {
  row: UnifiedMarketRow;
  isExpanded: boolean;
  hasMultipleModels: boolean;
  onToggle: () => void;
  rowRef: (el: HTMLTableRowElement | null) => void;
}) {
  const pos = row.position;
  const isYes = pos?.contract.toLowerCase() === "yes";

  return (
    <>
      <tr
        ref={rowRef}
        className="hover:bg-t-panel-hover transition-colors cursor-pointer"
        onClick={onToggle}
      >
        {/* Market */}
        <td className="px-3 py-2 max-w-[260px]">
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
            <div className="flex items-center gap-1.5 text-[9px] font-mono text-txt-muted">
              <span>{row.ticker}</span>
              {row.event_ticker && (
                <>
                  <span className="text-t-border-light">/</span>
                  <a
                    href={kalshiEventUrl(row.event_ticker)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="hover:text-accent transition-colors"
                    onClick={(e) => e.stopPropagation()}
                  >
                    {row.event_ticker}
                  </a>
                </>
              )}
            </div>
          </div>
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
        <td className={`px-3 py-2 text-right font-mono font-medium ${row.edge != null ? pnlCls(row.edge) : "text-txt-muted"}`}>
          {row.edge != null
            ? `${row.edge >= 0 ? "+" : ""}${(row.edge * 100).toFixed(1)}pp`
            : "--"}
        </td>

        {/* Position */}
        <td className="px-3 py-2 text-center">
          {pos ? (
            <span className="flex items-center justify-center gap-1.5">
              <span
                className={`inline-block px-1.5 py-px rounded text-[9px] font-bold tracking-wider ${
                  pos.contract.toLowerCase() === "yes"
                    ? "bg-profit-dim text-profit"
                    : "bg-loss-dim text-loss"
                }`}
              >
                {pos.contract.toUpperCase()}
              </span>
              <span className="font-mono text-txt-primary">{pos.quantity}</span>
            </span>
          ) : (
            <span className="text-txt-muted">--</span>
          )}
        </td>

        {/* Entry */}
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          {pos ? `${(pos.avg_price * 100).toFixed(0)}c` : "--"}
        </td>

        {/* Unrealized P&L */}
        <td className={`px-3 py-2 text-right font-mono font-medium ${pos ? pnlCls(pos.unrealized_pnl) : "text-txt-muted"}`}>
          {pos ? fmtDollar(pos.unrealized_pnl) : "--"}
        </td>

        {/* Capital */}
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          {pos ? fmtDollar(pos.capital) : "--"}
        </td>

        {/* Last Trade */}
        <td className="px-3 py-2 text-right font-mono text-txt-muted text-[10px]">
          {row.last_trade_time ? fmtTime(row.last_trade_time) : "--"}
        </td>
      </tr>

      {/* Expanded detail panel */}
      {isExpanded && (
        <tr>
          <td colSpan={9} className="p-0">
            <ExpandedPanel row={row} />
          </td>
        </tr>
      )}
    </>
  );
}

// ── Expanded Panel with Tabs ────────────────────────────────

function ExpandedPanel({ row }: { row: UnifiedMarketRow }) {
  const [activeTab, setActiveTab] = useState<"timeline" | "trades" | "models">("timeline");

  const tabs: { key: typeof activeTab; label: string; count?: number }[] = [
    { key: "timeline", label: "Timeline", count: row.trade_count },
    { key: "trades", label: "Trades", count: row.trade_count },
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
              <span>
                {row.position.contract.toUpperCase()} {row.position.quantity} @ {(row.position.avg_price * 100).toFixed(0)}c
              </span>
              <span className={pnlCls(row.position.realized_pnl + row.position.unrealized_pnl)}>
                Total P&L: {fmtDollar(row.position.realized_pnl + row.position.unrealized_pnl)}
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
        {activeTab === "timeline" && <TimelineTab row={row} />}
        {activeTab === "trades" && <TradesTab row={row} />}
        {activeTab === "models" && <ModelsTab row={row} />}
      </div>
    </div>
  );
}

// ── Tab 1: Activity Timeline ────────────────────────────────

function TimelineTab({ row }: { row: UnifiedMarketRow }) {
  // Show trades in chronological order (oldest first)
  const chronTrades = useMemo(
    () => [...row.trades].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()),
    [row.trades]
  );

  if (chronTrades.length === 0) {
    return <div className="text-[10px] text-txt-muted">No activity for this market</div>;
  }

  return (
    <div className="relative pl-4 max-h-[400px] overflow-y-auto">
      <div className="absolute left-[5px] top-2 bottom-2 w-px bg-t-border" />

      {chronTrades.map((trade, idx) => {
        const qty = trade.filled_shares || trade.count;
        const isSell = trade.action?.toUpperCase() === "SELL";
        const cost = (trade.price_cents / 100) * qty;
        const pred = trade.prediction;

        // Cumulative position
        let cumYes = 0, cumNo = 0, cumCost = 0;
        for (let i = 0; i <= idx; i++) {
          const t = chronTrades[i];
          const tQty = t.filled_shares || t.count;
          const tSell = t.action?.toUpperCase() === "SELL";
          const tCost = (t.price_cents / 100) * tQty;
          if (t.side.toLowerCase() === "yes") cumYes += tSell ? -tQty : tQty;
          else cumNo += tSell ? -tQty : tQty;
          cumCost += tSell ? -tCost : tCost;
        }
        const cumulativeQty = Math.max(cumYes, cumNo, 0);

        return (
          <div key={trade.id} className="relative flex items-start gap-3 py-1.5">
            <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-accent border-2 border-t-bg z-10" />

            <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
              {fmtTime(trade.created_at)}
            </div>

            <div className="flex flex-wrap items-center gap-2.5 flex-1 text-[10px] font-mono">
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
              <span className="text-txt-muted">
                total: {cumulativeQty} · ${Math.max(0, cumCost).toFixed(2)}
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
            </div>
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
              {row.yes_ask != null && (
                <span className="text-txt-muted">
                  mkt: {(row.yes_ask * 100).toFixed(0)}c
                </span>
              )}
              <span className={`font-medium ${pnlCls(row.position.unrealized_pnl)}`}>
                P&L: {fmtDollar(row.position.unrealized_pnl)}
              </span>
            </>
          ) : (
            <span className="text-txt-muted">No open position</span>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Tab 2: Trade History ────────────────────────────────────

function TradesTab({ row }: { row: UnifiedMarketRow }) {
  if (row.trades.length === 0) {
    return <div className="text-[10px] text-txt-muted">No trades for this market</div>;
  }

  return (
    <div className="overflow-x-auto max-h-[300px] overflow-y-auto">
      <table className="w-full text-[10px]">
        <thead>
          <tr className="text-txt-muted text-[9px] uppercase tracking-widest border-b border-t-border/40">
            <th className="px-2 py-1.5 text-left font-medium">Time</th>
            <th className="px-2 py-1.5 text-center font-medium">Side</th>
            <th className="px-2 py-1.5 text-right font-medium">Qty</th>
            <th className="px-2 py-1.5 text-right font-medium">Price</th>
            <th className="px-2 py-1.5 text-right font-medium">Cost</th>
            <th className="px-2 py-1.5 text-right font-medium">P&L</th>
            <th className="px-2 py-1.5 text-center font-medium">Status</th>
            <th className="px-2 py-1.5 text-center font-medium">Mode</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-t-border/20">
          {row.trades.map((trade) => {
            const cost = (trade.price_cents / 100) * trade.count;
            const qty = trade.filled_shares || trade.count;
            // Compute unrealized P&L for this trade
            const currentPrice =
              trade.side.toLowerCase() === "yes" ? row.yes_ask : row.no_ask;
            const pnl =
              currentPrice != null
                ? ((currentPrice * 100 - trade.price_cents) / 100) * qty
                : null;
            const isSell = trade.action?.toUpperCase() === "SELL";

            return (
              <tr key={trade.id} className="hover:bg-t-panel-hover/50">
                <td className="px-2 py-1.5 font-mono text-txt-muted whitespace-nowrap">
                  {fmtTime(trade.created_at)}
                </td>
                <td className="px-2 py-1.5 text-center">
                  <span className="flex items-center justify-center gap-1">
                    {isSell && (
                      <span className="text-[8px] px-1 py-px rounded font-bold bg-warn-dim text-warn">SELL</span>
                    )}
                    <span
                      className={`inline-block px-1 py-px rounded text-[8px] font-bold ${
                        trade.side.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"
                      }`}
                    >
                      {trade.side.toUpperCase()}
                    </span>
                  </span>
                </td>
                <td className="px-2 py-1.5 text-right font-mono text-txt-primary">{qty}</td>
                <td className="px-2 py-1.5 text-right font-mono text-txt-primary">{trade.price_cents}c</td>
                <td className="px-2 py-1.5 text-right font-mono text-txt-primary">${cost.toFixed(2)}</td>
                <td className={`px-2 py-1.5 text-right font-mono font-medium ${pnl != null ? pnlCls(pnl) : "text-txt-muted"}`}>
                  {pnl != null ? fmtDollar(pnl) : "--"}
                </td>
                <td className="px-2 py-1.5 text-center">
                  <StatusBadge status={trade.status} />
                </td>
                <td className="px-2 py-1.5 text-center">
                  <span
                    className={`text-[8px] font-bold px-1 py-px rounded ${
                      trade.dry_run ? "bg-warn-dim text-warn" : "bg-profit-dim text-profit"
                    }`}
                  >
                    {trade.dry_run ? "DRY" : "LIVE"}
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
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

function ModelsTab({ row }: { row: UnifiedMarketRow }) {
  const [priceHistory, setPriceHistory] = useState<PriceHistoryPoint[] | null>(null);
  const [loading, setLoading] = useState(false);
  const fetched = useRef(false);

  useEffect(() => {
    if (fetched.current) return;
    fetched.current = true;
    setLoading(true);
    api.getPriceHistory(row.market_id).then((data) => {
      setPriceHistory(Array.isArray(data) ? data : []);
      setLoading(false);
    });
  }, [row.market_id]);

  const preds = row.model_predictions;
  const yesAsk = row.yes_ask;

  return (
    <div className="space-y-4">
      {/* Per-model breakdown */}
      {preds.length > 0 ? (
        <div>
          <div className="flex items-center gap-4 text-[9px] text-txt-muted uppercase tracking-wider mb-2">
            <span>Per-Model Predictions</span>
            {yesAsk != null && (
              <span className="normal-case tracking-normal">
                yes_ask: <span className="text-txt-primary font-mono">{(yesAsk * 100).toFixed(1)}c</span>
              </span>
            )}
          </div>
          <div className="grid gap-1">
            {preds.map((pred, i) => {
              const modelEdge = pred.p_yes != null && yesAsk != null ? pred.p_yes - yesAsk : null;
              return (
                <div key={pred.model_name} className="flex items-center gap-3 text-[10px] font-mono">
                  <span className={`w-28 truncate font-medium ${MODEL_COLORS[i % MODEL_COLORS.length]}`}>
                    {shortModelName(pred.model_name)}
                  </span>
                  <span className="text-txt-primary w-14 text-right">
                    p: {pred.p_yes != null ? `${(pred.p_yes * 100).toFixed(1)}%` : "--"}
                  </span>
                  <span className={`w-20 text-right ${modelEdge != null ? pnlCls(modelEdge) : "text-txt-muted"}`}>
                    {modelEdge != null
                      ? `edge: ${modelEdge >= 0 ? "+" : ""}${(modelEdge * 100).toFixed(1)}pp`
                      : "--"}
                  </span>
                  <span
                    className={`px-1.5 py-px rounded text-[8px] font-bold ${
                      pred.decision === "BUY_YES"
                        ? "bg-profit-dim text-profit"
                        : pred.decision === "BUY_NO"
                          ? "bg-loss-dim text-loss"
                          : "bg-t-border/30 text-txt-muted"
                    }`}
                  >
                    {pred.decision}
                  </span>
                </div>
              );
            })}
          </div>
          {/* Aggregated summary */}
          <div className="flex items-center gap-3 text-[10px] font-mono pt-1 mt-1 border-t border-t-border/30">
            <span className="w-28 font-medium text-accent">agg (sum)</span>
            <span className="text-accent w-14 text-right">
              p: {row.aggregated_p_yes != null ? `${(row.aggregated_p_yes * 100).toFixed(1)}%` : "--"}
            </span>
            <span className={`w-20 text-right font-medium ${row.edge != null ? pnlCls(row.edge) : "text-txt-muted"}`}>
              {row.edge != null
                ? `edge: ${row.edge >= 0 ? "+" : ""}${(row.edge * 100).toFixed(1)}pp`
                : "--"}
            </span>
          </div>
        </div>
      ) : (
        <div className="text-[10px] text-txt-muted">No model predictions available</div>
      )}

      {/* Price history chart */}
      {loading && (
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

  return (
    <div>
      <div className="text-[9px] text-txt-muted uppercase tracking-widest mb-1 font-medium">
        Market Price vs Model Probability
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
  cur,
  asc,
  onClick,
  align,
  info,
}: {
  children: React.ReactNode;
  k: SortKey;
  cur: SortKey;
  asc: boolean;
  onClick: (k: SortKey) => void;
  align: "left" | "center" | "right";
  info?: string;
}) {
  const active = k === cur;
  const cls = align === "left" ? "text-left" : align === "right" ? "text-right" : "text-center";
  return (
    <th
      className={`px-3 py-2 font-medium cursor-pointer select-none hover:text-txt-primary transition-colors ${cls} ${active ? "text-txt-primary" : ""}`}
      onClick={() => onClick(k)}
    >
      {children}
      {info && <InfoButton text={info} />}
      {active && (
        <span className="ml-0.5 text-accent text-[8px]">
          {asc ? "\u25B2" : "\u25BC"}
        </span>
      )}
    </th>
  );
}
