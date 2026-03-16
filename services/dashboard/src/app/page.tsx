"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import {
  api,
  computePortfolioMetrics,
  type Trade,
  type Market,
  type Position,
  type PnLData,
  type HealthData,
  type SystemLogEntry,
  type KalshiBalanceData,
  type AnalyticsSummary,
  type ModelCalibrationData,
  type Alert,
} from "@/lib/api";
import { fmtDollar } from "@/lib/utils";
import { PnLChart } from "@/components/PnLChart";
import { TradeHistory } from "@/components/TradeHistory";
import { ActivePositions } from "@/components/ActivePositions";
import { MarketBreakdown } from "@/components/MarketBreakdown";
import { LiveActivity } from "@/components/LiveActivity";
import { SystemHealth } from "@/components/SystemHealth";
import { MarketTimeline } from "@/components/MarketTimeline";
import { EdgeChart } from "@/components/EdgeChart";
import { PositionHeatmap } from "@/components/PositionHeatmap";
import { RiskMetrics } from "@/components/RiskMetrics";
import { PnLAttribution } from "@/components/PnLAttribution";
import { ModelCalibration } from "@/components/ModelCalibration";
import { AlertsPanel } from "@/components/AlertsPanel";
import { ModelAggregation } from "@/components/ModelAggregation";

const REFRESH_INTERVAL = 5_000;

