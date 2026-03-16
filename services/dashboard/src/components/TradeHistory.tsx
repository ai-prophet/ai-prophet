"use client";

import { useState, useMemo } from "react";
import type { Trade, Market } from "@/lib/api";
import { kalshiMarketUrl, kalshiEventUrl } from "@/lib/api";
import { pnlCls } from "@/lib/utils";

function tradePnl(trade: Trade, market: Market | null): number | null {
  if (!market) return null;
  const entryCents = trade.price_cents;
  const qty = trade.filled_shares || trade.count;
  if (qty === 0) return null;
  const currentPrice =
    trade.side.toLowerCase() === "yes"
      ? market.yes_ask
      : market.no_ask;
  if (currentPrice == null) return null;
  const currentCents = currentPrice * 100;
  return ((currentCents - entryCents) / 100) * qty;
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
    <span
      className={`inline-block px-1.5 py-px rounded text-[9px] font-medium ${s[status] ?? "bg-t-border text-txt-muted"}`}
    >
      {status}
    </span>
  );
}

type SortKey = "time" | "market" | "side" | "qty" | "price" | "cost" | "pnl" | "status";

export function TradeHistory({
  trades,
  markets,
}: {
  trades: Trade[];
  markets: Market[];
}) {
  const [sortKey, setSortKey] = useState<SortKey>("time");
  const [sortAsc, setSortAsc] = useState(false);
  const [marketFilter, setMarketFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const marketMap = useMemo(
    () => new Map(markets.map((m) => [m.ticker, m])),
    [markets]
  );

  const uniqueStatuses = useMemo(() => {
    const set = new Set<string>();
    trades.forEach((t) => set.add(t.status));
    return Array.from(set).sort();
  }, [trades]);

  const filtered = useMemo(() => {
    let r = trades;
    if (marketFilter) {
      const q = marketFilter.toLowerCase();
      r = r.filter(
        (t) =>
          (t.market_title ?? t.ticker)?.toLowerCase().includes(q)
      );
    }
    if (statusFilter !== "all") {
      r = r.filter((t) => t.status === statusFilter);
    }
    return r;
  }, [trades, marketFilter, statusFilter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let d = 0;
      switch (sortKey) {
        case "time":
          d = new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
          break;
        case "market":
          d = (a.market_title ?? a.ticker).localeCompare(b.market_title ?? b.ticker);
          break;
        case "side":
          d = a.side.localeCompare(b.side);
          break;
        case "qty":
          d = a.count - b.count;
          break;
        case "price":
          d = a.price_cents - b.price_cents;
          break;
        case "cost":
          d = a.price_cents * a.count - b.price_cents * b.count;
          break;
        case "pnl": {
          const mktA = marketMap.get(a.ticker);
          const mktB = marketMap.get(b.ticker);
          d = (tradePnl(a, mktA ?? null) ?? 0) - (tradePnl(b, mktB ?? null) ?? 0);
          break;
        }
        case "status":
          d = a.status.localeCompare(b.status);
          break;
      }
      return sortAsc ? d : -d;
    });
  }, [filtered, sortKey, sortAsc]);

  const handleSort = (k: SortKey) => {
    if (sortKey === k) setSortAsc(!sortAsc);
    else {
      setSortKey(k);
      setSortAsc(false);
    }
  };

  if (trades.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No trades yet
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Filters */}
      <div className="px-3 py-1.5 border-b border-t-border flex flex-wrap gap-2 items-center">
        <input
          type="text"
          placeholder="Filter market..."
          value={marketFilter}
          onChange={(e) => setMarketFilter(e.target.value)}
          className="bg-t-bg border border-t-border rounded px-2 py-1 text-[10px] text-txt-primary placeholder-txt-muted focus:border-accent/50 focus:outline-none w-40 font-mono"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-t-bg border border-t-border rounded px-2 py-1 text-[10px] text-txt-primary focus:border-accent/50 focus:outline-none font-mono"
        >
          <option value="all">All</option>
          {uniqueStatuses.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <span className="text-[9px] text-txt-muted ml-auto font-mono">
          {sorted.length}/{trades.length}
        </span>
      </div>

      <div className="overflow-x-auto max-h-[380px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-t-panel z-10">
            <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
              <ThS k="time" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left">Time</ThS>
              <ThS k="market" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left">Market</ThS>
              <ThS k="side" cur={sortKey} asc={sortAsc} onClick={handleSort} align="center">Side</ThS>
              <ThS k="qty" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Qty</ThS>
              <ThS k="price" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Price</ThS>
              <th className="text-right px-3 py-2 font-medium">Cost</th>
              <ThS k="pnl" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">P&L</ThS>
              <ThS k="status" cur={sortKey} asc={sortAsc} onClick={handleSort} align="center">Status</ThS>
              <th className="text-left px-3 py-2 font-medium">Model</th>
              <th className="text-center px-3 py-2 font-medium">Mode</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-t-border/30">
            {sorted.map((trade) => {
              const cost = (trade.price_cents / 100) * trade.count;
              const isExpanded = expandedId === trade.id;
              const mkt = marketMap.get(trade.ticker) ?? null;
              const pnl = tradePnl(trade, mkt);

              return (
                <TradeRow
                  key={trade.id}
                  trade={trade}
                  cost={cost}
                  pnl={pnl}
                  isExpanded={isExpanded}
                  market={mkt}
                  onToggle={() =>
                    setExpandedId(isExpanded ? null : trade.id)
                  }
                />
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TradeRow({
  trade,
  cost,
  pnl,
  isExpanded,
  market,
  onToggle,
}: {
  trade: Trade;
  cost: number;
  pnl: number | null;
  isExpanded: boolean;
  market: Market | null;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className="hover:bg-t-panel-hover transition-colors cursor-pointer"
        onClick={onToggle}
      >
        <td className="px-3 py-2 text-txt-secondary text-[10px] whitespace-nowrap font-mono">
          {new Date(trade.created_at).toLocaleString("en-US", {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </td>
        <td className="px-3 py-2 max-w-[180px]">
          <div className="text-txt-primary text-xs font-medium truncate">
            {trade.market_title ?? trade.ticker}
          </div>
          {trade.market_title && (
            <div className="text-[9px] text-txt-muted font-mono">{trade.ticker}</div>
          )}
        </td>
        <td className="px-3 py-2 text-center">
          <span
            className={`inline-block px-1.5 py-px rounded text-[9px] font-bold tracking-wider ${
              trade.side.toLowerCase() === "yes"
                ? "bg-profit-dim text-profit"
                : "bg-loss-dim text-loss"
            }`}
          >
            {trade.side.toUpperCase()}
          </span>
        </td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          {trade.count}
        </td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          {trade.price_cents}c
        </td>
        <td className="px-3 py-2 text-right font-mono text-txt-primary">
          ${cost.toFixed(2)}
        </td>
        <td className={`px-3 py-2 text-right font-mono font-medium ${pnl != null ? (pnl === 0 ? "text-txt-muted" : pnlCls(pnl)) : "text-txt-muted"}`}>
          {pnl != null
            ? pnl === 0
              ? "FLAT"
              : `${pnl >= 0 ? "+" : "-"}$${Math.abs(pnl).toFixed(2)}`
            : "--"}
        </td>
        <td className="px-3 py-2 text-center">
          <StatusBadge status={trade.status} />
        </td>
        <td className="px-3 py-2 text-txt-muted text-[9px] truncate max-w-[80px] font-mono">
          {trade.prediction?.source ?? "--"}
        </td>
        <td className="px-3 py-2 text-center">
          <span
            className={`text-[9px] font-bold px-1.5 py-px rounded ${
              trade.dry_run
                ? "bg-warn-dim text-warn"
                : "bg-profit-dim text-profit"
            }`}
          >
            {trade.dry_run ? "DRY" : "LIVE"}
          </span>
        </td>
      </tr>

      {/* Expandable detail row */}
      {isExpanded && (
        <tr className="bg-t-panel-alt">
          <td colSpan={10} className="px-4 py-3">
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[10px]">
              {/* Market info */}
              <div>
                <div className="text-txt-muted uppercase tracking-widest mb-1 text-[8px] font-medium">
                  Market
                </div>
                <div className="space-y-0.5 font-mono">
                  <div className="text-txt-primary">
                    {trade.market_title ?? trade.ticker}
                  </div>
                  <div className="text-txt-muted">Ticker: {trade.ticker}</div>
                  {market && (
                    <a
                      href={kalshiMarketUrl(market.event_ticker)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-accent hover:underline"
                    >
                      View on Kalshi
                    </a>
                  )}
                </div>
              </div>

              {/* Event info */}
              <div>
                <div className="text-txt-muted uppercase tracking-widest mb-1 text-[8px] font-medium">
                  Event
                </div>
                <div className="space-y-0.5 font-mono">
                  {market ? (
                    <>
                      <div className="text-txt-primary">
                        {market.event_ticker}
                      </div>
                      <div className="text-txt-muted">
                        Cat: {market.category ?? "N/A"}
                      </div>
                      <a
                        href={kalshiEventUrl(market.event_ticker)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-accent hover:underline"
                      >
                        View Event
                      </a>
                    </>
                  ) : (
                    <div className="text-txt-muted">No event data</div>
                  )}
                </div>
              </div>

              {/* Market data */}
              <div>
                <div className="text-txt-muted uppercase tracking-widest mb-1 text-[8px] font-medium">
                  Market Data
                </div>
                <div className="space-y-0.5 font-mono">
                  {market ? (
                    <>
                      <div className="text-txt-secondary">
                        Yes: {market.yes_ask != null ? `${(market.yes_ask * 100).toFixed(0)}c` : "--"}
                        {" / "}
                        No: {market.no_ask != null ? `${(market.no_ask * 100).toFixed(0)}c` : "--"}
                      </div>
                      <div className="text-txt-muted">
                        Vol 24h: {market.volume_24h?.toLocaleString() ?? "--"}
                      </div>
                      <div className="text-txt-muted">
                        Expires:{" "}
                        {market.expiration
                          ? new Date(market.expiration).toLocaleDateString()
                          : "--"}
                      </div>
                    </>
                  ) : (
                    <div className="text-txt-muted">No market data</div>
                  )}
                </div>
              </div>

              {/* Prediction */}
              <div>
                <div className="text-txt-muted uppercase tracking-widest mb-1 text-[8px] font-medium">
                  Model Prediction
                </div>
                <div className="space-y-0.5 font-mono">
                  {trade.prediction ? (
                    <>
                      <div className="text-txt-secondary">
                        P(Yes): {(trade.prediction.p_yes * 100).toFixed(1)}%
                      </div>
                      <div className="text-txt-muted">
                        Mkt Yes: {(trade.prediction.yes_ask * 100).toFixed(0)}c
                      </div>
                      <div className="text-txt-muted">
                        Source: {trade.prediction.source}
                      </div>
                    </>
                  ) : (
                    <div className="text-txt-muted">No prediction</div>
                  )}
                </div>
              </div>
            </div>

            {/* Order details */}
            <div className="mt-2 pt-2 border-t border-t-border/40 flex flex-wrap gap-4 text-[9px] font-mono text-txt-muted">
              <span>Order: {trade.order_id}</span>
              {trade.exchange_order_id && (
                <span>Exchange: {trade.exchange_order_id}</span>
              )}
              <span>
                Fill: {trade.filled_shares} @ {trade.fill_price}
              </span>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function ThS({
  children,
  k,
  cur,
  asc,
  onClick,
  align,
}: {
  children: React.ReactNode;
  k: SortKey;
  cur: SortKey;
  asc: boolean;
  onClick: (k: SortKey) => void;
  align: "left" | "center" | "right";
}) {
  const active = k === cur;
  const cls =
    align === "left"
      ? "text-left"
      : align === "right"
        ? "text-right"
        : "text-center";
  return (
    <th
      className={`px-3 py-2 font-medium cursor-pointer select-none hover:text-txt-primary transition-colors ${cls} ${active ? "text-txt-primary" : ""}`}
      onClick={() => onClick(k)}
    >
      {children}
      {active && (
        <span className="ml-0.5 text-accent text-[8px]">
          {asc ? "\u25B2" : "\u25BC"}
        </span>
      )}
    </th>
  );
}
