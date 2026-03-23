"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { createApiClient, type ComparisonModelData, type ComparisonModelsData } from "@/lib/api";
import { fmtDollar } from "@/lib/utils";

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const REFRESH_INTERVAL = 30_000;

const MODEL_COLORS: Record<string, string> = {
  GPT5: "#10a37f",
  Grok4: "#1da1f2",
  Opus46: "#d4a017",
};

function StatCard({
  label,
  value,
  sub,
  pnl,
}: {
  label: string;
  value: string;
  sub?: string;
  pnl?: number;
}) {
  const pnlClass =
    pnl == null ? "text-txt-primary" : pnl > 0 ? "text-profit" : pnl < 0 ? "text-loss" : "text-txt-primary";
  return (
    <div className="bg-t-panel border border-t-border rounded p-3">
      <div className="text-[9px] text-txt-muted font-mono uppercase tracking-wider mb-1">{label}</div>
      <div className={`text-sm font-mono font-semibold ${pnlClass}`}>{value}</div>
      {sub && <div className="text-[9px] text-txt-secondary font-mono mt-0.5">{sub}</div>}
    </div>
  );
}

function ModelCard({ instance, data }: { instance: string; data: ComparisonModelData }) {
  const pnlPct =
    data.starting_cash > 0 ? ((data.total_pnl / data.starting_cash) * 100).toFixed(2) : "0.00";
  const balanceDelta = data.balance - data.starting_cash;
  const color = MODEL_COLORS[instance] ?? "#888";
  const lastUpdated = data.last_updated
    ? new Date(data.last_updated).toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "No activity yet";

  return (
    <Link href={`/comparison/${instance}`} className="block group">
      <div className="bg-t-panel border border-t-border rounded-lg p-4 space-y-3 transition-all group-hover:border-accent/40 group-hover:bg-t-panel-hover cursor-pointer">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div
              className="w-2.5 h-2.5 rounded-full"
              style={{ backgroundColor: color }}
            />
            <span className="text-sm font-semibold text-txt-primary font-mono">
              {data.model_label}
            </span>
            <span className="text-[9px] text-txt-muted font-mono border border-t-border rounded px-1.5 py-0.5">
              DRY RUN
            </span>
          </div>
          <div className="flex items-center gap-2">
            {data.error && (
              <span className="text-[9px] text-loss font-mono">⚠ error</span>
            )}
            <span className="text-[9px] text-txt-muted group-hover:text-accent transition-colors">→</span>
          </div>
        </div>

        {/* Metrics */}
        <div className="grid grid-cols-2 gap-2">
          <StatCard
            label="Balance"
            value={`$${data.balance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
            sub={`started $${data.starting_cash.toLocaleString()}`}
            pnl={balanceDelta}
          />
          <StatCard
            label="Total P&L"
            value={fmtDollar(data.total_pnl)}
            sub={`${pnlPct}% return`}
            pnl={data.total_pnl}
          />
          <StatCard
            label="Trades"
            value={String(data.trade_count)}
            sub={`${data.open_positions} open`}
          />
          <StatCard
            label="Win Rate"
            value={data.trade_count > 0 ? `${(data.win_rate * 100).toFixed(1)}%` : "—"}
            sub={data.trade_count > 0 ? `${data.trade_count} settled` : "no settled trades"}
          />
        </div>

        {/* Last updated */}
        <div className="text-[9px] text-txt-muted font-mono border-t border-t-border pt-2">
          Last prediction: {lastUpdated}
        </div>
      </div>
    </Link>
  );
}

const CustomTooltip = ({
  active,
  payload,
}: {
  active?: boolean;
  payload?: Array<{ name: string; value: number; payload: { label: string } }>;
}) => {
  if (!active || !payload?.length) return null;
  const { label } = payload[0].payload;
  const value = payload[0].value;
  return (
    <div className="bg-t-panel border border-t-border rounded p-2 text-[10px] font-mono shadow-lg">
      <div className="text-txt-primary font-semibold">{label}</div>
      <div className={value >= 0 ? "text-profit" : "text-loss"}>
        P&L: {fmtDollar(value)}
      </div>
    </div>
  );
};

export default function ComparisonPage() {
  const [data, setData] = useState<ComparisonModelsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [lastUpdate, setLastUpdate] = useState("");

  const apiClient = createApiClient(DEFAULT_API_URL);

  const fetchData = useCallback(async () => {
    try {
      const result = await apiClient.getComparisonModels();
      if (result) setData(result);
    } catch (e) {
      console.error("Failed to fetch comparison data:", e);
    } finally {
      setLoading(false);
      setLastUpdate(
        new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        })
      );
    }
  }, []);

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchData]);

  const instances = Object.keys(MODEL_COLORS);
  const chartData = data
    ? instances.map((inst) => ({
        label: data.models[inst]?.model_label ?? inst,
        instance: inst,
        pnl: data.models[inst]?.total_pnl ?? 0,
      }))
    : [];

  const bestModel =
    data && instances.length > 0
      ? instances.reduce((best, inst) =>
          (data.models[inst]?.total_pnl ?? -Infinity) >
          (data.models[best]?.total_pnl ?? -Infinity)
            ? inst
            : best
        )
      : null;

  return (
    <main className="min-h-screen bg-t-bg">
      {/* Header */}
      <header className="border-b border-t-border bg-t-panel/90 backdrop-blur-md sticky top-0 z-20">
        <div className="max-w-[1400px] mx-auto px-5 h-11 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="text-[10px] text-txt-muted hover:text-txt-primary transition-colors"
            >
              ← Prophet Arena
            </Link>
            <span className="text-txt-secondary text-[10px]">/</span>
            <span className="text-sm font-semibold text-txt-primary tracking-tight">
              Model Arena
            </span>
            <span className="text-[9px] font-mono text-txt-muted border border-t-border rounded px-1.5 py-0.5">
              dry-run benchmarks
            </span>
          </div>
          <div className="flex items-center gap-2 text-[9px] font-mono text-txt-muted">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full bg-profit"
              style={{ opacity: loading ? 0.4 : 1 }}
            />
            {lastUpdate || "--:--:--"}
          </div>
        </div>
      </header>

      <div className="max-w-[1400px] mx-auto px-5 py-4 space-y-4">
        {/* Intro */}
        <div className="text-[11px] text-txt-secondary font-mono border border-t-border rounded p-3 bg-t-panel/50">
          These models run on the same Kalshi markets as the live instances, but in{" "}
          <span className="text-accent">dry-run mode</span> — no real money is used. All models start
          with the same virtual balance and make independent predictions with market price data
          included.
        </div>

        {/* Best performer banner */}
        {bestModel && data && (data.models[bestModel]?.total_pnl ?? 0) !== 0 && (
          <div className="flex items-center gap-2 text-[10px] font-mono text-txt-secondary border border-t-border rounded p-2 bg-t-panel/50">
            <span className="text-profit">▲</span>
            <span>
              Best performer:{" "}
              <span className="text-txt-primary font-semibold">
                {data.models[bestModel]?.model_label}
              </span>{" "}
              with {fmtDollar(data.models[bestModel]?.total_pnl ?? 0)} P&L
            </span>
          </div>
        )}

        {/* Model cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {loading
            ? instances.map((inst) => (
                <div
                  key={inst}
                  className="bg-t-panel border border-t-border rounded-lg p-4 h-48 animate-pulse"
                />
              ))
            : instances.map((inst) => {
                const modelData = data?.models[inst];
                if (!modelData) return null;
                return (
                  <ModelCard key={inst} instance={inst} data={modelData} />
                );
              })}
        </div>

        {/* P&L Comparison Chart */}
        {!loading && chartData.some((d) => d.pnl !== 0) && (
          <div className="bg-t-panel border border-t-border rounded-lg p-4">
            <div className="text-[10px] text-txt-muted font-mono uppercase tracking-wider mb-3">
              P&L Comparison
            </div>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fontFamily: "JetBrains Mono, monospace", fill: "#888" }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 9, fontFamily: "JetBrains Mono, monospace", fill: "#888" }}
                  axisLine={false}
                  tickLine={false}
                  tickFormatter={(v) => `$${v.toFixed(0)}`}
                  width={48}
                />
                <Tooltip content={<CustomTooltip />} />
                <ReferenceLine y={0} stroke="#333" strokeWidth={1} />
                <Bar dataKey="pnl" radius={[3, 3, 0, 0]}>
                  {chartData.map((entry) => (
                    <Cell
                      key={entry.instance}
                      fill={
                        entry.pnl >= 0
                          ? MODEL_COLORS[entry.instance] ?? "#888"
                          : "#ef4444"
                      }
                      opacity={0.8}
                    />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}

        {/* Summary table */}
        {!loading && data && (
          <div className="bg-t-panel border border-t-border rounded-lg overflow-hidden">
            <div className="text-[10px] text-txt-muted font-mono uppercase tracking-wider p-3 border-b border-t-border">
              Summary
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-[10px] font-mono">
                <thead>
                  <tr className="border-b border-t-border text-txt-muted">
                    <th className="text-left px-3 py-2">Model</th>
                    <th className="text-right px-3 py-2">Balance</th>
                    <th className="text-right px-3 py-2">P&L</th>
                    <th className="text-right px-3 py-2">Return</th>
                    <th className="text-right px-3 py-2">Trades</th>
                    <th className="text-right px-3 py-2">Open</th>
                    <th className="text-right px-3 py-2">Win Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {instances.map((inst) => {
                    const m = data.models[inst];
                    if (!m) return null;
                    const returnPct =
                      m.starting_cash > 0
                        ? ((m.total_pnl / m.starting_cash) * 100).toFixed(2)
                        : "0.00";
                    const pnlClass =
                      m.total_pnl > 0
                        ? "text-profit"
                        : m.total_pnl < 0
                        ? "text-loss"
                        : "text-txt-muted";
                    return (
                      <tr
                        key={inst}
                        className="border-b border-t-border/50 hover:bg-t-border/10 transition-colors cursor-pointer"
                        onClick={() => window.location.href = `/comparison/${inst}`}
                      >
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-1.5">
                            <div
                              className="w-1.5 h-1.5 rounded-full"
                              style={{ backgroundColor: MODEL_COLORS[inst] ?? "#888" }}
                            />
                            <span className="text-txt-primary group-hover:text-accent">{m.model_label}</span>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-right text-txt-primary">
                          ${m.balance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                        </td>
                        <td className={`px-3 py-2 text-right ${pnlClass}`}>
                          {fmtDollar(m.total_pnl)}
                        </td>
                        <td className={`px-3 py-2 text-right ${pnlClass}`}>
                          {returnPct}%
                        </td>
                        <td className="px-3 py-2 text-right text-txt-secondary">
                          {m.trade_count}
                        </td>
                        <td className="px-3 py-2 text-right text-txt-secondary">
                          {m.open_positions}
                        </td>
                        <td className="px-3 py-2 text-right text-txt-secondary">
                          {m.trade_count > 0 ? `${(m.win_rate * 100).toFixed(1)}%` : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {!loading && !data && (
          <div className="text-center text-txt-muted text-xs font-mono py-12">
            No comparison data available. Start the comparison worker to begin benchmarking.
          </div>
        )}
      </div>
    </main>
  );
}
