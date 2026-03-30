"use client";

import { useMemo, useState } from "react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
  ReferenceLine,
  CartesianGrid,
} from "recharts";
import type { ResolvedMarketsData, ResolvedMarketRow, MarketTrade } from "@/lib/api";
import { pnlCls, fmtDollar, fmtTime, TOOLTIP_STYLE, TOOLTIP_LABEL_STYLE } from "@/lib/utils";

function StatCard({
  label,
  value,
  sub,
  valueClass,
}: {
  label: string;
  value: string;
  sub?: string;
  valueClass?: string;
}) {
  return (
    <div className="flex flex-col gap-1 p-3 rounded border border-t-border/60 bg-t-bg/30">
      <span className="text-[9px] text-txt-muted uppercase tracking-wider">{label}</span>
      <span className={`text-xl font-mono font-semibold ${valueClass ?? "text-txt-primary"}`}>
        {value}
      </span>
      {sub && <span className="text-[9px] font-mono text-txt-muted">{sub}</span>}
    </div>
  );
}

const OUTCOME_COLOR: Record<string, string> = {
  profit: "#22c55e",
  loss: "#ef4444",
  neutral: "#6b7280",
};

export function ModelCalibration({
  resolvedMarkets,
}: {
  resolvedMarkets: ResolvedMarketsData | null;
}) {
  const [view, setView] = useState<"table" | "chart">("table");
  const [filter, setFilter] = useState<"all" | "positions">("positions");

  const rows = useMemo(() => {
    if (!resolvedMarkets) return [];
    const base = filter === "positions"
      ? resolvedMarkets.markets.filter((r) => r.position_side !== null)
      : resolvedMarkets.markets;
    return [...base].sort((a, b) => b.pnl - a.pnl);
  }, [resolvedMarkets, filter]);

  const chartData = useMemo(() => {
    return rows
      .filter((r) => r.position_side !== null)
      .map((r) => ({
        label: r.ticker || r.market_id,
        title: r.title,
        pnl: r.pnl,
        outcome: r.outcome,
        side: r.position_side,
      }))
      .slice(0, 30); // cap at 30 bars
  }, [rows]);

  if (!resolvedMarkets || resolvedMarkets.summary.total_markets === 0) {
    return (
      <div className="bg-t-panel border border-t-border rounded p-8 text-center text-txt-muted text-xs">
        No resolved markets yet
      </div>
    );
  }

  const { summary } = resolvedMarkets;

  return (
    <div className="bg-t-panel border border-t-border rounded">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-t-border">
        <h3 className="text-xs font-medium text-txt-secondary uppercase tracking-widest">
          Resolved Markets
        </h3>
        <div className="flex gap-1">
          {(["table", "chart"] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${
                view === v
                  ? "bg-accent/20 text-accent"
                  : "text-txt-muted hover:text-txt-secondary"
              }`}
            >
              {v === "table" ? "Table" : "P&L Chart"}
            </button>
          ))}
        </div>
      </div>

      {/* Summary stat cards */}
      <div className="grid grid-cols-5 gap-2 p-3 border-b border-t-border/50">
        <StatCard
          label="Realized P&L"
          value={fmtDollar(summary.total_pnl)}
          sub={`${summary.markets_with_position} traded markets`}
          valueClass={summary.total_pnl > 0 ? "text-profit" : summary.total_pnl < 0 ? "text-loss" : "text-txt-primary"}
        />
        <StatCard
          label="Win Rate"
          value={summary.markets_with_position > 0 ? `${summary.win_rate.toFixed(1)}%` : "—"}
          sub={`${summary.win_count}W · ${summary.loss_count}L`}
          valueClass={summary.win_rate >= 50 ? "text-profit" : "text-loss"}
        />
        <StatCard
          label="Capital Deployed"
          value={fmtDollar(summary.total_capital)}
          sub={`${summary.total_markets} total resolved`}
        />
        <StatCard
          label="Return"
          value={
            summary.total_capital > 0
              ? `${((summary.total_pnl / summary.total_capital) * 100).toFixed(1)}%`
              : "—"
          }
          sub="on deployed capital"
          valueClass={
            summary.total_pnl > 0 ? "text-profit" : summary.total_pnl < 0 ? "text-loss" : "text-txt-primary"
          }
        />
        <StatCard
          label="Brier Score"
          value={summary.brier_score !== undefined ? summary.brier_score.toFixed(3) : "—"}
          sub={
            summary.market_baseline_brier !== undefined
              ? `vs ${summary.market_baseline_brier.toFixed(3)} market`
              : undefined
          }
          valueClass={
            summary.brier_score !== undefined && summary.market_baseline_brier !== undefined
              ? summary.brier_score < summary.market_baseline_brier
                ? "text-profit"
                : "text-loss"
              : "text-txt-primary"
          }
        />
      </div>

      {/* Filter bar */}
      <div className="px-3 py-1.5 border-b border-t-border/40 flex items-center gap-2">
        {(["positions", "all"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-2 py-0.5 text-[9px] rounded transition-colors ${
              filter === f
                ? "bg-accent/20 text-accent"
                : "text-txt-muted hover:text-txt-secondary"
            }`}
          >
            {f === "positions" ? "With Positions" : "All Resolved"}
          </button>
        ))}
        <span className="ml-auto text-[9px] font-mono text-txt-muted">{rows.length} markets</span>
      </div>

      {/* Explanation note */}
      <div className="mx-3 mt-2 p-2 rounded bg-t-bg/50 border border-t-border/30 text-[9px] text-txt-muted">
        <span className="text-txt-secondary">Note:</span> P&L can be positive even with incorrect predictions if you sold early at a profit,
        or negative despite correct predictions if you bought at unfavorable prices. The ✓/✗ shows whether your final position
        matched the market outcome, not whether you made money.
      </div>

      {view === "table" ? (
        <div className="overflow-x-auto">
          {rows.length === 0 ? (
            <div className="text-center text-txt-muted text-[10px] py-8">
              No markets match this filter
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-t-border text-txt-muted text-[9px] uppercase tracking-widest">
                  <th className="px-3 py-2 text-left font-medium">Market</th>
                  <th className="px-3 py-2 text-center font-medium">Outcome</th>
                  <th className="px-3 py-2 text-center font-medium">Our Side</th>
                  <th className="px-3 py-2 text-right font-medium">Qty</th>
                  <th className="px-3 py-2 text-right font-medium">Avg Entry</th>
                  <th className="px-3 py-2 text-right font-medium">Capital</th>
                  <th className="px-3 py-2 text-right font-medium">P&L</th>
                  <th className="px-3 py-2 text-right font-medium">Return</th>
                  <th className="px-3 py-2 text-right font-medium">Resolved</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-t-border/40">
                {rows.map((row) => (
                  <ResolvedRow key={row.market_id} row={row} />
                ))}
              </tbody>
            </table>
          )}
        </div>
      ) : (
        <div className="p-3">
          {chartData.length === 0 ? (
            <div className="text-center text-txt-muted text-[10px] py-8">
              No position data to chart
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={220}>
              <BarChart
                data={chartData}
                margin={{ top: 8, right: 8, bottom: 40, left: 8 }}
                barCategoryGap="30%"
              >
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
                <XAxis
                  dataKey="label"
                  stroke="transparent"
                  tick={{ fill: "#6b7280", fontSize: 8 }}
                  tickLine={false}
                  angle={-45}
                  textAnchor="end"
                  interval={0}
                />
                <YAxis
                  stroke="transparent"
                  tick={{ fill: "#6b7280", fontSize: 9 }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `$${v >= 0 ? "+" : ""}${v.toFixed(2)}`}
                  width={52}
                />
                <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
                <Tooltip
                  contentStyle={TOOLTIP_STYLE}
                  labelStyle={TOOLTIP_LABEL_STYLE}
                  cursor={{ fill: "rgba(255,255,255,0.04)" }}
                  content={({ active, payload }) => {
                    if (!active || !payload?.length) return null;
                    const d = payload[0].payload as typeof chartData[0];
                    return (
                      <div style={TOOLTIP_STYLE} className="text-[10px] font-mono space-y-0.5 max-w-[200px]">
                        <div className="text-txt-secondary truncate">{d.title}</div>
                        <div>Outcome: <span className="text-txt-primary">{d.outcome}</span></div>
                        <div>Side: <span className="text-txt-primary">{d.side}</span></div>
                        <div>
                          P&L:{" "}
                          <span className={d.pnl >= 0 ? "text-profit" : "text-loss"}>
                            {fmtDollar(d.pnl)}
                          </span>
                        </div>
                      </div>
                    );
                  }}
                />
                <Bar dataKey="pnl" radius={[2, 2, 0, 0]}>
                  {chartData.map((entry, i) => (
                    <Cell
                      key={i}
                      fill={entry.pnl > 0 ? OUTCOME_COLOR.profit : entry.pnl < 0 ? OUTCOME_COLOR.loss : OUTCOME_COLOR.neutral}
                      fillOpacity={0.85}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      )}
    </div>
  );
}

function ResolvedRow({ row }: { row: ResolvedMarketRow }) {
  const [expanded, setExpanded] = useState(false);
  const hasPos = row.position_side !== null;
  const correct = row.correct;
  const hasTrades = row.trades && row.trades.length > 0;

  return (
    <>
      <tr
        className={`hover:bg-t-panel-hover transition-colors ${hasTrades ? "cursor-pointer" : ""}`}
        onClick={() => hasTrades && setExpanded(!expanded)}
        title={hasTrades ? "Click to view trade history" : ""}
      >
      <td className="px-3 py-2 max-w-[260px]">
        <div className="truncate text-txt-primary text-[11px]">{row.title}</div>
        <div className="text-[9px] font-mono text-txt-muted mt-0.5">
          {row.ticker}
          {row.category && (
            <span className="ml-1 px-1 py-px rounded bg-t-panel-alt border border-t-border/60 uppercase tracking-wider text-[8px]">
              {row.category}
            </span>
          )}
        </div>
      </td>
      <td className="px-3 py-2 text-center">
        <span
          className={`px-1.5 py-0.5 rounded text-[9px] font-medium font-mono ${
            row.outcome === "YES" ? "bg-profit/15 text-profit" : "bg-loss/15 text-loss"
          }`}
        >
          {row.outcome}
        </span>
      </td>
      <td className="px-3 py-2 text-center">
        {hasPos ? (
          <span className="flex items-center justify-center gap-1">
            <span
              className={`px-1.5 py-0.5 rounded text-[9px] font-medium font-mono ${
                row.position_side === "YES" ? "bg-blue-500/15 text-blue-400" : "bg-amber-500/15 text-amber-400"
              }`}
              title={`We held ${row.position_side}, market resolved to ${row.outcome}`}
            >
              {row.position_side}
            </span>
            {correct !== null && (
              <span
                className={`text-[9px] ${correct ? "text-profit" : "text-loss"}`}
                title={correct ? "Correct prediction" : "Incorrect prediction"}
              >
                {correct ? "✓" : "✗"}
              </span>
            )}
          </span>
        ) : (
          <span className="text-txt-muted text-[9px]">—</span>
        )}
      </td>
      <td className="px-3 py-2 text-right font-mono text-txt-secondary text-[11px]">
        {hasPos ? row.quantity : "—"}
      </td>
      <td className="px-3 py-2 text-right font-mono text-txt-secondary text-[11px]">
        {hasPos ? `${(row.avg_price * 100).toFixed(0)}¢` : "—"}
      </td>
      <td className="px-3 py-2 text-right font-mono text-txt-secondary text-[11px]">
        {hasPos ? fmtDollar(row.capital) : "—"}
      </td>
      <td
        className={`px-3 py-2 text-right font-mono font-medium text-[11px] ${hasPos ? pnlCls(row.pnl) : "text-txt-muted"}`}
        title={
          hasPos && row.pnl > 0 && correct === false
            ? `Profitable despite wrong outcome - likely sold position early at a profit`
            : hasPos && row.pnl < 0 && correct === true
            ? `Loss despite correct outcome - likely bought at unfavorable price or sold early at a loss`
            : ""
        }
      >
        {hasPos ? fmtDollar(row.pnl) : "—"}
      </td>
      <td className={`px-3 py-2 text-right font-mono text-[11px] ${hasPos ? pnlCls(row.return_pct) : "text-txt-muted"}`}>
        {hasPos ? `${row.return_pct >= 0 ? "+" : ""}${row.return_pct.toFixed(1)}%` : "—"}
      </td>
      <td className="px-3 py-2 text-right font-mono text-txt-muted text-[10px]">
        <div className="flex items-center justify-end gap-2">
          {row.resolved_at ? fmtTime(row.resolved_at) : "—"}
          {hasTrades && (
            <span className={`text-[10px] text-accent transition-transform inline-block ${expanded ? "rotate-180" : ""}`}>
              ▼
            </span>
          )}
        </div>
      </td>
    </tr>
    {expanded && hasTrades && (
      <tr>
        <td colSpan={9} className="px-3 py-2 bg-t-bg/50">
          <div className="space-y-2">
            <div className="text-[10px] font-mono text-txt-secondary font-medium">
              Trade History & P&L Calculation:
            </div>
            <table className="w-full text-[9px] font-mono">
              <thead>
                <tr className="border-b border-t-border/30 text-txt-muted">
                  <th className="text-left py-1">Date</th>
                  <th className="text-left py-1">Action</th>
                  <th className="text-right py-1">Contracts</th>
                  <th className="text-right py-1">Price</th>
                  <th className="text-right py-1">Value</th>
                  <th className="text-right py-1">Running P&L</th>
                </tr>
              </thead>
              <tbody>
                {row.trades?.map((trade, idx) => {
                  // Calculate running P&L
                  let runningPnl = 0;
                  let position = 0;
                  let costBasis = 0;
                  let positionSide = "";

                  for (let i = 0; i <= idx; i++) {
                    const t = row.trades![i];
                    if (t.action === "BUY") {
                      // Adding to position
                      const prevPosition = position;
                      const prevCostBasis = costBasis;
                      position += t.shares;
                      costBasis = ((prevPosition * prevCostBasis) + (t.shares * t.price)) / position;
                      positionSide = t.side; // Track which side we're holding
                    } else {
                      // Selling position
                      const sellPnl = t.shares * (t.price - costBasis);
                      runningPnl += sellPnl;
                      position -= t.shares;
                    }
                  }

                  // Add unrealized P&L if position remains
                  if (position > 0 && idx === row.trades!.length - 1 && row.outcome !== "PENDING") {
                    // Settlement price is $1 if our position matches outcome, $0 if not
                    const settlementPrice =
                      (positionSide === "YES" && row.outcome === "YES") ||
                      (positionSide === "NO" && row.outcome === "NO") ? 1.0 : 0.0;
                    const unrealizedPnl = position * (settlementPrice - costBasis);
                    runningPnl += unrealizedPnl;
                  }

                  return (
                    <tr key={idx} className="border-b border-t-border/20">
                      <td className="py-1 text-txt-muted">
                        {trade.date ? new Date(trade.date).toLocaleDateString() : "—"}
                      </td>
                      <td className="py-1">
                        <span className={`px-1 py-0.5 rounded text-[8px] ${
                          trade.action === "BUY"
                            ? "bg-accent/15 text-accent"
                            : "bg-orange-500/15 text-orange-400"
                        }`}>
                          {trade.action} {trade.side}
                        </span>
                      </td>
                      <td className="py-1 text-right text-txt-secondary">
                        {trade.shares}
                      </td>
                      <td className="py-1 text-right text-txt-secondary">
                        {(trade.price * 100).toFixed(0)}¢
                      </td>
                      <td className="py-1 text-right text-txt-secondary">
                        ${trade.value.toFixed(2)}
                      </td>
                      <td className={`py-1 text-right font-medium ${
                        runningPnl > 0 ? "text-profit" : runningPnl < 0 ? "text-loss" : "text-txt-muted"
                      }`}>
                        {runningPnl !== 0 ? `${runningPnl > 0 ? "+" : ""}$${runningPnl.toFixed(2)}` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>

            {/* P&L Breakdown */}
            <div className="mt-2 p-2 rounded bg-t-panel/50 border border-t-border/30 text-[9px] space-y-1">
              <div className="text-txt-secondary font-medium">P&L Breakdown:</div>
              {(() => {
                let totalBought = 0;
                let totalSold = 0;
                let remainingShares = 0;
                let holdingSide = ""; // Track which side we're holding

                // Track by side to handle YES/NO correctly
                let yesPosition = 0;
                let noPosition = 0;

                row.trades?.forEach(t => {
                  if (t.action === "BUY") {
                    totalBought += t.value;
                    if (t.side === "YES") {
                      yesPosition += t.shares;
                    } else if (t.side === "NO") {
                      noPosition += t.shares;
                    }
                  } else { // SELL
                    totalSold += t.value;
                    if (t.side === "YES") {
                      yesPosition -= t.shares;
                    } else if (t.side === "NO") {
                      noPosition -= t.shares;
                    }
                  }
                });

                // Determine remaining position and side
                remainingShares = Math.max(yesPosition, noPosition);
                holdingSide = yesPosition > 0 ? "YES" : noPosition > 0 ? "NO" : "";

                // Settlement value depends on if our position matches the outcome
                const settlementValue = remainingShares > 0
                  ? remainingShares * ((holdingSide === "YES" && row.outcome === "YES") ||
                                      (holdingSide === "NO" && row.outcome === "NO") ? 1 : 0)
                  : 0;

                const netPnl = totalSold + settlementValue - totalBought;

                return (
                  <>
                    <div className="flex justify-between text-txt-muted">
                      <span>Total Invested:</span>
                      <span className="font-mono">-${totalBought.toFixed(2)}</span>
                    </div>
                    <div className="flex justify-between text-txt-muted">
                      <span>Total Sold:</span>
                      <span className="font-mono">+${totalSold.toFixed(2)}</span>
                    </div>
                    {remainingShares > 0 && (
                      <div className="flex justify-between text-txt-muted">
                        <span>Settlement ({remainingShares.toFixed(0)} {holdingSide} @ {
                          (holdingSide === "YES" && row.outcome === "YES") || (holdingSide === "NO" && row.outcome === "NO")
                            ? "$1" : "$0"
                        }):</span>
                        <span className="font-mono">{settlementValue > 0 ? "+" : ""}${settlementValue.toFixed(2)}</span>
                      </div>
                    )}
                    <div className="flex justify-between font-medium pt-1 border-t border-t-border/30">
                      <span>Net P&L:</span>
                      <span className={`font-mono ${netPnl > 0 ? "text-profit" : netPnl < 0 ? "text-loss" : "text-txt-muted"}`}>
                        {netPnl > 0 ? "+" : ""}${netPnl.toFixed(2)}
                      </span>
                    </div>
                  </>
                );
              })()}
            </div>
          </div>
        </td>
      </tr>
    )}
    </>
  );
}
