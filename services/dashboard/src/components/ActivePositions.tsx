"use client";

import { useState, useMemo } from "react";
import type { Position, Market } from "@/lib/api";
import { kalshiMarketUrl, kalshiEventUrl } from "@/lib/api";
import { pnlCls, fmtDollar } from "@/lib/utils";

type SortKey =
  | "market"
  | "contract"
  | "quantity"
  | "avg_price"
  | "mkt_price"
  | "predicted"
  | "edge"
  | "capital"
  | "unrealized"
  | "return";

function getMarketForPosition(
  pos: Position,
  byId: Map<string, Market>,
  byTicker: Map<string, Market>
): Market | undefined {
  return byId.get(pos.market_id) ?? (pos.ticker ? byTicker.get(pos.ticker) : undefined);
}

export function ActivePositions({
  positions,
  markets,
}: {
  positions: Position[];
  markets: Market[];
}) {
  const [sortKey, setSortKey] = useState<SortKey>("unrealized");
  const [sortAsc, setSortAsc] = useState(false);

  const { byId, byTicker } = useMemo(() => {
    const byId = new Map(markets.map((m) => [m.market_id, m]));
    const byTicker = new Map(markets.map((m) => [m.ticker, m]));
    return { byId, byTicker };
  }, [markets]);

  if (positions.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No active positions
      </div>
    );
  }

  const handleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else {
      setSortKey(key);
      setSortAsc(false);
    }
  };

  const getMktPrice = (_pos: Position, mkt: Market | undefined): number | null => {
    if (!mkt) return null;
    return mkt.yes_ask;
  };

  const getPredicted = (mkt: Market | undefined): number | null => {
    return mkt?.model_prediction?.p_yes ?? null;
  };

  const sorted = [...positions].sort((a, b) => {
    let diff = 0;
    const mktA = getMarketForPosition(a, byId, byTicker);
    const mktB = getMarketForPosition(b, byId, byTicker);
    switch (sortKey) {
      case "market":
        diff = (a.market_title ?? "").localeCompare(b.market_title ?? "");
        break;
      case "contract":
        diff = a.contract.localeCompare(b.contract);
        break;
      case "quantity":
        diff = a.quantity - b.quantity;
        break;
      case "avg_price":
        diff = a.avg_price - b.avg_price;
        break;
      case "mkt_price":
        diff = (getMktPrice(a, mktA) ?? 0) - (getMktPrice(b, mktB) ?? 0);
        break;
      case "predicted":
        diff = (getPredicted(mktA) ?? 0) - (getPredicted(mktB) ?? 0);
        break;
      case "edge": {
        const edgeA = (getPredicted(mktA) ?? 0) - (getMktPrice(a, mktA) ?? 0);
        const edgeB = (getPredicted(mktB) ?? 0) - (getMktPrice(b, mktB) ?? 0);
        diff = edgeA - edgeB;
        break;
      }
      case "capital":
        diff = a.avg_price * a.quantity - b.avg_price * b.quantity;
        break;
      case "unrealized":
        diff = a.unrealized_pnl - b.unrealized_pnl;
        break;
      case "return": {
        const capA = a.avg_price * a.quantity;
        const capB = b.avg_price * b.quantity;
        diff =
          (capA > 0 ? a.unrealized_pnl / capA : 0) -
          (capB > 0 ? b.unrealized_pnl / capB : 0);
        break;
      }
    }
    return sortAsc ? diff : -diff;
  });

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
              <Th k="market" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left">Market</Th>
              <Th k="contract" cur={sortKey} asc={sortAsc} onClick={handleSort} align="center">Side</Th>
              <Th k="quantity" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Qty</Th>
              <Th k="avg_price" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Entry</Th>
              <Th k="mkt_price" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Yes Ask</Th>
              <Th k="predicted" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Model P</Th>
              <Th k="edge" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Edge</Th>
              <Th k="capital" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Capital</Th>
              <Th k="unrealized" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">P&L</Th>
              <Th k="return" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Return</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-t-border/40">
            {sorted.map((pos) => {
              const capital = pos.avg_price * pos.quantity;
              const totalPnl = pos.realized_pnl + pos.unrealized_pnl;
              const ret = capital > 0 ? (totalPnl / capital) * 100 : 0;
              const mkt = getMarketForPosition(pos, byId, byTicker);
              const mktPrice = getMktPrice(pos, mkt);
              const predicted = getPredicted(mkt);
              const edge =
                predicted != null && mktPrice != null
                  ? predicted - mktPrice
                  : null;

              return (
                <tr
                  key={pos.id}
                  className="hover:bg-t-panel-hover transition-colors"
                >
                  {/* Market cell */}
                  <td className="px-3 py-2 max-w-[280px]">
                    <div className="flex flex-col gap-0.5">
                      {pos.event_ticker ? (
                        <a
                          href={kalshiMarketUrl(pos.event_ticker)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-txt-primary hover:text-accent transition-colors font-medium truncate text-xs"
                        >
                          {pos.market_title ?? pos.market_id}
                        </a>
                      ) : (
                        <span className="text-txt-primary font-medium truncate text-xs">
                          {pos.market_title ?? pos.market_id}
                        </span>
                      )}
                      <div className="flex items-center gap-1.5 text-[9px] font-mono text-txt-muted">
                        {pos.ticker && <span>{pos.ticker}</span>}
                        {pos.event_ticker && (
                          <>
                            <span className="text-t-border-light">/</span>
                            <a
                              href={kalshiEventUrl(pos.event_ticker)}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="hover:text-accent transition-colors"
                            >
                              {pos.event_ticker}
                            </a>
                          </>
                        )}
                      </div>
                    </div>
                  </td>

                  <td className="px-3 py-2 text-center">
                    <span
                      className={`inline-block px-1.5 py-px rounded text-[9px] font-bold tracking-wider ${
                        pos.contract.toLowerCase() === "yes"
                          ? "bg-profit-dim text-profit"
                          : "bg-loss-dim text-loss"
                      }`}
                    >
                      {pos.contract.toUpperCase()}
                    </span>
                  </td>

                  <td className="px-3 py-2 text-right font-mono text-txt-primary">
                    {pos.quantity}
                  </td>
                  <td className="px-3 py-2 text-right font-mono text-txt-primary">
                    {(pos.avg_price * 100).toFixed(0)}c
                  </td>

                  {/* Current market price */}
                  <td className="px-3 py-2 text-right font-mono text-txt-primary">
                    {mktPrice != null
                      ? `${(mktPrice * 100).toFixed(0)}c`
                      : "--"}
                  </td>

                  {/* Model predicted probability */}
                  <td className="px-3 py-2 text-right font-mono text-accent">
                    {predicted != null
                      ? `${(predicted * 100).toFixed(0)}%`
                      : "--"}
                  </td>

                  {/* Edge = model - market */}
                  <td className={`px-3 py-2 text-right font-mono font-medium ${edge != null ? pnlCls(edge) : "text-txt-muted"}`}>
                    {edge != null
                      ? `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(0)}pp`
                      : "--"}
                  </td>

                  <td className="px-3 py-2 text-right font-mono text-txt-primary">
                    {fmtDollar(capital)}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono ${pnlCls(totalPnl)}`}>
                    {fmtDollar(totalPnl)}
                  </td>
                  <td className={`px-3 py-2 text-right font-mono font-medium ${pnlCls(ret)}`}>
                    {ret >= 0 ? "+" : ""}{ret.toFixed(1)}%
                  </td>
                </tr>
              );
            })}
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
