"use client";

import { useState, useMemo } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
} from "recharts";
import type { AnalyticsSummary } from "@/lib/api";
import { fmtDollar, TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE, CHART_COLORS } from "@/lib/utils";

type Tab = "model" | "market";

export function PnLAttribution({
  analytics,
}: {
  analytics: AnalyticsSummary | null;
}) {
  const [tab, setTab] = useState<Tab>("model");

  const modelData = useMemo(() => {
    if (!analytics?.pnl_by_model) return [];
    return Object.entries(analytics.pnl_by_model)
      .map(([name, data]) => ({
        name: name.length > 25 ? name.slice(0, 22) + "..." : name,
        fullName: name,
        pnl: parseFloat(data.pnl.toFixed(2)),
        trades: data.trades,
        winRate: parseFloat((data.win_rate * 100).toFixed(1)),
        fill: data.pnl >= 0 ? CHART_COLORS.profit : CHART_COLORS.loss,
      }))
      .sort((a, b) => b.pnl - a.pnl);
  }, [analytics]);

  const marketData = useMemo(() => {
    if (!analytics?.pnl_by_market) return [];
    return Object.entries(analytics.pnl_by_market)
      .map(([id, data]) => {
        const title = data.title || id;
        return {
          name: title.length > 30 ? title.slice(0, 27) + "..." : title,
          fullName: title,
          pnl: parseFloat(data.pnl.toFixed(2)),
          trades: data.trades,
          fill: data.pnl >= 0 ? CHART_COLORS.profit : CHART_COLORS.loss,
        };
      })
      .sort((a, b) => b.pnl - a.pnl);
  }, [analytics]);

  if (!analytics) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No analytics data available
      </div>
    );
  }

  const data = tab === "model" ? modelData : marketData;
  const chartHeight = Math.max(200, data.length * 32);

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Header with tabs */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          P&L Attribution
        </h3>
        <div className="flex gap-1">
          {(["model", "market"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                tab === t
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              By {t === "model" ? "Model" : "Market"}
            </button>
          ))}
        </div>
      </div>

      <div className="p-3">
        {data.length === 0 ? (
          <div className="text-center text-txt-muted text-[10px] py-8">
            {!analytics
              ? "Waiting for trade data"
              : `No ${tab} breakdown data — trades needed for attribution`}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart
              data={data}
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
                tickFormatter={(v) => `$${v.toFixed(2)}`}
              />
              <YAxis
                type="category"
                dataKey="name"
                stroke="transparent"
                fontSize={9}
                tickLine={false}
                axisLine={false}
                tick={{ fill: CHART_COLORS.muted }}
                width={140}
              />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelStyle={TOOLTIP_LABEL_STYLE}
                cursor={{ fill: "rgba(255,255,255,0.03)" }}
                content={({ active, payload }) => {
                  if (!active || !payload?.[0]) return null;
                  const d = payload[0].payload;
                  return (
                    <div style={TOOLTIP_STYLE}>
                      <div style={TOOLTIP_LABEL_STYLE} className="mb-1">
                        {d.fullName}
                      </div>
                      <div>
                        P&L: <span style={{ color: d.pnl >= 0 ? CHART_COLORS.profit : CHART_COLORS.loss }}>{fmtDollar(d.pnl)}</span>
                      </div>
                      <div>Trades: {d.trades}</div>
                      {d.winRate !== undefined && (
                        <div>Win Rate: {d.winRate}%</div>
                      )}
                    </div>
                  );
                }}
              />
              <ReferenceLine x={0} stroke={CHART_COLORS.reference} strokeDasharray="4 4" />
              <Bar
                dataKey="pnl"
                radius={[0, 2, 2, 0]}
                maxBarSize={20}
                isAnimationActive={false}
              >
                {data.map((entry, idx) => (
                  <Cell key={idx} fill={entry.fill} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Summary */}
      {data.length > 0 && (
        <div className="px-3 py-2 border-t border-t-border flex gap-4 text-[10px] font-mono text-txt-muted">
          <span>
            Total:{" "}
            <span className={data.reduce((s, d) => s + d.pnl, 0) >= 0 ? "text-profit" : "text-loss"}>
              {fmtDollar(data.reduce((s, d) => s + d.pnl, 0))}
            </span>
          </span>
          <span>
            {tab === "model" ? "Models" : "Markets"}: <span className="text-txt-primary">{data.length}</span>
          </span>
          <span>
            Profitable:{" "}
            <span className="text-profit">
              {data.filter((d) => d.pnl > 0).length}
            </span>
          </span>
        </div>
      )}
    </div>
  );
}
