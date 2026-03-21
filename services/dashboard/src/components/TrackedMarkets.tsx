"use client";

import { useState, useMemo } from "react";
import type { Market } from "@/lib/api";
import { kalshiMarketUrl, kalshiEventUrl } from "@/lib/api";

type SortKey = "title" | "yes" | "no" | "volume" | "expiry" | "category";

export function TrackedMarkets({ markets }: { markets: Market[] }) {
  const [sortKey, setSortKey] = useState<SortKey>("volume");
  const [sortAsc, setSortAsc] = useState(false);
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    if (!filter) return markets;
    const q = filter.toLowerCase();
    return markets.filter(
      (m) =>
        m.title.toLowerCase().includes(q) ||
        m.ticker.toLowerCase().includes(q) ||
        m.event_ticker.toLowerCase().includes(q) ||
        (m.category ?? "").toLowerCase().includes(q)
    );
  }, [markets, filter]);

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let d = 0;
      switch (sortKey) {
        case "title":
          d = a.title.localeCompare(b.title);
          break;
        case "yes":
          d = (a.yes_ask ?? 0) - (b.yes_ask ?? 0);
          break;
        case "no":
          d = (a.no_ask ?? 0) - (b.no_ask ?? 0);
          break;
        case "volume":
          d = (a.volume_24h ?? 0) - (b.volume_24h ?? 0);
          break;
        case "expiry":
          d =
            new Date(a.expiration ?? 0).getTime() -
            new Date(b.expiration ?? 0).getTime();
          break;
        case "category":
          d = (a.category ?? "").localeCompare(b.category ?? "");
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

  if (markets.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No markets being tracked
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Filter bar */}
      <div className="px-3 py-1.5 border-b border-t-border flex items-center gap-2">
        <input
          type="text"
          placeholder="Filter markets..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="bg-t-bg border border-t-border rounded px-2 py-1 text-[10px] text-txt-primary placeholder-txt-muted focus:border-accent/50 focus:outline-none w-48 font-mono"
        />
        <span className="text-[9px] text-txt-muted ml-auto font-mono">
          {sorted.length}/{markets.length}
        </span>
      </div>

      <div className="overflow-x-auto max-h-[380px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-t-panel z-10">
            <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
              <Th k="title" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left">Market</Th>
              <th className="text-left px-3 py-2 font-medium">Event</th>
              <Th k="category" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left">Category</Th>
              <Th k="yes" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Yes</Th>
              <Th k="no" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">No</Th>
              <Th k="volume" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Vol 24h</Th>
              <Th k="expiry" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Expires</Th>
              <th className="text-left px-3 py-2 font-medium">Prediction</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-t-border/30">
            {sorted.map((mkt) => (
              <tr
                key={mkt.id}
                className="hover:bg-t-panel-hover transition-colors"
              >
                {/* Market title + ticker */}
                <td className="px-3 py-2 max-w-[240px]">
                  <a
                    href={kalshiMarketUrl(mkt.event_ticker)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-txt-primary hover:text-accent transition-colors font-medium truncate text-xs block"
                  >
                    {mkt.title}
                  </a>
                  <div className="text-[9px] text-txt-muted font-mono">
                    {mkt.ticker}
                  </div>
                </td>

                {/* Event */}
                <td className="px-3 py-2">
                  <a
                    href={kalshiEventUrl(mkt.event_ticker)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[10px] text-accent hover:underline font-mono"
                  >
                    {mkt.event_ticker}
                  </a>
                </td>

                {/* Category */}
                <td className="px-3 py-2 text-[10px] text-txt-muted font-mono">
                  {mkt.category ?? "--"}
                </td>

                {/* Yes ask */}
                <td className="px-3 py-2 text-right font-mono text-profit">
                  {mkt.yes_ask != null
                    ? `${(mkt.yes_ask * 100).toFixed(0)}c`
                    : "--"}
                </td>

                {/* No ask */}
                <td className="px-3 py-2 text-right font-mono text-loss">
                  {mkt.no_ask != null
                    ? `${(mkt.no_ask * 100).toFixed(0)}c`
                    : "--"}
                </td>

                {/* Volume */}
                <td className="px-3 py-2 text-right font-mono text-txt-primary">
                  {mkt.volume_24h != null
                    ? mkt.volume_24h.toLocaleString()
                    : "--"}
                </td>

                {/* Expiration */}
                <td className="px-3 py-2 text-right text-[10px] text-txt-muted font-mono whitespace-nowrap">
                  {mkt.expiration
                    ? new Date(mkt.expiration).toLocaleDateString("en-US", {
                        month: "short",
                        day: "numeric",
                      })
                    : "--"}
                </td>

                {/* Model prediction */}
                <td className="px-3 py-2">
                  {mkt.model_prediction ? (
                    <div className="space-y-0.5">
                      <span
                        className={`inline-block px-1.5 py-px rounded text-[9px] font-bold ${
                          mkt.model_prediction.decision === "HOLD_NOPROFIT"
                            ? "bg-yellow-900/30 text-yellow-500"
                            : mkt.model_prediction.decision.includes("YES")
                              ? "bg-profit-dim text-profit"
                              : mkt.model_prediction.decision.includes("NO")
                                ? "bg-loss-dim text-loss"
                                : "bg-accent-dim text-accent"
                        }`}
                      >
                        {mkt.model_prediction.decision}
                      </span>
                      {mkt.model_prediction.p_yes != null && (
                        <div className="text-[9px] text-txt-muted font-mono">
                          P(Yes): {(mkt.model_prediction.p_yes * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  ) : (
                    <span className="text-[9px] text-txt-muted">--</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Th({
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
