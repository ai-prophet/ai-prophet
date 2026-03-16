"use client";

import { useState, useMemo } from "react";
import {
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
  ComposedChart,
  Line,
  Brush,
} from "recharts";
import type { PnLPoint, TradeMarker } from "@/lib/api";
import { TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE, CHART_COLORS } from "@/lib/utils";

type ViewMode = "cumulative" | "realized" | "unrealized";

export function PnLChart({
  data,
  tradeMarkers = [],
}: {
  data: PnLPoint[];
  tradeMarkers?: TradeMarker[];
}) {
  const [viewMode, setViewMode] = useState<ViewMode>("cumulative");
  const [showDrawdown, setShowDrawdown] = useState(false);
  const [showTradeMarkers, setShowTradeMarkers] = useState(true);

  const chartData = useMemo(() => {
    if (data.length === 0) return [];

    let peak = 0;
    return data.map((d) => {
      const pnl = d.pnl ?? 0;
      const realized = d.realized_pnl ?? 0;
      const unrealized = d.unrealized_pnl ?? (pnl - realized);

      if (pnl > peak) peak = pnl;
      const drawdown = peak > 0 ? pnl - peak : 0;

      return {
        time: new Date(d.timestamp).toLocaleDateString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
        timestamp: new Date(d.timestamp).getTime(),
        pnl,
        realized,
        unrealized,
        drawdown,
        tradeCost: d.trade_cost,
        ticker: d.ticker,
        side: d.side,
        action: d.action ?? "BUY",
      };
    });
  }, [data]);

  if (data.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-10 text-center text-txt-muted text-xs">
        No P&L data available
      </div>
    );
  }

  const dataKey =
    viewMode === "cumulative"
      ? "pnl"
      : viewMode === "realized"
        ? "realized"
        : "unrealized";

  const lastVal = chartData[chartData.length - 1]?.[dataKey] ?? 0;
  const isUp = lastVal >= 0;
  const stroke = isUp ? CHART_COLORS.profit : CHART_COLORS.loss;

  const maxDrawdown = Math.min(...chartData.map((d) => d.drawdown), 0);

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Controls */}
      <div className="px-3 py-1.5 border-b border-t-border flex items-center gap-2 flex-wrap">
        <div className="flex items-center gap-1">
          {(
            [
              ["cumulative", "P&L"],
              ["realized", "Realized"],
              ["unrealized", "Unrealized"],
            ] as [ViewMode, string][]
          ).map(([mode, label]) => (
            <button
              key={mode}
              onClick={() => setViewMode(mode)}
              className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
                viewMode === mode
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2 ml-auto">
          <label className="flex items-center gap-1 text-[9px] text-txt-muted cursor-pointer">
            <input
              type="checkbox"
              checked={showDrawdown}
              onChange={(e) => setShowDrawdown(e.target.checked)}
              className="w-3 h-3 rounded border-t-border bg-t-bg accent-accent"
            />
            Drawdown
          </label>
          <label className="flex items-center gap-1 text-[9px] text-txt-muted cursor-pointer">
            <input
              type="checkbox"
              checked={showTradeMarkers}
              onChange={(e) => setShowTradeMarkers(e.target.checked)}
              className="w-3 h-3 rounded border-t-border bg-t-bg accent-accent"
            />
            Trades
          </label>
          {showDrawdown && maxDrawdown < 0 && (
            <span className="text-[9px] font-mono text-loss">
              Max DD: ${Math.abs(maxDrawdown).toFixed(2)}
            </span>
          )}
        </div>
      </div>

      <div className="p-3">
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={chartData}>
            <defs>
              <linearGradient id="gUp" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_COLORS.profit} stopOpacity={0.2} />
                <stop offset="100%" stopColor={CHART_COLORS.profit} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gDown" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_COLORS.loss} stopOpacity={0.2} />
                <stop offset="100%" stopColor={CHART_COLORS.loss} stopOpacity={0} />
              </linearGradient>
              <linearGradient id="gDrawdown" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={CHART_COLORS.loss} stopOpacity={0.08} />
                <stop offset="100%" stopColor={CHART_COLORS.loss} stopOpacity={0.02} />
              </linearGradient>
            </defs>
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
              tickFormatter={(v) => `$${v.toFixed(2)}`}
              tick={{ fill: CHART_COLORS.muted }}
              width={55}
            />
            <Tooltip
              contentStyle={TOOLTIP_STYLE}
              labelStyle={TOOLTIP_LABEL_STYLE}
              formatter={(value: number, name: string) => {
                const labels: Record<string, string> = {
                  pnl: "Cumulative P&L",
                  realized: "Realized",
                  unrealized: "Unrealized",
                  drawdown: "Drawdown",
                };
                return [`$${value.toFixed(4)}`, labels[name] ?? name];
              }}
            />
            <ReferenceLine y={0} stroke={CHART_COLORS.reference} strokeDasharray="4 4" />

            {/* Main P&L area */}
            <Area
              type="monotone"
              dataKey={dataKey}
              stroke={stroke}
              strokeWidth={1.5}
              fill={isUp ? "url(#gUp)" : "url(#gDown)"}
              dot={false}
              activeDot={{
                r: 2.5,
                fill: stroke,
                stroke: "#0f1419",
                strokeWidth: 2,
              }}
            />

            {/* Drawdown overlay */}
            {showDrawdown && (
              <Area
                type="monotone"
                dataKey="drawdown"
                stroke={CHART_COLORS.loss}
                strokeWidth={1}
                strokeDasharray="2 2"
                fill="url(#gDrawdown)"
                dot={false}
              />
            )}

            {/* Trade marker dots */}
            {showTradeMarkers && (
              <Line
                type="monotone"
                dataKey={dataKey}
                stroke="transparent"
                dot={(props: any) => {
                  const { cx, cy, payload } = props;
                  if (!payload?.ticker) return <g key={`e-${cx}-${cy}`} />;
                  const isSell = (payload.action ?? "").toLowerCase() === "sell";
                  return (
                    <circle
                      key={`t-${cx}-${cy}`}
                      cx={cx}
                      cy={cy}
                      r={3}
                      fill={isSell ? CHART_COLORS.loss : CHART_COLORS.accent}
                      stroke="#0f1419"
                      strokeWidth={1.5}
                    />
                  );
                }}
                activeDot={false}
              />
            )}

            {/* Brush for zoom */}
            {chartData.length > 10 && (
              <Brush
                dataKey="time"
                height={20}
                stroke={CHART_COLORS.reference}
                fill="#0f1419"
                travellerWidth={8}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
