"use client";

import { useMemo } from "react";
import type { Market } from "@/lib/api";
import { pnlCls } from "@/lib/utils";

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

const MODEL_BG_COLORS = [
  "bg-blue-400/10",
  "bg-amber-400/10",
  "bg-emerald-400/10",
  "bg-purple-400/10",
  "bg-rose-400/10",
  "bg-cyan-400/10",
];

interface ModelMarketRow {
  market_id: string;
  title: string;
  ticker: string;
  yes_ask: number;
  aggregated_edge: number;
  models: Record<string, { p_yes: number; decision: string; edge: number }>;
}

export function ModelAggregation({ markets }: { markets: Market[] }) {
  const { rows, modelNames, stats } = useMemo(() => {
    const rows: ModelMarketRow[] = [];
    const modelNameSet = new Set<string>();

    for (const mkt of markets) {
      const preds = mkt.model_predictions?.filter((p) => p.model_name !== "aggregated") ?? [];
      if (preds.length === 0) continue;

      const models: Record<string, { p_yes: number; decision: string; edge: number }> = {};

      for (const pred of preds) {
        if (pred.p_yes == null) continue;
        const edge = pred.p_yes - (mkt.yes_ask ?? 0);
        models[pred.model_name] = { p_yes: pred.p_yes, decision: pred.decision, edge };
        modelNameSet.add(pred.model_name);
      }

      if (Object.keys(models).length === 0 || mkt.yes_ask == null) continue;

      // Signed-sum of edges
      const aggregated_edge = Object.values(models).reduce((s, m) => s + m.edge, 0);

      rows.push({
        market_id: mkt.market_id,
        title: mkt.title,
        ticker: mkt.ticker,
        yes_ask: mkt.yes_ask,
        aggregated_edge,
        models,
      });
    }

    // Sort by absolute aggregated edge descending
    rows.sort((a, b) => Math.abs(b.aggregated_edge) - Math.abs(a.aggregated_edge));

    const modelNames = Array.from(modelNameSet).sort();

    return {
      rows,
      modelNames,
      stats: { totalMarkets: rows.length },
    };
  }, [markets]);

  if (rows.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-6 text-center text-txt-muted text-xs">
        No model predictions available
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Summary stats */}
      <div className="px-4 py-2 border-b border-t-border flex items-center gap-6 text-[10px]">
        <div className="flex items-center gap-2">
          <span className="text-txt-muted">Models:</span>
          <div className="flex gap-1.5">
            {modelNames.map((name, i) => (
              <span
                key={name}
                className={`${MODEL_COLORS[i % MODEL_COLORS.length]} ${MODEL_BG_COLORS[i % MODEL_BG_COLORS.length]} px-1.5 py-px rounded font-mono font-medium`}
              >
                {shortModelName(name)}
              </span>
            ))}
          </div>
        </div>
        <span className="text-txt-muted">
          Markets: <span className="text-txt-primary font-medium">{stats.totalMarkets}</span>
        </span>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
              <th className="px-3 py-2 text-left font-medium">Market</th>
              <th className="px-3 py-2 text-right font-medium">Yes Ask</th>
              {modelNames.map((name, i) => (
                <th
                  key={name}
                  className={`px-3 py-2 text-right font-medium ${MODEL_COLORS[i % MODEL_COLORS.length]}`}
                >
                  {shortModelName(name)}
                </th>
              ))}
              <th className="px-3 py-2 text-right font-medium text-accent">Agg Edge</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-t-border/40">
            {rows.map((row) => (
              <tr key={row.market_id} className="hover:bg-t-panel-hover transition-colors">
                <td className="px-3 py-2 max-w-[240px]">
                  <div className="truncate text-txt-primary font-medium">{row.title}</div>
                  <div className="text-[9px] font-mono text-txt-muted">{row.ticker}</div>
                </td>
                <td className="px-3 py-2 text-right font-mono text-txt-primary">
                  {(row.yes_ask * 100).toFixed(0)}c
                </td>
                {modelNames.map((name, i) => {
                  const pred = row.models[name];
                  if (!pred) {
                    return (
                      <td key={name} className="px-3 py-2 text-right font-mono text-txt-muted">
                        --
                      </td>
                    );
                  }
                  return (
                    <td key={name} className={`px-3 py-2 text-right font-mono ${MODEL_COLORS[i % MODEL_COLORS.length]}`}>
                      {(pred.p_yes * 100).toFixed(0)}%
                      <span className={`ml-1 text-[9px] ${pnlCls(pred.edge)}`}>
                        {pred.edge >= 0 ? "+" : ""}{(pred.edge * 100).toFixed(0)}
                      </span>
                    </td>
                  );
                })}
                <td className={`px-3 py-2 text-right font-mono font-medium ${pnlCls(row.aggregated_edge)}`}>
                  {row.aggregated_edge >= 0 ? "+" : ""}{(row.aggregated_edge * 100).toFixed(0)}pp
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
