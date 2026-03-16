"use client";

import { useState, useMemo } from "react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
  ReferenceLine,
} from "recharts";
import type { Trade, Market, Position } from "@/lib/api";
import { kalshiMarketUrl } from "@/lib/api";
import { pnlCls, fmtDollar, TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE, CHART_COLORS } from "@/lib/utils";

interface MarketActivity {
  marketTitle: string;
  ticker: string;
  eventTicker: string;
  trades: Trade[];
  totalQty: number;
  totalCost: number;
  currentPrice: number | null;
  currentValue: number | null;
  pnl: number | null;
  positionPnl: number | null;
  side: string;
}

export function MarketTimeline({
  trades,
  markets,
  positions,
}: {
  trades: Trade[];
  markets: Market[];
  positions: Position[];
}) {
  const [expandedMarket, setExpandedMarket] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [viewMode, setViewMode] = useState<"list" | "chart">("list");

  const marketMap = useMemo(
    () => new Map(markets.map((m) => [m.ticker, m])),
    [markets]
  );

  const positionMap = useMemo(
    () => new Map(positions.map((p) => [p.ticker ?? p.market_id, p])),
    [positions]
  );

  const marketActivities: MarketActivity[] = useMemo(() => {
    const grouped = new Map<string, Trade[]>();
    for (const trade of trades) {
      if (trade.status !== "FILLED" && trade.status !== "DRY_RUN") continue;
      const existing = grouped.get(trade.ticker);
      if (existing) existing.push(trade);
      else grouped.set(trade.ticker, [trade]);
    }

    const activities: MarketActivity[] = [];
    const groupedEntries = Array.from(grouped.entries());
    for (const [ticker, tickerTrades] of groupedEntries) {
      const sorted = [...tickerTrades].sort(
        (a, b) =>
          new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
      );

      const mkt = marketMap.get(ticker);
      const pos = positionMap.get(ticker);
      // Compute net position accounting for BUY/SELL and side changes.
      // Track YES and NO quantities separately, then derive net.
      let yesQty = 0;
      let noQty = 0;
      let totalCost = 0;
      for (const t of sorted) {
        const qty = t.filled_shares || t.count;
        const isSell = t.action?.toUpperCase() === "SELL";
        const tradeSide = t.side.toLowerCase();
        if (tradeSide === "yes") {
          yesQty += isSell ? -qty : qty;
        } else {
          noQty += isSell ? -qty : qty;
        }
        // Cost: buys add cost, sells subtract
        const cost = (t.price_cents / 100) * qty;
        totalCost += isSell ? -cost : cost;
      }

      // Net position: whichever side has positive contracts
      const side = yesQty > 0 ? "yes" : noQty > 0 ? "no" : (sorted[sorted.length - 1]?.side.toLowerCase() ?? "yes");
      const totalQty = Math.max(yesQty, noQty, 0);

      const currentPrice =
        mkt != null
          ? side === "yes"
            ? mkt.yes_ask
            : mkt.no_ask
          : null;
      const currentValue =
        currentPrice != null ? currentPrice * totalQty : null;
      const pnl = currentValue != null ? currentValue - Math.max(0, totalCost) : null;

      activities.push({
        marketTitle: sorted[0]?.market_title ?? mkt?.title ?? ticker,
        ticker,
        eventTicker: mkt?.event_ticker ?? "",
        trades: sorted,
        totalQty,
        totalCost,
        currentPrice,
        currentValue,
        pnl,
        positionPnl: pos ? pos.realized_pnl + pos.unrealized_pnl : null,
        side,
      });
    }

    activities.sort((a, b) => {
      const lastA = new Date(
        a.trades[a.trades.length - 1].created_at
      ).getTime();
      const lastB = new Date(
        b.trades[b.trades.length - 1].created_at
      ).getTime();
      return lastB - lastA;
    });

    return activities;
  }, [trades, marketMap, positionMap]);

  const filtered = useMemo(() => {
    if (!filter) return marketActivities;
    const q = filter.toLowerCase();
    return marketActivities.filter(
      (a) =>
        a.marketTitle.toLowerCase().includes(q) ||
        a.ticker.toLowerCase().includes(q)
    );
  }, [marketActivities, filter]);

  if (marketActivities.length === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No market activity yet
      </div>
    );
  }

  return (
    <div className="bg-t-panel border border-t-border rounded overflow-hidden">
      {/* Header with filter and view toggle */}
      <div className="px-3 py-1.5 border-b border-t-border flex items-center gap-2">
        <input
          type="text"
          placeholder="Filter markets..."
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          className="bg-t-bg border border-t-border rounded px-2 py-1 text-[10px] text-txt-primary placeholder-txt-muted focus:border-accent/50 focus:outline-none w-48 font-mono"
        />
        <div className="flex items-center gap-1 ml-2">
          <button
            onClick={() => setViewMode("list")}
            className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
              viewMode === "list"
                ? "bg-accent/20 text-accent"
                : "text-txt-muted hover:text-txt-secondary"
            }`}
          >
            Timeline
          </button>
          <button
            onClick={() => setViewMode("chart")}
            className={`px-2 py-0.5 rounded text-[9px] font-medium transition-colors ${
              viewMode === "chart"
                ? "bg-accent/20 text-accent"
                : "text-txt-muted hover:text-txt-secondary"
            }`}
          >
            Chart
          </button>
        </div>
        <span className="text-[9px] text-txt-muted ml-auto font-mono">
          {filtered.length} markets
        </span>
      </div>

      {viewMode === "chart" ? (
        <MarketChartView activities={filtered} />
      ) : (
        <div className="max-h-[500px] overflow-y-auto divide-y divide-t-border/40">
          {filtered.map((activity) => {
            const isExpanded = expandedMarket === activity.ticker;
            const displayPnl = activity.positionPnl ?? activity.pnl;

            return (
              <div key={activity.ticker}>
                {/* Market summary row */}
                <div
                  className="px-3 py-2.5 hover:bg-t-panel-hover transition-colors cursor-pointer flex items-center gap-3"
                  onClick={() =>
                    setExpandedMarket(isExpanded ? null : activity.ticker)
                  }
                >
                  <span className="text-[9px] text-txt-muted w-3 flex-shrink-0">
                    {isExpanded ? "\u25BC" : "\u25B6"}
                  </span>

                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      {activity.eventTicker ? (
                        <a
                          href={kalshiMarketUrl(activity.eventTicker)}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-xs font-medium text-txt-primary hover:text-accent truncate"
                          onClick={(e) => e.stopPropagation()}
                        >
                          {activity.marketTitle}
                        </a>
                      ) : (
                        <span className="text-xs font-medium text-txt-primary truncate">
                          {activity.marketTitle}
                        </span>
                      )}
                      <span
                        className={`inline-block px-1.5 py-px rounded text-[8px] font-bold tracking-wider flex-shrink-0 ${
                          activity.side === "yes"
                            ? "bg-profit-dim text-profit"
                            : "bg-loss-dim text-loss"
                        }`}
                      >
                        {activity.side.toUpperCase()}
                      </span>
                    </div>
                    <div className="text-[9px] text-txt-muted font-mono mt-0.5">
                      {activity.ticker} · {activity.trades.length} trade
                      {activity.trades.length !== 1 ? "s" : ""}
                    </div>
                  </div>

                  <div className="flex items-center gap-4 flex-shrink-0 text-right">
                    <div>
                      <div className="text-[9px] text-txt-muted">Qty</div>
                      <div className="text-xs font-mono text-txt-primary">
                        {activity.totalQty}
                      </div>
                    </div>
                    <div>
                      <div className="text-[9px] text-txt-muted">Cost</div>
                      <div className="text-xs font-mono text-txt-primary">
                        {fmtDollar(activity.totalCost)}
                      </div>
                    </div>
                    <div>
                      <div className="text-[9px] text-txt-muted">Mkt</div>
                      <div className="text-xs font-mono text-txt-primary">
                        {activity.currentPrice != null
                          ? `${(activity.currentPrice * 100).toFixed(0)}c`
                          : "--"}
                      </div>
                    </div>
                    <div className="min-w-[60px]">
                      <div className="text-[9px] text-txt-muted">P&L</div>
                      <div
                        className={`text-xs font-mono font-medium ${
                          displayPnl != null
                            ? pnlCls(displayPnl)
                            : "text-txt-muted"
                        }`}
                      >
                        {displayPnl != null ? fmtDollar(displayPnl) : "--"}
                      </div>
                    </div>
                  </div>
                </div>

                {/* Expanded trade timeline */}
                {isExpanded && (
                  <ExpandedTimeline activity={activity} />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ── Expanded timeline with probabilities ── */

function ExpandedTimeline({ activity }: { activity: MarketActivity }) {
  const displayPnl = activity.positionPnl ?? activity.pnl;

  return (
    <div className="bg-t-bg border-t border-t-border/40 px-4 py-2">
      <div className="relative pl-4">
        <div className="absolute left-[5px] top-2 bottom-2 w-px bg-t-border" />

        {activity.trades.map((trade, idx) => {
          const qty = trade.filled_shares || trade.count;
          const isSell = trade.action?.toUpperCase() === "SELL";
          const cost = (trade.price_cents / 100) * qty;

          // Cumulative position accounting for buys/sells and side changes
          let cumYes = 0, cumNo = 0, cumCost = 0;
          for (let i = 0; i <= idx; i++) {
            const t = activity.trades[i];
            const tQty = t.filled_shares || t.count;
            const tSell = t.action?.toUpperCase() === "SELL";
            const tCost = (t.price_cents / 100) * tQty;
            if (t.side.toLowerCase() === "yes") {
              cumYes += tSell ? -tQty : tQty;
            } else {
              cumNo += tSell ? -tQty : tQty;
            }
            cumCost += tSell ? -tCost : tCost;
          }
          const cumulativeQty = Math.max(cumYes, cumNo, 0);
          const cumulativeCost = Math.max(0, cumCost);

          const pred = trade.prediction;

          return (
            <div
              key={trade.id}
              className="relative flex items-start gap-3 py-1.5"
            >
              <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-accent border-2 border-t-bg z-10" />

              <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
                {new Date(trade.created_at).toLocaleString("en-US", {
                  month: "short",
                  day: "numeric",
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </div>

              <div className="flex flex-wrap items-center gap-3 flex-1 text-[10px] font-mono">
                {isSell && (
                  <span className="text-[9px] px-1 py-px rounded font-bold bg-warn-dim text-warn">
                    SELL
                  </span>
                )}
                <span
                  className={`text-[9px] px-1 py-px rounded font-bold ${
                    trade.side.toLowerCase() === "yes"
                      ? "bg-profit-dim text-profit"
                      : "bg-loss-dim text-loss"
                  }`}
                >
                  {trade.side.toUpperCase()}
                </span>
                <span className="text-txt-primary">
                  {isSell ? "-" : "+"}{qty} @ {trade.price_cents}c
                </span>
                <span className="text-txt-muted">
                  {isSell ? "proceeds" : "cost"}: ${cost.toFixed(2)}
                </span>
                <span className="text-txt-muted">
                  total: {cumulativeQty} · ${cumulativeCost.toFixed(2)}
                </span>
                {/* Market & Model probabilities at trade time */}
                {pred && (
                  <>
                    <span className="text-txt-secondary">
                      mkt: {(pred.yes_ask * 100).toFixed(0)}c
                    </span>
                    <span className="text-accent">
                      model: {(pred.p_yes * 100).toFixed(0)}%
                    </span>
                    <span className={pnlCls(pred.p_yes - pred.yes_ask)}>
                      edge: {pred.p_yes - pred.yes_ask >= 0 ? "+" : ""}
                      {((pred.p_yes - pred.yes_ask) * 100).toFixed(0)}pp
                    </span>
                  </>
                )}
                <span
                  className={`text-[9px] px-1 py-px rounded font-bold ${
                    trade.dry_run
                      ? "bg-warn-dim text-warn"
                      : "bg-profit-dim text-profit"
                  }`}
                >
                  {trade.dry_run ? "DRY" : "LIVE"}
                </span>
              </div>
            </div>
          );
        })}

        {/* Current state summary */}
        <div className="relative flex items-start gap-3 py-1.5 mt-1 border-t border-t-border/30">
          <div className="absolute left-[-12px] top-[8px] w-[7px] h-[7px] rounded-full bg-txt-secondary border-2 border-t-bg z-10" />
          <div className="text-[9px] text-txt-muted font-mono whitespace-nowrap w-[100px] flex-shrink-0">
            NOW
          </div>
          <div className="flex items-center gap-3 text-[10px] font-mono">
            {activity.totalQty > 0 ? (
              <>
                <span className="text-txt-primary">
                  Holding {activity.totalQty}{" "}
                  <span className={activity.side === "yes" ? "text-profit" : "text-loss"}>
                    {activity.side.toUpperCase()}
                  </span>
                </span>
                <span className="text-txt-muted">
                  avg:{" "}
                  {activity.totalCost > 0
                    ? `${((activity.totalCost / activity.totalQty) * 100).toFixed(0)}c`
                    : "--"}
                </span>
                <span className="text-txt-muted">
                  mkt:{" "}
                  {activity.currentPrice != null
                    ? `${(activity.currentPrice * 100).toFixed(0)}c`
                    : "--"}
                </span>
              </>
            ) : (
              <span className="text-txt-muted">No open position</span>
            )}
            {displayPnl != null && (
              <span className={`font-medium ${pnlCls(displayPnl)}`}>
                P&L: {fmtDollar(displayPnl)}
              </span>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ── Chart view ── */

interface ChartPoint {
  time: string;
  timestamp: number;
  cumulativeQty: number;
  avgPrice: number;
  mktPrice: number | null;
  modelProb: number | null;
  edge: number | null;
}

function MarketChartView({ activities }: { activities: MarketActivity[] }) {
  const [selectedTicker, setSelectedTicker] = useState<string | null>(
    activities[0]?.ticker ?? null
  );

  const activity = activities.find((a) => a.ticker === selectedTicker);

  const chartData: ChartPoint[] = useMemo(() => {
    if (!activity) return [];
    const points: ChartPoint[] = [];
    let cumQty = 0;
    let cumCost = 0;

    for (const trade of activity.trades) {
      const qty = trade.filled_shares || trade.count;
      cumQty += qty;
      cumCost += (trade.price_cents / 100) * trade.count;
      const avgPrice = cumQty > 0 ? (cumCost / cumQty) * 100 : 0;

      const pred = trade.prediction;
      points.push({
        time: new Date(trade.created_at).toLocaleString("en-US", {
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        }),
        timestamp: new Date(trade.created_at).getTime(),
        cumulativeQty: cumQty,
        avgPrice: Math.round(avgPrice),
        mktPrice: pred ? Math.round(pred.yes_ask * 100) : null,
        modelProb: pred ? Math.round(pred.p_yes * 100) : null,
        edge: pred ? Math.round((pred.p_yes - pred.yes_ask) * 100) : null,
      });
    }

    // Add current state
    if (activity.currentPrice != null) {
      points.push({
        time: "Now",
        timestamp: Date.now(),
        cumulativeQty: cumQty,
        avgPrice: cumQty > 0 ? Math.round((cumCost / cumQty) * 100) : 0,
        mktPrice: Math.round(activity.currentPrice * 100),
        modelProb: null,
        edge: null,
      });
    }

    return points;
  }, [activity]);

  if (activities.length === 0) {
    return (
      <div className="p-8 text-center text-txt-muted text-xs">
        No data for chart
      </div>
    );
  }

  return (
    <div className="p-3">
      {/* Market selector */}
      <div className="flex items-center gap-2 mb-3 flex-wrap">
        {activities.map((a) => (
          <button
            key={a.ticker}
            onClick={() => setSelectedTicker(a.ticker)}
            className={`px-2 py-1 rounded text-[9px] font-mono transition-colors ${
              selectedTicker === a.ticker
                ? "bg-accent/20 text-accent"
                : "bg-t-bg text-txt-muted hover:text-txt-secondary border border-t-border"
            }`}
          >
            {a.ticker}
          </button>
        ))}
      </div>

      {activity && chartData.length > 0 ? (
        <div className="space-y-3">
          {/* Price & Probability chart */}
          <div>
            <div className="text-[9px] text-txt-muted uppercase tracking-widest mb-1 font-medium">
              Market Price vs Model Probability (cents / %)
            </div>
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={chartData}>
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
                  width={35}
                  tickFormatter={(v) => `${v}`}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  formatter={(value: number, name: string) => {
                    const labels: Record<string, string> = {
                      mktPrice: "Market",
                      modelProb: "Model",
                      avgPrice: "Avg Entry",
                    };
                    return [`${value}c`, labels[name] ?? name];
                  }}
                />
                <Line
                  type="monotone"
                  dataKey="avgPrice"
                  stroke={CHART_COLORS.muted}
                  strokeWidth={1}
                  strokeDasharray="4 4"
                  dot={false}
                  name="avgPrice"
                />
                <Line
                  type="monotone"
                  dataKey="mktPrice"
                  stroke={CHART_COLORS.loss}
                  strokeWidth={1.5}
                  dot={{ r: 2, fill: CHART_COLORS.loss }}
                  connectNulls
                  name="mktPrice"
                />
                <Line
                  type="monotone"
                  dataKey="modelProb"
                  stroke={CHART_COLORS.accent}
                  strokeWidth={1.5}
                  dot={{ r: 2, fill: CHART_COLORS.accent }}
                  connectNulls
                  name="modelProb"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Position size chart */}
          <div>
            <div className="text-[9px] text-txt-muted uppercase tracking-widest mb-1 font-medium">
              Cumulative Position (contracts)
            </div>
            <ResponsiveContainer width="100%" height={100}>
              <LineChart data={chartData}>
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
                  width={35}
                />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  formatter={(value: number) => [
                    `${value} contracts`,
                    "Position",
                  ]}
                />
                <ReferenceLine y={0} stroke={CHART_COLORS.reference} />
                <Line
                  type="stepAfter"
                  dataKey="cumulativeQty"
                  stroke={CHART_COLORS.profit}
                  strokeWidth={1.5}
                  dot={{ r: 2, fill: CHART_COLORS.profit }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </div>
      ) : (
        <div className="p-8 text-center text-txt-muted text-xs">
          Select a market to view chart
        </div>
      )}
    </div>
  );
}
