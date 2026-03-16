"use client";

import { useMemo } from "react";
import type { Position, Market } from "@/lib/api";
import { pnlCls, fmtDollar, fmtPct } from "@/lib/utils";

function getMarketForPosition(
  pos: Position,
  byId: Map<string, Market>,
  byTicker: Map<string, Market>
): Market | undefined {
  return byId.get(pos.market_id) ?? (pos.ticker ? byTicker.get(pos.ticker) : undefined);
}

interface CellData {
  id: number;
  marketId: string;
  title: string;
  quantity: number;
  capital: number;
  pnl: number;
  returnPct: number;
  contract: string;
}

function pnlBgColor(pnl: number, maxAbsPnl: number): string {
  if (maxAbsPnl === 0) return "bg-t-panel";
  const intensity = Math.min(Math.abs(pnl) / maxAbsPnl, 1);
  if (pnl > 0) {
    // Green gradient
    const alpha = (intensity * 0.4 + 0.05).toFixed(2);
    return "";
  }
  if (pnl < 0) {
    const alpha = (intensity * 0.4 + 0.05).toFixed(2);
    return "";
  }
  return "";
}

function pnlBgStyle(pnl: number, maxAbsPnl: number): React.CSSProperties {
  if (maxAbsPnl === 0) return {};
  const intensity = Math.min(Math.abs(pnl) / maxAbsPnl, 1);
  const alpha = (intensity * 0.35 + 0.05).toFixed(2);
  if (pnl > 0) {
    return { backgroundColor: `rgba(0, 210, 106, ${alpha})` };
  }
  if (pnl < 0) {
    return { backgroundColor: `rgba(255, 71, 87, ${alpha})` };
  }
  return { backgroundColor: "rgba(90, 101, 119, 0.1)" };
}

function sizeClass(capital: number, maxCapital: number): string {
  if (maxCapital === 0) return "col-span-1 row-span-1";
  const ratio = capital / maxCapital;
  if (ratio > 0.5) return "col-span-2 row-span-2";
  if (ratio > 0.25) return "col-span-2 row-span-1";
  return "col-span-1 row-span-1";
}

export function PositionHeatmap({
  positions,
  markets,
  onCellClick,
}: {
  positions: Position[];
  markets: Market[];
  onCellClick?: (marketId: string) => void;
}) {
  const { byId, byTicker } = useMemo(() => {
    const byId = new Map(markets.map((m) => [m.market_id, m]));
    const byTicker = new Map(markets.map((m) => [m.ticker, m]));
    return { byId, byTicker };
  }, [markets]);

  const cells: CellData[] = useMemo(() => {
    return positions
      .map((pos) => {
        const capital = pos.avg_price * pos.quantity;
        const pnl = pos.unrealized_pnl;
        const returnPct = capital > 0 ? (pnl / capital) * 100 : 0;

        return {
          id: pos.id,
          marketId: pos.market_id,
          title: pos.market_title ?? pos.ticker ?? pos.market_id,
          quantity: pos.quantity,
          capital,
          pnl,
          returnPct,
          contract: pos.contract,
        };
      })
      .sort((a, b) => b.capital - a.capital);
  }, [positions]);

  if (positions.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No positions to display
      </div>
    );
  }

  const maxCapital = Math.max(...cells.map((c) => c.capital), 0.01);
  const maxAbsPnl = Math.max(...cells.map((c) => Math.abs(c.pnl)), 0.01);

  return (
    <div className="bg-t-panel border border-t-border rounded">
      <div className="px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Position Heatmap
        </h3>
      </div>

      <div className="p-3">
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-1.5 auto-rows-auto">
          {cells.map((cell) => (
            <div
              key={cell.id}
              className={`${sizeClass(cell.capital, maxCapital)} rounded border border-t-border/60 p-2 flex flex-col justify-between min-h-[72px] transition-all hover:border-t-border ${onCellClick ? "cursor-pointer" : ""}`}
              style={pnlBgStyle(cell.pnl, maxAbsPnl)}
              onClick={() => onCellClick?.(cell.marketId)}
            >
              <div className="flex items-start justify-between gap-1">
                <span className="text-[10px] text-txt-primary font-medium leading-tight line-clamp-2 flex-1">
                  {cell.title}
                </span>
                <span
                  className={`text-[8px] font-bold tracking-wider px-1 py-px rounded shrink-0 ${
                    cell.contract.toLowerCase() === "yes"
                      ? "bg-profit-dim text-profit"
                      : "bg-loss-dim text-loss"
                  }`}
                >
                  {cell.contract.toUpperCase()}
                </span>
              </div>

              <div className="mt-1.5 flex items-end justify-between">
                <div className="text-[9px] font-mono text-txt-muted">
                  <span>{cell.quantity} @ {fmtDollar(cell.capital)}</span>
                </div>
                <div className="text-right">
                  <div className={`text-[11px] font-mono font-medium ${pnlCls(cell.pnl)}`}>
                    {fmtDollar(cell.pnl)}
                  </div>
                  <div className={`text-[9px] font-mono ${pnlCls(cell.returnPct)}`}>
                    {fmtPct(cell.returnPct)}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Legend */}
        <div className="mt-3 flex items-center justify-center gap-4 text-[9px] text-txt-muted">
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-2 rounded-sm" style={{ backgroundColor: "rgba(255, 71, 87, 0.35)" }} />
            <span>Loss</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-2 rounded-sm bg-t-panel border border-t-border" />
            <span>Flat</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className="w-3 h-2 rounded-sm" style={{ backgroundColor: "rgba(0, 210, 106, 0.35)" }} />
            <span>Profit</span>
          </div>
          <span className="text-t-border-light">|</span>
          <span>Size = capital deployed</span>
        </div>
      </div>
    </div>
  );
}
