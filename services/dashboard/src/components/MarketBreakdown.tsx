"use client";

import { useState, useMemo } from "react";
import type { Position, Trade } from "@/lib/api";
import { groupByMarket } from "@/lib/api";
import { pnlCls } from "@/lib/utils";

type SortField = "pnl" | "capital" | "trades" | "size";

export function MarketBreakdown({
  positions,
  trades,
}: {
  positions: Position[];
  trades: Trade[];
}) {
  const [sortField, setSortField] = useState<SortField>("pnl");

  const markets = useMemo(
    () => groupByMarket(positions, trades),
    [positions, trades]
  );

  const sorted = useMemo(() => {
    return [...markets].sort((a, b) => {
      switch (sortField) {
        case "pnl":
          return b.pnl - a.pnl;
        case "capital":
          return b.capitalDeployed - a.capitalDeployed;
        case "trades":
          return b.tradeCount - a.tradeCount;
        case "size":
          return b.openSize - a.openSize;
      }
    });
  }, [markets, sortField]);

  if (markets.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No market data
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Sort pills */}
      <div className="px-3 py-1.5 border-b border-t-border flex items-center gap-1">
        <span className="text-[9px] text-txt-muted mr-1 uppercase tracking-widest">Sort</span>
        {(
          [
            ["pnl", "P&L"],
            ["capital", "Capital"],
            ["size", "Size"],
          ] as [SortField, string][]
        ).map(([f, l]) => (
          <button
            key={f}
            onClick={() => setSortField(f)}
            className={`text-[9px] px-1.5 py-0.5 rounded transition-colors font-mono ${
              sortField === f
                ? "bg-accent-dim text-accent"
                : "text-txt-muted hover:text-txt-primary"
            }`}
          >
            {l}
          </button>
        ))}
      </div>

      <div className="max-h-[210px] overflow-y-auto divide-y divide-t-border/30">
        {sorted.map((mkt) => {
          const retPct =
            mkt.capitalDeployed > 0
              ? (mkt.pnl / mkt.capitalDeployed) * 100
              : 0;

          return (
            <div
              key={mkt.marketId}
              className="px-3 py-2 hover:bg-t-panel-hover transition-colors"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-xs font-medium text-txt-primary truncate">
                    {mkt.title}
                  </p>
                  <div className="flex gap-3 mt-0.5 text-[9px] text-txt-muted font-mono">
                    <span>${mkt.capitalDeployed.toFixed(2)} deployed</span>
                    <span>{mkt.openSize} contracts</span>
                  </div>
                </div>
                <div className="text-right flex-shrink-0">
                  <p className={`text-xs font-semibold font-mono ${pnlCls(mkt.pnl)}`}>
                    {mkt.pnl >= 0 ? "+" : ""}${mkt.pnl.toFixed(2)}
                  </p>
                  <p className={`text-[9px] font-mono ${pnlCls(retPct)}`}>
                    {retPct >= 0 ? "+" : ""}{retPct.toFixed(1)}%
                  </p>
                </div>
              </div>
              {/* Bar */}
              <div className="mt-1 h-0.5 bg-t-bg rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full ${mkt.pnl >= 0 ? "bg-profit/60" : "bg-loss/60"}`}
                  style={{
                    width: `${Math.min(Math.abs(retPct), 100)}%`,
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