export default function Dashboard() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [pnl, setPnl] = useState<PnLData | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [logs, setLogs] = useState<SystemLogEntry[]>([]);
  const [balance, setBalance] = useState<KalshiBalanceData | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [calibration, setCalibration] = useState<ModelCalibrationData | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [refreshing, setRefreshing] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAll = useCallback(async () => {
    setRefreshing(true);
    try {
      const [t, m, posData, pnlData, h, l, b, an, cal, al] = await Promise.all([
        api.getTrades(200),
        api.getMarkets(200),
        api.getPositions(200),
        api.getPnL(),
        api.getHealth(),
        api.getSystemLogs(40),
        api.getKalshiBalance().catch(() => null),
        api.getAnalyticsSummary(),
        api.getModelCalibration(),
        api.getAlerts(),
      ]);
      setTrades(t);
      setMarkets(m);
      setPositions(posData.positions);
      setPnl(pnlData);
      setHealth(h);
      setLogs(l);
      if (b) setBalance(b);
      setAnalytics(an);
      setCalibration(cal);
      setAlerts(al.alerts);
      setLastUpdate(
        new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        })
      );
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to fetch data");
    } finally {
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    intervalRef.current = setInterval(fetchAll, REFRESH_INTERVAL);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchAll]);

  const metrics = computePortfolioMetrics(positions, trades, pnl);

  const cashBalance =
    balance == null
      ? null
      : balance.dry_run
        ? balance.balance - metrics.capitalDeployed
        : balance.balance;

  const alertErrors = alerts.filter((a) => a.severity === "error").length;
  const alertWarnings = alerts.filter((a) => a.severity === "warning").length;

  return (
    <main className="min-h-screen bg-t-bg">
      {/* Header */}
      <header className="border-b border-t-border bg-t-panel/90 backdrop-blur-md sticky top-0 z-20">
        <div className="max-w-[1800px] mx-auto px-5 h-11 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold text-txt-primary tracking-tight">
              Prophet Arena
            </span>
            <button
              onClick={() => fetchAll()}
              disabled={refreshing}
              className="flex items-center gap-1 text-[10px] text-txt-muted font-mono hover:text-txt-primary transition-colors"
              title="Click to refresh now"
            >
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${refreshing ? "bg-accent animate-pulse" : "bg-profit"}`} />
              {lastUpdate || "--:--:--"}
            </button>
            {/* Alert indicators in header */}
            {(alertErrors > 0 || alertWarnings > 0) && (
              <div className="flex items-center gap-1.5">
                {alertErrors > 0 && (
                  <span className="flex items-center gap-1 text-[9px] font-mono text-loss bg-loss-dim px-1.5 py-0.5 rounded">
                    <span className="w-1.5 h-1.5 rounded-full bg-loss animate-pulse" />
                    {alertErrors}
                  </span>
                )}
                {alertWarnings > 0 && (
                  <span className="flex items-center gap-1 text-[9px] font-mono text-warn bg-warn-dim px-1.5 py-0.5 rounded">
                    <span className="w-1.5 h-1.5 rounded-full bg-warn" />
                    {alertWarnings}
                  </span>
                )}
              </div>
            )}
          </div>
          <div className="flex items-center gap-3">
            <CycleCountdown health={health} />
            <SystemHealth health={health} />
          </div>
        </div>
      </header>

      <div className="max-w-[1800px] mx-auto px-5 py-3 space-y-3">
        {error && (
          <div className="bg-loss-dim border border-loss/20 text-loss px-3 py-2 rounded text-xs font-mono">
            {error}
          </div>
        )}

        {/* Row 1: Portfolio Summary Metrics */}
        <div className="grid grid-cols-3 md:grid-cols-5 xl:grid-cols-11 gap-2">
          <MetricCard
            label="Cash Balance"
            value={
              cashBalance != null
                ? `$${cashBalance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : "--"
            }
            sub={balance?.dry_run ? "simulated" : "kalshi"}
          />
          <MetricCard
            label="Total P&L"
            value={fmtDollar(metrics.totalPnl)}
            pnl={metrics.totalPnl}
          />
          <MetricCard
            label="Realized"
            value={fmtDollar(metrics.totalRealizedPnl)}
            pnl={metrics.totalRealizedPnl}
          />
          <MetricCard
            label="Unrealized"
            value={fmtDollar(metrics.totalUnrealizedPnl)}
            pnl={metrics.totalUnrealizedPnl}
          />
          <MetricCard
            label="Deployed"
            value={fmtDollar(metrics.capitalDeployed)}
          />
          <MetricCard
            label="Positions"
            value={metrics.openPositions.toString()}
          />
          <MetricCard
            label="Markets"
            value={metrics.marketsTraded.toString()}
          />
          <MetricCard
            label="Win Rate"
            value={`${(metrics.winRate * 100).toFixed(0)}%`}
            pnl={metrics.winRate >= 0.5 ? 1 : -1}
          />
          <MetricCard
            label="Return"
            value={`${metrics.avgReturn >= 0 ? "+" : ""}${metrics.avgReturn.toFixed(1)}%`}
            pnl={metrics.avgReturn}
          />
          <MetricCard
            label="Sharpe"
            value={analytics ? analytics.sharpe_ratio.toFixed(2) : "--"}
            pnl={analytics ? analytics.sharpe_ratio : undefined}
          />
          <MetricCard
            label="Max DD"
            value={analytics ? fmtDollar(analytics.max_drawdown) : "--"}
            pnl={analytics ? -Math.abs(analytics.max_drawdown) : undefined}
          />
        </div>

        {/* Row 2: P&L Chart + Risk Metrics */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-2">
          <div className="lg:col-span-3">
            <SectionLabel text="P&L Over Time" />
            <PnLChart
              data={pnl?.series ?? []}
              tradeMarkers={pnl?.trade_markers ?? []}
            />
          </div>
          <div className="lg:col-span-2">
            <SectionLabel text="Risk & Performance" />
            <RiskMetrics analytics={analytics} />
          </div>
        </div>

        {/* Row 3: Edge + P&L Attribution */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
          <div>
            <SectionLabel text="Edge Visualization" />
            <EdgeChart positions={positions} markets={markets} />
          </div>
          <div>
            <SectionLabel text="P&L Attribution" />
            <PnLAttribution analytics={analytics} />
          </div>
        </div>

        {/* Row 4: Model Aggregation */}
        {markets.some((m) => (m.model_predictions?.length ?? 0) > 0) && (
          <div>
            <SectionLabel text="Model Predictions" count={markets.filter((m) => (m.model_predictions?.length ?? 0) > 1).length} />
            <ModelAggregation markets={markets} />
          </div>
        )}

        {/* Row 5: Position Heatmap */}
        {positions.length > 0 && (
          <div>
            <SectionLabel text="Position Heatmap" count={positions.length} />
            <PositionHeatmap positions={positions} markets={markets} />
          </div>
        )}

        {/* Row 6: Open Positions */}
        <div>
          <SectionLabel text="Open Positions" count={positions.length} />
          <ActivePositions positions={positions} markets={markets} />
        </div>

        {/* Row 6: P&L by Market + Market Breakdown */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
          <div>
            <SectionLabel text="P&L by Market" />
            <MarketBreakdown positions={positions} trades={trades} />
          </div>
          <div>
            <SectionLabel text="Model Calibration" />
            <ModelCalibration calibration={calibration} />
          </div>
        </div>

        {/* Row 7: Market Activity Timeline */}
        <div>
          <SectionLabel text="Market Activity" />
          <MarketTimeline trades={trades} markets={markets} positions={positions} />
        </div>

        {/* Row 8: Trades + Alerts + System Activity */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-2">
          <div className="lg:col-span-2">
            <SectionLabel text="Trade History" count={trades.length} />
            <TradeHistory trades={trades} markets={markets} />
          </div>
          <div>
            <SectionLabel text="Alerts" count={alerts.length > 0 ? alerts.length : undefined} />
            <AlertsPanel alerts={alerts} />
          </div>
          <div>
            <SectionLabel text="System Activity" />
            <LiveActivity logs={logs} />
          </div>
        </div>
      </div>
    </main>
  );
}

function MetricCard({
  label,
  value,
  pnl,
  sub,
}: {
  label: string;
  value: string;
  pnl?: number;
  sub?: string;
}) {
  const color =
    pnl === undefined
      ? "text-txt-primary"
      : pnl > 0
        ? "text-profit"
        : pnl < 0
          ? "text-loss"
          : "text-txt-secondary";

  return (
    <div className="bg-t-panel border border-t-border rounded px-2.5 py-2 hover:bg-t-panel-hover transition-colors">
      <div className="text-[9px] text-txt-muted uppercase tracking-widest font-medium leading-none">
        {label}
      </div>
      <div className={`text-base font-semibold font-mono mt-1 leading-none ${color}`}>
        {value}
      </div>
      {sub && (
        <div className="text-[9px] text-txt-muted mt-0.5 leading-none">{sub}</div>
      )}
    </div>
  );
}

function CycleCountdown({ health }: { health: HealthData | null }) {
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  if (!health?.last_cycle_end || !health.poll_interval_sec) {
    return (
      <span className="text-[10px] text-txt-muted font-mono">
        Next cycle: --:--
      </span>
    );
  }

  const cycleEndMs = new Date(health.last_cycle_end).getTime();
  const nextCycleMs = cycleEndMs + health.poll_interval_sec * 1000;
  const remainingSec = Math.max(0, Math.floor((nextCycleMs - now) / 1000));
  const min = Math.floor(remainingSec / 60);
  const sec = remainingSec % 60;
  const isOverdue = remainingSec === 0;

  return (
    <span
      className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
        isOverdue
          ? "text-warn bg-warn-dim animate-pulse"
          : remainingSec < 60
            ? "text-accent bg-accent-dim"
            : "text-txt-muted"
      }`}
      title={`Last cycle ended: ${health.last_cycle_end}`}
    >
      {isOverdue
        ? "Cycle running..."
        : `Next cycle: ${min}:${sec.toString().padStart(2, "0")}`}
    </span>
  );
}

function SectionLabel({ text, count }: { text: string; count?: number }) {
  return (
    <div className="flex items-center gap-1.5 mb-1.5">
      <span className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest">
        {text}
      </span>
      {count != null && (
        <span className="text-[9px] bg-t-border text-txt-muted px-1.5 py-px rounded font-mono">
          {count}
        </span>
      )}
    </div>
  );
}
