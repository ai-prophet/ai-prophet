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
  aggregated_p_yes: number;
  edge: number;
  models: Record<string, { p_yes: number; decision: string }>;
  disagreement: number; // max - min p_yes across models
}

export function ModelAggregation({ markets }: { markets: Market[] }) {
  const { rows, modelNames, stats } = useMemo(() => {
    const rows: ModelMarketRow[] = [];
    const modelNameSet = new Set<string>();

    for (const mkt of markets) {
      const preds = mkt.model_predictions?.filter((p) => p.model_name !== "aggregated") ?? [];
      if (preds.length === 0) continue;

      const models: Record<string, { p_yes: number; decision: string }> = {};
      const pValues: number[] = [];

      for (const pred of preds) {
        if (pred.p_yes == null) continue;
        models[pred.model_name] = { p_yes: pred.p_yes, decision: pred.decision };
        modelNameSet.add(pred.model_name);
        pValues.push(pred.p_yes);
      }

      if (pValues.length === 0 || mkt.yes_ask == null) continue;

      const aggP = mkt.aggregated_p_yes ?? pValues.reduce((a, b) => a + b, 0) / pValues.length;
      const disagreement = pValues.length > 1 ? Math.max(...pValues) - Math.min(...pValues) : 0;

      rows.push({
        market_id: mkt.market_id,
        title: mkt.title,
        ticker: mkt.ticker,
        yes_ask: mkt.yes_ask,
        aggregated_p_yes: aggP,
        edge: aggP - mkt.yes_ask,
        models,
        disagreement,
      });
    }

    // Sort by absolute edge descending
    rows.sort((a, b) => Math.abs(b.edge) - Math.abs(a.edge));

    const modelNames = Array.from(modelNameSet).sort();

    // Compute summary stats
    const totalDisagreements = rows.filter((r) => r.disagreement > 0.10).length;
    const avgDisagreement = rows.length > 0
      ? rows.reduce((s, r) => s + r.disagreement, 0) / rows.length
      : 0;

    return {
      rows,
      modelNames,
      stats: { totalDisagreements, avgDisagreement, totalMarkets: rows.length },
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
        <span className="text-txt-muted">
          Avg spread: <span className="text-txt-primary font-medium">{(stats.avgDisagreement * 100).toFixed(1)}pp</span>
        </span>
        {stats.totalDisagreements > 0 && (
          <span className="text-amber-400">
            {stats.totalDisagreements} high disagreement{stats.totalDisagreements > 1 ? "s" : ""} (&gt;10pp)
          </span>
        )}
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
              <th className="px-3 py-2 text-right font-medium text-accent">Aggregated</th>
              <th className="px-3 py-2 text-right font-medium">Edge</th>
              <th className="px-3 py-2 text-right font-medium">Spread</th>
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
                    </td>
                  );
                })}
                <td className="px-3 py-2 text-right font-mono text-accent font-medium">
                  {(row.aggregated_p_yes * 100).toFixed(0)}%
                </td>
                <td className={`px-3 py-2 text-right font-mono font-medium ${pnlCls(row.edge)}`}>
                  {row.edge >= 0 ? "+" : ""}{(row.edge * 100).toFixed(0)}pp
                </td>
                <td className="px-3 py-2 text-right font-mono">
                  <span className={row.disagreement > 0.10 ? "text-amber-400 font-medium" : "text-txt-muted"}>
                    {(row.disagreement * 100).toFixed(0)}pp
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
