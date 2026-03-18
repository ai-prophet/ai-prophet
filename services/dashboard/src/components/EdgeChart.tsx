"use client";

import { useState, useMemo, useEffect } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  LineChart,
  Line,
  ReferenceLine,
} from "recharts";
import type { ApiClient, Position, Market } from "@/lib/api";
import { fmtEdge, fmtTimeShort, TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE, CHART_COLORS } from "@/lib/utils";

type View = "current" | "history";

interface EdgeHistoryPoint {
  time: string;
  edge: number;
  modelProb: number;
  yesAsk: number;
}

function getMarketForPosition(
  pos: Position,
  byId: Map<string, Market>,
  byTicker: Map<string, Market>
): Market | undefined {
  return byId.get(pos.market_id) ?? (pos.ticker ? byTicker.get(pos.ticker) : undefined);
}

export function EdgeChart({
  positions,
  markets,
  apiClient,
}: {
  positions: Position[];
  markets: Market[];
  apiClient: ApiClient;
}) {
  const [view, setView] = useState<View>("current");
  const [selectedMarketId, setSelectedMarketId] = useState<string | null>(null);
  const [edgeHistory, setEdgeHistory] = useState<EdgeHistoryPoint[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const { byId, byTicker } = useMemo(() => {
    const byId = new Map(markets.map((m) => [m.market_id, m]));
    const byTicker = new Map(markets.map((m) => [m.ticker, m]));
    return { byId, byTicker };
  }, [markets]);

  const edgeData = useMemo(() => {
    return positions
      .map((pos) => {
        const mkt = getMarketForPosition(pos, byId, byTicker);
        if (!mkt || !mkt.model_prediction?.p_yes) return null;

        const side = pos.contract.toLowerCase();
        const mktPrice = side === "yes" ? mkt.yes_ask : mkt.no_ask;
        if (mktPrice == null) return null;

        const modelProb = mkt.model_prediction.p_yes;
        const edge = modelProb - mktPrice;

        return {
          marketId: pos.market_id,
          title: (pos.market_title ?? pos.ticker ?? pos.market_id).slice(0, 40),
          edge: parseFloat((edge * 100).toFixed(1)),
          modelProb: parseFloat((modelProb * 100).toFixed(1)),
          mktPrice: parseFloat((mktPrice * 100).toFixed(1)),
          fill: edge >= 0 ? CHART_COLORS.profit : CHART_COLORS.loss,
        };
      })
      .filter(Boolean)
      .sort((a, b) => Math.abs(b!.edge) - Math.abs(a!.edge)) as Array<{
      marketId: string;
      title: string;
      edge: number;
      modelProb: number;
      mktPrice: number;
      fill: string;
    }>;
  }, [positions, byId, byTicker]);

  // Fetch edge history from price snapshots when a market is selected
  useEffect(() => {
    if (!selectedMarketId) {
      setEdgeHistory([]);
      return;
    }
    setLoadingHistory(true);
    apiClient.getPriceHistory(selectedMarketId).then((data) => {
      const points: EdgeHistoryPoint[] = (data ?? [])
        .filter((p) => p.model_p_yes != null)
        .map((p) => ({
          time: fmtTimeShort(p.timestamp),
          edge: parseFloat(((p.model_p_yes! - p.yes_ask) * 100).toFixed(1)),
          modelProb: parseFloat((p.model_p_yes! * 100).toFixed(1)),
          yesAsk: parseFloat((p.yes_ask * 100).toFixed(1)),
        }));
      setEdgeHistory(points);
      setLoadingHistory(false);
    });
  }, [apiClient, selectedMarketId]);

  if (positions.length === 0 || edgeData.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No edge data available — positions need model predictions and market prices
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Header with toggle */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Edge Analysis
        </h3>
        <div className="flex gap-1">
          {(["current", "history"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                view === v
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {v === "current" ? "Current Edge" : "Edge History"}
            </button>
          ))}
        </div>
      </div>

      <div className="p-3">
        {view === "current" ? (
          <ResponsiveContainer width="100%" height={Math.max(200, edgeData.length * 32)}>
            <BarChart
              data={edgeData}
              layout="vertical"
              margin={{ left: 10, right: 20, top: 5, bottom: 5 }}
            >
              <CartesianGrid
                strokeDasharray="3 3"
                stroke={CHART_COLORS.grid}
                horizontal={false}
              />
              <XAxis
                type="number"
                stroke="transparent"
                fontSize={9}
                tickLine={false}
                axisLine={false}
                tick={{ fill: CHART_COLORS.muted }}
                tickFormatter={(v) => `${v}pp`}
              />
              <YAxis
                type="category"
                dataKey="title"
                stroke="transparent"
                fontSize={9}
                tickLine={false}
                axisLine={false}
                tick={{ fill: "#e8edf5" }}
                width={160}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={{ ...TOOLTIP_LABEL_STYLE, color: "#e8edf5" }}
                cursor={{ fill: "rgba(255,255,255,0.03)" }}
                formatter={(_: number, __: string, entry: any) => {
                  const d = entry?.payload;
                  if (!d) return ["", ""];
                  return [
                    `Edge: ${d.edge >= 0 ? "+" : ""}${d.edge}pp | Model: ${d.modelProb}% | Mkt: ${d.mktPrice}%`,
                    "",
                  ];
                }}
                itemStyle={{ color: "#e8edf5" }}
              />
              <ReferenceLine x={0} stroke={CHART_COLORS.reference} strokeDasharray="4 4" />
              <Bar
                dataKey="edge"
                radius={[0, 2, 2, 0]}
                maxBarSize={20}
                isAnimationActive={false}
              >
                {edgeData.map((entry, idx) => (
                  <rect key={idx} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        ) : (
          <div className="space-y-3">
            {/* Market selector for history view */}
            <div className="flex flex-wrap gap-1">
              {edgeData.map((d) => (
                <button
                  key={d.marketId}
                  onClick={() => setSelectedMarketId(d.marketId)}
                  className={`px-2 py-0.5 text-[9px] rounded border transition-colors truncate max-w-[200px] ${
                    selectedMarketId === d.marketId
                      ? "border-accent text-accent bg-accent/10"
                      : "border-t-border text-txt-muted hover:text-txt-secondary"
                  }`}
                >
                  {d.title}
                </button>
              ))}
            </div>

            {selectedMarketId && loadingHistory ? (
              <div className="text-center text-txt-muted text-[10px] py-8">
                Loading edge history...
              </div>
            ) : selectedMarketId && edgeHistory.length > 0 ? (
              <ResponsiveContainer width="100%" height={200}>
                <LineChart data={edgeHistory}>
                  <CartesianGrid
                    strokeDasharray="3 3"
                    stroke={CHART_COLORS.grid}
                    vertical={false}
                  />
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
                    tickFormatter={(v) => `${v}pp`}
                    width={40}
                  />
                  <Tooltip
                    contentStyle={TOOLTIP_STYLE}
                    labelStyle={{ ...TOOLTIP_LABEL_STYLE, color: "#e8edf5" }}
                    formatter={(_: number, __: string, entry: any) => {
                      const d = entry?.payload;
                      if (!d) return ["", ""];
                      return [
                        `Edge: ${d.edge >= 0 ? "+" : ""}${d.edge}pp | Model: ${d.modelProb}% | Ask: ${d.yesAsk}%`,
                        "",
                      ];
                    }}
                    itemStyle={{ color: "#e8edf5" }}
                  />
                  <ReferenceLine y={0} stroke={CHART_COLORS.reference} strokeDasharray="4 4" />
                  <Line
                    type="monotone"
                    dataKey="edge"
                    stroke={CHART_COLORS.accent}
                    strokeWidth={1.5}
                    dot={edgeHistory.length < 50}
                    activeDot={{ r: 2.5, fill: CHART_COLORS.accent, stroke: "#0f1419", strokeWidth: 2 }}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="text-center text-txt-muted text-[10px] py-8">
                {selectedMarketId
                  ? "No edge history available — price snapshots will accumulate over cycles"
                  : "Select a market to view edge history"}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Summary row */}
      {view === "current" && (
        <div className="px-3 py-2 border-t border-t-border flex gap-4 text-[10px] font-mono text-txt-muted">
          <span>
            Avg Edge:{" "}
            <span className="text-txt-primary">
              {(edgeData.reduce((s, d) => s + d.edge, 0) / edgeData.length).toFixed(1)}pp
            </span>
          </span>
          <span>
            Positive:{" "}
            <span className="text-profit">
              {edgeData.filter((d) => d.edge > 0).length}
            </span>
          </span>
          <span>
            Negative:{" "}
            <span className="text-loss">
              {edgeData.filter((d) => d.edge < 0).length}
            </span>
          </span>
        </div>
      )}
    </div>
  );
}
