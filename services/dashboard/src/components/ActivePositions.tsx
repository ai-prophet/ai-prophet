"use client";

import { useState, useMemo } from "react";
import type { Position, Market, ModelPrediction } from "@/lib/api";
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

/** Short display name — compact enough for tight table cells.
 *  "gemini:gemini-3.1-pro-preview:market" → "g-3.1-pro"
 *  "gemini:gemini-3.1-pro-preview"         → "g-3.1-pro*"
 *  The asterisk (*) marks the variant WITHOUT market data.
 */
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

const PAGE_SIZE = 25;

export function ActivePositions({
  positions,
  markets,
}: {
  positions: Position[];
  markets: Market[];
}) {
  const [sortKey, setSortKey] = useState<SortKey>("unrealized");
  const [sortAsc, setSortAsc] = useState(false);
  const [search, setSearch] = useState("");
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);
  const [expandedId, setExpandedId] = useState<number | null>(null);

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

  const getYesAsk = (mkt: Market | undefined): number | null => mkt?.yes_ask ?? null;
  const getNoAsk = (mkt: Market | undefined): number | null => mkt?.no_ask ?? null;

  const getPredicted = (mkt: Market | undefined): number | null => {
    return mkt?.aggregated_p_yes ?? mkt?.model_prediction?.p_yes ?? null;
  };

  const getModelPredictions = (mkt: Market | undefined): ModelPrediction[] => {
    return mkt?.model_predictions?.filter((p) => p.model_name !== "aggregated") ?? [];
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
        diff = (getYesAsk(mktA) ?? 0) - (getYesAsk(mktB) ?? 0);
        break;
      case "predicted":
        diff = (getPredicted(mktA) ?? 0) - (getPredicted(mktB) ?? 0);
        break;
      case "edge": {
        const edgeA = (getPredicted(mktA) ?? 0) - (getYesAsk(mktA) ?? 0);
        const edgeB = (getPredicted(mktB) ?? 0) - (getYesAsk(mktB) ?? 0);
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

  // Client-side search filter
  const filtered = search
    ? sorted.filter((pos) => {
        const needle = search.toLowerCase();
        return (
          (pos.market_title?.toLowerCase().includes(needle)) ||
          (pos.ticker?.toLowerCase().includes(needle)) ||
          (pos.event_ticker?.toLowerCase().includes(needle))
        );
      })
    : sorted;

  const visible = filtered.slice(0, visibleCount);
  const hasMore = visibleCount < filtered.length;

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Search bar */}
      {positions.length > PAGE_SIZE && (
        <div className="px-3 py-2 border-b border-t-border">
          <input
            type="text"
            placeholder="Search markets..."
            value={search}
            onChange={(e) => { setSearch(e.target.value); setVisibleCount(PAGE_SIZE); }}
            className="w-full bg-t-bg border border-t-border rounded px-2 py-1 text-xs text-txt-primary placeholder-txt-muted focus:outline-none focus:border-accent"
          />
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
              <Th k="market" cur={sortKey} asc={sortAsc} onClick={handleSort} align="left">Market</Th>
              <Th k="contract" cur={sortKey} asc={sortAsc} onClick={handleSort} align="center">Side</Th>
              <Th k="quantity" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Qty</Th>
              <Th k="avg_price" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Entry</Th>
              <Th k="mkt_price" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Yes / No</Th>
              <Th k="predicted" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Model P</Th>
              <Th k="edge" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Edge</Th>
              <Th k="capital" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Capital</Th>
              <Th k="unrealized" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">P&L</Th>
              <Th k="return" cur={sortKey} asc={sortAsc} onClick={handleSort} align="right">Return</Th>
            </tr>
          </thead>
          <tbody className="divide-y divide-t-border/40">
            {visible.map((pos) => {
              const capital = pos.avg_price * pos.quantity;
              const totalPnl = pos.realized_pnl + pos.unrealized_pnl;
              const ret = capital > 0 ? (totalPnl / capital) * 100 : 0;
              const mkt = getMarketForPosition(pos, byId, byTicker);
              const yesAsk = getYesAsk(mkt);
              const noAsk = getNoAsk(mkt);
              const predicted = getPredicted(mkt);
              const isYes = pos.contract.toLowerCase() === "yes";
              const edge =
                predicted != null && yesAsk != null
                  ? predicted - yesAsk
                  : null;
              const modelPreds = getModelPredictions(mkt);
              const isExpanded = expandedId === pos.id;
              const hasMultipleModels = modelPreds.length > 1;

              return (
                <>
                  <tr
                    key={pos.id}
                    className={`hover:bg-t-panel-hover transition-colors ${hasMultipleModels ? "cursor-pointer" : ""}`}
                    onClick={hasMultipleModels ? () => setExpandedId(isExpanded ? null : pos.id) : undefined}
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
                            onClick={(e) => e.stopPropagation()}
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
                                onClick={(e) => e.stopPropagation()}
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

                    {/* Current market prices — highlight the side we're on */}
                    <td className="px-3 py-2 text-right font-mono">
                      <span className={isYes ? "text-profit font-semibold" : "text-txt-muted"}>
                        {yesAsk != null ? `${(yesAsk * 100).toFixed(0)}c` : "--"}
                      </span>
                      <span className="text-txt-muted mx-0.5">/</span>
                      <span className={!isYes ? "text-loss font-semibold" : "text-txt-muted"}>
                        {noAsk != null ? `${(noAsk * 100).toFixed(0)}c` : "--"}
                      </span>
                    </td>

                    {/* Model predicted probability — aggregated */}
                    <td className="px-3 py-2 text-right font-mono text-accent">
                      <span className="flex items-center justify-end gap-1">
                        {predicted != null
                          ? `${(predicted * 100).toFixed(1)}%`
                          : "--"}
                        {hasMultipleModels && (
                          <span className="text-[8px] text-txt-muted">
                            {isExpanded ? "\u25B2" : "\u25BC"}
                          </span>
                        )}
                      </span>
                    </td>

                    {/* Edge = model - market */}
                    <td className={`px-3 py-2 text-right font-mono font-medium ${edge != null ? pnlCls(edge) : "text-txt-muted"}`}>
                      {edge != null
                        ? `${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)}pp`
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

                  {/* Expanded per-model breakdown */}
                  {isExpanded && hasMultipleModels && (
                    <tr key={`${pos.id}-models`}>
                      <td colSpan={10} className="px-6 py-3 bg-t-bg/50">
                        <div className="flex flex-col gap-2">
                          {/* Header with yes_ask context */}
                          <div className="flex items-center gap-4 text-[9px] text-txt-muted uppercase tracking-wider">
                            <span>Per-Model Predictions</span>
                            {yesAsk != null && (
                              <span className="normal-case tracking-normal">
                                yes_ask: <span className="text-txt-primary font-mono">{(yesAsk * 100).toFixed(1)}c</span>
                              </span>
                            )}
                          </div>
                          {/* Model rows */}
                          <div className="grid gap-1">
                            {modelPreds.map((pred, i) => {
                              const modelEdge = pred.p_yes != null && yesAsk != null
                                ? pred.p_yes - yesAsk
                                : null;
                              return (
                                <div
                                  key={pred.model_name}
                                  className="flex items-center gap-3 text-[10px] font-mono"
                                >
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
                                  <span className={`px-1.5 py-px rounded text-[8px] font-bold ${
                                    pred.decision === "BUY_YES"
                                      ? "bg-profit-dim text-profit"
                                      : pred.decision === "BUY_NO"
                                        ? "bg-loss-dim text-loss"
                                        : "bg-t-border/30 text-txt-muted"
                                  }`}>
                                    {pred.decision}
                                  </span>
                                </div>
                              );
                            })}
                          </div>
                          {/* Aggregated summary */}
                          <div className="flex items-center gap-3 text-[10px] font-mono pt-1 border-t border-t-border/30">
                            <span className="w-28 font-medium text-accent">agg (sum)</span>
                            <span className="text-accent w-14 text-right">
                              p: {predicted != null ? `${(predicted * 100).toFixed(1)}%` : "--"}
                            </span>
                            <span className={`w-20 text-right font-medium ${edge != null ? pnlCls(edge) : "text-txt-muted"}`}>
                              {edge != null
                                ? `edge: ${edge >= 0 ? "+" : ""}${(edge * 100).toFixed(1)}pp`
                                : "--"}
                            </span>
                          </div>
                        </div>
                      </td>
                    </tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
      {/* Show more / count */}
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
