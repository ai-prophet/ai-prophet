"use client";

import { useState, useMemo, useEffect } from "react";
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

type ViewMode = "pnl" | "open_value" | "cash_pnl";
type TimeFrame = "1h" | "1d" | "1w" | "all";

const TIME_FRAME_MS: Record<TimeFrame, number | null> = {
  "1h": 60 * 60 * 1000,
  "1d": 24 * 60 * 60 * 1000,
  "1w": 7 * 24 * 60 * 60 * 1000,
  "all": null,
};

const TIME_FRAME_LABELS: Record<TimeFrame, string> = {
  "1h": "1H",
  "1d": "1D",
  "1w": "1W",
  "all": "All",
};

const VIEW_LABELS: Record<ViewMode, string> = {
  pnl: "P&L",
  open_value: "Open Value",
  cash_pnl: "Cash P&L",
};

const TOOLTIP_LABELS: Record<string, string> = {
  pnl: "Net P&L",
  open_value: "Open Value",
  cash_pnl: "Cash P&L",
  drawdown: "Drawdown",
};

export function PnLChart({
  data,
  tradeMarkers = [],
}: {
  data: PnLPoint[];
  tradeMarkers?: TradeMarker[];
}) {
  const [viewMode, setViewMode] = useState<ViewMode>("pnl");
  const [showDrawdown, setShowDrawdown] = useState(false);
  const [showTradeMarkers, setShowTradeMarkers] = useState(true);
  const [timeFrame, setTimeFrame] = useState<TimeFrame>("all");
  const [brushRange, setBrushRange] = useState<{ startIndex: number; endIndex: number } | null>(null);
  const [groupByCycle, setGroupByCycle] = useState(true);

  // Reset brush when timeframe changes
  useEffect(() => { setBrushRange(null); }, [timeFrame]);

  const handleTimeFrameChange = (nextTimeFrame: TimeFrame) => {
    if (nextTimeFrame === "all") {
      setBrushRange(null);
    }
    setTimeFrame(nextTimeFrame);
  };

  const chartData = useMemo(() => {
    if (data.length === 0) return [];

    // If grouping by cycle, aggregate trades within each hour
    if (groupByCycle) {
      const cycleMap = new Map<number, {
        timestamp: number;
        pnl: number;
        cash_pnl: number;
        open_value: number;
        tradeCount: number;
        trades: typeof data;
      }>();

      // Group all data points by cycle (hourly boundaries)
      data.forEach((d) => {
        const ts = new Date(d.timestamp).getTime();
        // Round to nearest hour for cycle grouping
        const cycleTs = Math.floor(ts / (60 * 60 * 1000)) * (60 * 60 * 1000);

        if (!cycleMap.has(cycleTs)) {
          cycleMap.set(cycleTs, {
            timestamp: cycleTs,
            pnl: 0,
            cash_pnl: 0,
            open_value: 0,
            tradeCount: 0,
            trades: [],
          });
        }

        const cycle = cycleMap.get(cycleTs)!;
        // For each cycle, use the LAST values within that cycle
        // (as they represent the cumulative state at cycle end)
        cycle.pnl = d.pnl ?? 0;
        cycle.cash_pnl = d.cash_pnl ?? d.realized_pnl ?? 0;
        cycle.open_value = d.open_value ?? 0;
        cycle.tradeCount++;
        cycle.trades.push(d);
      });

      // Convert to array and sort by timestamp
      const cycles = Array.from(cycleMap.values()).sort((a, b) => a.timestamp - b.timestamp);

      let peak = 0;
      return cycles.map((cycle) => {
        if (cycle.pnl > peak) peak = cycle.pnl;
        const drawdown = peak > 0 ? cycle.pnl - peak : 0;

        return {
          time: new Date(cycle.timestamp).toLocaleDateString("en-US", {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          }),
          timestamp: cycle.timestamp,
          pnl: cycle.pnl,
          cash_pnl: cycle.cash_pnl,
          open_value: cycle.open_value,
          drawdown,
          tradeCount: cycle.tradeCount,
          // Keep first trade info for tooltip
          tradeCost: cycle.trades[0]?.trade_cost,
          ticker: cycle.trades[0]?.ticker,
          side: cycle.trades[0]?.side,
          action: cycle.trades[0]?.action ?? "BUY",
        };
      });
    }

    // Original per-trade data
    let peak = 0;
    return data.map((d) => {
      const pnl = d.pnl ?? 0;
      const cashPnl = d.cash_pnl ?? d.realized_pnl ?? 0;
      const openValue = d.open_value ?? 0;

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
        cash_pnl: cashPnl,
        open_value: openValue,
        drawdown,
        tradeCost: d.trade_cost,
        ticker: d.ticker,
        side: d.side,
        action: d.action ?? "BUY",
      };
    });
  }, [data, groupByCycle]);

  // Filter by selected timeframe
  const filteredData = useMemo(() => {
    const windowMs = TIME_FRAME_MS[timeFrame];
    if (windowMs === null) return chartData;
    const cutoff = Date.now() - windowMs;
    const filtered = chartData.filter((d) => d.timestamp >= cutoff);
    // Always include at least the last point so chart isn't empty
    return filtered.length > 0 ? filtered : chartData.slice(-1);
  }, [chartData, timeFrame]);

  if (data.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-10 text-center text-txt-muted text-xs">
        No P&L data available
      </div>
    );
  }

  const dataKey = viewMode;

  const lastVal = filteredData[filteredData.length - 1]?.[dataKey] ?? 0;
  const isUp = lastVal >= 0;
  const stroke = isUp ? CHART_COLORS.profit : CHART_COLORS.loss;

  const maxDrawdown = Math.min(...filteredData.map((d) => d.drawdown), 0);

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Controls */}
      <div className="px-3 py-1.5 border-b border-t-border flex items-center gap-2 flex-wrap">
        {/* View mode */}
        <div className="flex items-center gap-1">
          {(Object.entries(VIEW_LABELS) as [ViewMode, string][]).map(([mode, label]) => (
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

        {/* Timeframe buttons */}
        <div className="flex items-center gap-1 border-l border-t-border pl-2">
          {(["1h", "1d", "1w", "all"] as TimeFrame[]).map((tf) => (
            <button
              key={tf}
              onClick={() => handleTimeFrameChange(tf)}
              className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
                timeFrame === tf
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {TIME_FRAME_LABELS[tf]}
            </button>
          ))}
          {timeFrame !== "all" && (
            <button
              onClick={() => handleTimeFrameChange("all")}
              className="px-1.5 py-0.5 rounded text-[8px] font-medium text-txt-muted hover:text-accent border border-t-border transition-colors ml-1"
              title="Reset to all time"
            >
              ↺
            </button>
          )}
        </div>

        <div className="flex items-center gap-2 ml-auto">
          {/* Current terminal value */}
          <span className={`text-[10px] font-mono font-semibold ${isUp ? "text-profit" : "text-loss"}`}>
            {lastVal >= 0 ? "+" : ""}${lastVal.toFixed(2)}
          </span>
          <label className="flex items-center gap-1 text-[9px] text-txt-muted cursor-pointer">
            <input
              type="checkbox"
              checked={groupByCycle}
              onChange={(e) => setGroupByCycle(e.target.checked)}
              className="w-3 h-3 rounded border-t-border bg-t-bg accent-accent"
            />
            Per Cycle
          </label>
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
          {showTradeMarkers && (
            <div className="flex items-center gap-2 text-[9px] text-txt-muted border-l border-t-border pl-2">
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded-full bg-accent" />
                Buy
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-2 h-2 rounded-full bg-loss" />
                Sell
              </span>
            </div>
          )}
          {showDrawdown && maxDrawdown < 0 && (
            <span className="text-[9px] font-mono text-loss">
              Max DD: ${Math.abs(maxDrawdown).toFixed(2)}
            </span>
          )}
        </div>
      </div>

      <div className="p-3">
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={filteredData}>
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
                return [`$${value.toFixed(4)}`, TOOLTIP_LABELS[name] ?? name];
              }}
              content={(props: any) => {
                if (!props.active || !props.payload?.[0]) return null;
                const data = props.payload[0].payload;

                return (
                  <div style={TOOLTIP_STYLE}>
                    <p style={TOOLTIP_LABEL_STYLE}>{props.label}</p>
                    {props.payload.map((entry: any, index: number) => (
                      <p key={index} style={{ color: entry.color || "#ccc", fontSize: 11, margin: "2px 0" }}>
                        {TOOLTIP_LABELS[entry.name] ?? entry.name}: ${entry.value.toFixed(4)}
                      </p>
                    ))}
                    {groupByCycle && data.tradeCount && (
                      <p style={{ color: "#888", fontSize: 10, marginTop: 4, borderTop: "1px solid #333", paddingTop: 4 }}>
                        Trades in cycle: {data.tradeCount}
                      </p>
                    )}
                  </div>
                );
              }}
            />
            <ReferenceLine y={0} stroke={CHART_COLORS.reference} strokeDasharray="4 4" />

            {/* Main P&L area */}
            <Area
              type={groupByCycle ? "stepAfter" : "monotone"}
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
                  if (groupByCycle) {
                    // For cycle view, show dots for all cycles with trades
                    if (!payload?.tradeCount) return <g key={`e-${cx}-${cy}`} />;
                    return (
                      <g key={`c-${cx}-${cy}`}>
                        <circle
                          cx={cx}
                          cy={cy}
                          r={4}
                          fill={CHART_COLORS.accent}
                          stroke="#0f1419"
                          strokeWidth={1.5}
                          opacity={0.8}
                        />
                        {payload.tradeCount > 1 && (
                          <text
                            x={cx}
                            y={cy - 8}
                            fill={CHART_COLORS.muted}
                            fontSize={8}
                            textAnchor="middle"
                          >
                            {payload.tradeCount}
                          </text>
                        )}
                      </g>
                    );
                  }
                  // Original per-trade dots
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

            {/* Brush for fine-grained zoom within selected window */}
            {filteredData.length > 10 && (
              <Brush
                dataKey="time"
                height={20}
                stroke={CHART_COLORS.reference}
                fill="#0f1419"
                travellerWidth={8}
                startIndex={brushRange?.startIndex ?? 0}
                endIndex={brushRange?.endIndex ?? filteredData.length - 1}
                onChange={(range) => {
                  if (range.startIndex != null && range.endIndex != null) {
                    setBrushRange({ startIndex: range.startIndex, endIndex: range.endIndex });
                  }
                }}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
