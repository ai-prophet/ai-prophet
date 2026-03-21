"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import Link from "next/link";
import {
  createApiClient,
  dashboardInstances,
  getDefaultDashboardInstance,
  type DashboardInstance,
  computePortfolioMetrics,
  buildUnifiedMarketRows,
  liveNetPnl as calcLiveNetPnl,
  type Trade,
  type Market,
  type Position,
  type PnLData,
  type HealthData,
  type SystemLogEntry,
  type KalshiBalanceData,
  type AnalyticsSummary,
  type ResolvedMarketsData,
  type Alert,
} from "@/lib/api";
import { fmtDollar } from "@/lib/utils";
import { PnLChart } from "@/components/PnLChart";
import { LiveActivity } from "@/components/LiveActivity";
import { SystemHealth } from "@/components/SystemHealth";
import { PositionHeatmap } from "@/components/PositionHeatmap";
import { RiskMetrics } from "@/components/RiskMetrics";
// import { PnLAttribution } from "@/components/PnLAttribution"; // commented out — reinstate when needed
import { ModelCalibration } from "@/components/ModelCalibration";
import { AlertsPanel } from "@/components/AlertsPanel";
import { UnifiedMarketTable } from "@/components/UnifiedMarketTable";

const REFRESH_INTERVAL = 5_000;
const INSTANCE_STORAGE_KEY = "dashboard-instance-key";

type DashboardSnapshot = {
  trades: Trade[];
  markets: Market[];
  positions: Position[];
  pnl: PnLData | null;
  health: HealthData | null;
  logs: SystemLogEntry[];
  balance: KalshiBalanceData | null;
  analytics: AnalyticsSummary | null;
  resolvedMarkets: ResolvedMarketsData | null;
  alerts: Alert[];
  lastUpdate: string;
};

function formatLastUpdateTime(): string {
  return new Date().toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export default function Dashboard() {
  const defaultInstance = getDefaultDashboardInstance();
  const [selectedInstanceKey, setSelectedInstanceKey] = useState(defaultInstance.key);
  const [loadingInstanceKey, setLoadingInstanceKey] = useState<string | null>(null);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [pnl, setPnl] = useState<PnLData | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [logs, setLogs] = useState<SystemLogEntry[]>([]);
  const [balance, setBalance] = useState<KalshiBalanceData | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [resolvedMarkets, setResolvedMarkets] = useState<ResolvedMarketsData | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [clearingAlertKey, setClearingAlertKey] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [refreshing, setRefreshing] = useState(false);
  const [scrollToMarketId, setScrollToMarketId] = useState<string | null>(null);
  const [supportTab, setSupportTab] = useState<"risk" | "alerts" | "activity">("risk");
  const [marketViewTab, setMarketViewTab] = useState<"activity" | "heatmap">("activity");
  const dataCacheRef = useRef<Record<string, DashboardSnapshot>>({});
  const activeRequestRef = useRef(0);
  const selectedInstance =
    dashboardInstances.find((instance) => instance.key === selectedInstanceKey) ||
    defaultInstance;
  const instanceApi = useMemo(
    () => createApiClient(selectedInstance.apiUrl, selectedInstance.instanceName),
    [selectedInstance.apiUrl, selectedInstance.instanceName]
  );
  const isSwitchingInstance =
    loadingInstanceKey != null && loadingInstanceKey === selectedInstance.key;

  const focusMarket = useCallback((marketId: string) => {
    const normalizedTarget = marketId.startsWith("kalshi:")
      ? marketId
      : (
        markets.find((m) => m.market_id === marketId || m.ticker === marketId)?.market_id
        ?? `kalshi:${marketId}`
      );

    setMarketViewTab("activity");
    setScrollToMarketId(null);
    requestAnimationFrame(() => {
      setScrollToMarketId(normalizedTarget);
    });
  }, [markets]);

  // Live net P&L per market: cash flow from trades + current bid value of open position
  const livePnlByMarket = (() => {
    const marketById = new Map(markets.map((m) => [m.market_id, m]));
    const tradesByTicker = new Map<string, Trade[]>();
    for (const t of trades) {
      const arr = tradesByTicker.get(t.ticker) ?? [];
      arr.push(t);
      tradesByTicker.set(t.ticker, arr);
    }
    const map = new Map<string, number>();
    for (const pos of positions) {
      const mkt = marketById.get(pos.market_id);
      const mktTrades = tradesByTicker.get(pos.ticker ?? "") ?? [];
      const cashFlow = mktTrades.reduce((sum, t) => {
        const qty = t.filled_shares || t.count;
        const price = t.price_cents / 100;
        return sum + (t.action?.toUpperCase() === "SELL" ? qty * price : -(qty * price));
      }, 0);
      const currentBid = pos.contract.toLowerCase() === "yes"
        ? (mkt?.yes_bid ?? (mkt?.no_ask != null ? 1 - mkt.no_ask : null))
        : (mkt?.no_bid ?? (mkt?.yes_ask != null ? 1 - mkt.yes_ask : null));
      const net = cashFlow + (currentBid != null ? pos.quantity * currentBid : 0);
      map.set(pos.market_id, net);
    }
    return map;
  })();
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const stored = window.localStorage.getItem(INSTANCE_STORAGE_KEY);
    if (stored && dashboardInstances.some((instance) => instance.key === stored)) {
      setSelectedInstanceKey(stored);
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(INSTANCE_STORAGE_KEY, selectedInstance.key);
  }, [selectedInstance.key]);

  const applySnapshot = useCallback((snapshot: DashboardSnapshot) => {
    setTrades(snapshot.trades);
    setMarkets(snapshot.markets);
    setPositions(snapshot.positions);
    setPnl(snapshot.pnl);
    setHealth(snapshot.health);
    setLogs(snapshot.logs);
    setBalance(snapshot.balance);
    setAnalytics(snapshot.analytics);
    setResolvedMarkets(snapshot.resolvedMarkets);
    setAlerts(snapshot.alerts);
    setLastUpdate(snapshot.lastUpdate);
  }, []);

  const clearSnapshot = useCallback(() => {
    setTrades([]);
    setMarkets([]);
    setPositions([]);
    setPnl(null);
    setHealth(null);
    setLogs([]);
    setBalance(null);
    setAnalytics(null);
    setResolvedMarkets(null);
    setAlerts([]);
    setClearingAlertKey(null);
    setLastUpdate("");
  }, []);

  const clearAlert = useCallback(async (alertKey: string) => {
    setClearingAlertKey(alertKey);
    let removedAlert: Alert | undefined;
    try {
      setAlerts((currentAlerts) => {
        removedAlert = currentAlerts.find((alert) => alert.key === alertKey);
        const nextAlerts = currentAlerts.filter((alert) => alert.key !== alertKey);
        const current = dataCacheRef.current[selectedInstance.key];
        if (current) {
          dataCacheRef.current[selectedInstance.key] = {
            ...current,
            alerts: nextAlerts,
          };
        }
        return nextAlerts;
      });
      await instanceApi.clearAlert(alertKey);
    } catch (e) {
      if (removedAlert) {
        const restoredAlert = removedAlert;
        setAlerts((currentAlerts) => {
          if (currentAlerts.some((alert) => alert.key === restoredAlert.key)) {
            return currentAlerts;
          }
          const nextAlerts = [restoredAlert, ...currentAlerts];
          const current = dataCacheRef.current[selectedInstance.key];
          if (current) {
            dataCacheRef.current[selectedInstance.key] = {
              ...current,
              alerts: nextAlerts,
            };
          }
          return nextAlerts;
        });
      }
      const message = e instanceof Error ? e.message : "Failed to clear alert";
      setError(`${selectedInstance.label}: ${message}`);
    } finally {
      setClearingAlertKey((current) => (current === alertKey ? null : current));
    }
  }, [instanceApi, selectedInstance.key, selectedInstance.label]);

  const clearAllAlerts = useCallback(async () => {
    setClearingAll(true);
    const previousAlerts = alerts;
    try {
      setAlerts([]);
      const current = dataCacheRef.current[selectedInstance.key];
      if (current) {
        dataCacheRef.current[selectedInstance.key] = { ...current, alerts: [] };
      }
      await instanceApi.clearAllAlerts();
    } catch (e) {
      setAlerts(previousAlerts);
      const current = dataCacheRef.current[selectedInstance.key];
      if (current) {
        dataCacheRef.current[selectedInstance.key] = { ...current, alerts: previousAlerts };
      }
      const message = e instanceof Error ? e.message : "Failed to clear all alerts";
      setError(`${selectedInstance.label}: ${message}`);
    } finally {
      setClearingAll(false);
    }
  }, [alerts, instanceApi, selectedInstance.key, selectedInstance.label]);

  const fetchAll = useCallback(async () => {
    const requestId = activeRequestRef.current + 1;
    activeRequestRef.current = requestId;
    const instanceKey = selectedInstance.key;
    setRefreshing(true);
    // Only show the loading banner when there's no cached data (tab switch / first load)
    if (!dataCacheRef.current[instanceKey]) {
      setLoadingInstanceKey(instanceKey);
    }

    try {
      // Tier 1: Critical data — renders header, metrics, markets, alerts immediately
      const [t, m, posData, h, b, al] = await Promise.all([
        instanceApi.getTrades(500),
        instanceApi.getMarkets(200),
        instanceApi.getPositions(200),
        instanceApi.getHealth(),
        instanceApi.getKalshiBalance().catch(() => null),
        instanceApi.getAlerts(),
      ]);
      if (activeRequestRef.current !== requestId) return;

      // Apply Tier 1 immediately so the page renders
      const cached = dataCacheRef.current[instanceKey];
      const tier1Snapshot: DashboardSnapshot = {
        trades: t,
        markets: m,
        positions: posData.positions,
        pnl: cached?.pnl ?? null,
        health: h,
        logs: cached?.logs ?? [],
        balance: b,
        analytics: cached?.analytics ?? null,
        resolvedMarkets: cached?.resolvedMarkets ?? null,
        alerts: al.alerts,
        lastUpdate: formatLastUpdateTime(),
      };
      dataCacheRef.current[instanceKey] = tier1Snapshot;
      applySnapshot(tier1Snapshot);
      setError("");
      setLoadingInstanceKey((current) => (current === instanceKey ? null : current));

      // Tier 2: Heavy analytics — fills in P&L chart, risk metrics, resolved markets, logs
      const [pnlData, l, an, resolved] = await Promise.all([
        instanceApi.getPnL(),
        instanceApi.getSystemLogs(40),
        instanceApi.getAnalyticsSummary(),
        instanceApi.getResolvedMarkets(),
      ]);
      if (activeRequestRef.current !== requestId) return;

      const fullSnapshot: DashboardSnapshot = {
        ...tier1Snapshot,
        pnl: pnlData,
        logs: l,
        analytics: an,
        resolvedMarkets: resolved,
        lastUpdate: formatLastUpdateTime(),
      };
      dataCacheRef.current[instanceKey] = fullSnapshot;
      applySnapshot(fullSnapshot);
    } catch (e) {
      if (activeRequestRef.current !== requestId) return;
      const message = e instanceof Error ? e.message : "Failed to fetch data";
      setError(`${selectedInstance.label}: ${message}`);
    } finally {
      if (activeRequestRef.current !== requestId) return;
      setRefreshing(false);
      setLoadingInstanceKey((current) => (current === instanceKey ? null : current));
    }
  }, [applySnapshot, instanceApi, selectedInstance.key, selectedInstance.label]);

  useEffect(() => {
    const cachedSnapshot = dataCacheRef.current[selectedInstance.key];
    if (cachedSnapshot) {
      applySnapshot(cachedSnapshot);
    }
    // No clearSnapshot() — keep stale data visible until fresh data arrives
    setError("");
    fetchAll();
    intervalRef.current = setInterval(fetchAll, REFRESH_INTERVAL);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [applySnapshot, clearSnapshot, fetchAll, selectedInstance.key]);

  const metrics = computePortfolioMetrics(positions, trades, pnl, markets);

  const unifiedRows = buildUnifiedMarketRows(markets, positions, trades);
  const [expandedMetric, setExpandedMetric] = useState<"netpnl" | "realized" | "unrealized" | null>(null);

  // Per-position P&L breakdowns
  const marketById = new Map(markets.map((m) => [m.market_id, m]));

  // Replay trades in chronological order to compute the running avg at each sell.
  // This matches how the server computes realized_pnl (FIFO cost basis).
  // Accepts the trades array directly (from row.trades) so it uses the same set as liveNetPnl —
  // including trades matched via prediction.market_id fallback, not just by ticker.
  function replayTrades(rowTrades: Trade[]) {
    const relevant = [...rowTrades]
      .sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
    let netShares = 0;
    let totalCost = 0;
    const sells: { qty: number; sellPrice: number; avgAtSell: number; contribution: number }[] = [];
    let totalRealized = 0;
    for (const t of relevant) {
      const qty = t.filled_shares || t.count;
      let price = t.price_cents / 100;
      if (price > 1.0) price /= 100; // fix corrupted fill_price stored as cents
      const action = (t.action ?? "BUY").toUpperCase();
      const isYes = (t.side ?? "yes").toLowerCase() === "yes";
      if (action === "SELL") {
        const avgAtSell = Math.abs(netShares) > 0.001 ? Math.abs(totalCost / netShares) : 0;
        const contribution = (price - avgAtSell) * qty;
        totalRealized += contribution;
        sells.push({ qty, sellPrice: price, avgAtSell, contribution });
        if (isYes) { netShares -= qty; totalCost -= avgAtSell * qty; }
        else { netShares += qty; totalCost += avgAtSell * qty; }
        if (Math.abs(netShares) < 0.001) { netShares = 0; totalCost = 0; }
      } else {
        if (isYes) { netShares += qty; totalCost += qty * price; }
        else { netShares -= qty; totalCost -= qty * price; }
      }
    }
    const remainingQty = Math.abs(netShares);
    const remainingAvgPrice = remainingQty > 0.001 ? Math.abs(totalCost / netShares) : 0;
    const remainingSide = netShares >= 0 ? "yes" : "no";
    return { sells, totalRealized, remainingQty, remainingAvgPrice, remainingSide };
  }

  // Single pass — compute everything once per market_id so all cards and breakdowns
  // derive from identical numbers and are guaranteed to sum correctly.
  //
  // Open Value  = (bid − avgEntry) × qty   — unrealized gain/loss on open shares
  // Cash Spent  = avgEntry × qty            — net cost basis of open shares
  // Cash P&L    = totalRealized             — locked-in gains/losses from completed sells
  // Net P&L     = Cash P&L + Open Value
  //
  // Open Value + Cash Spent = bid × qty  (current market value of open shares)
  let totalCashPnl = 0;
  let totalOpenValue = 0;
  let totalCashSpent = 0;
  const seenMarketIds = new Set<string>();

  type PerMarketResult = {
    title: string;
    contract: string;
    sells: { qty: number; sellPrice: number; avgAtSell: number; contribution: number }[];
    totalRealized: number;
    avgEntry: number;
    currentBid: number | null;
    dbQty: number;
    openValue: number;
    cashSpent: number;
    hasMoreTrades: boolean;
  };
  const perMarket: PerMarketResult[] = [];

  for (const row of unifiedRows) {
    if (seenMarketIds.has(row.market_id)) continue;
    seenMarketIds.add(row.market_id);

    const { sells, totalRealized, remainingAvgPrice } = replayTrades(row.trades);
    totalCashPnl += totalRealized;

    const dbQty = row.position?.quantity ?? 0;
    const contract = row.position?.contract ?? (row.trades[0]?.side ?? "yes");
    const mkt = marketById.get(row.market_id);
    let currentBid: number | null = null;
    if (mkt && dbQty > 0.001) {
      currentBid = contract === "yes"
        ? (mkt.yes_bid ?? (mkt.no_ask != null ? 1.0 - mkt.no_ask : null))
        : (mkt.no_bid ?? (mkt.yes_ask != null ? 1.0 - mkt.yes_ask : null));
    }
    const cashSpent = remainingAvgPrice * dbQty;
    const openValue = currentBid != null ? currentBid * dbQty : 0;
    totalOpenValue += openValue;
    totalCashSpent += cashSpent;

    const dbPos = positions.find((p) => p.market_id === row.market_id);
    const hasMoreTrades = dbPos != null && Math.abs(totalRealized - dbPos.realized_pnl) > 0.005;

    perMarket.push({ title: row.title, contract, sells, totalRealized, avgEntry: remainingAvgPrice, currentBid, dbQty, openValue, cashSpent, hasMoreTrades });
  }

  const totalLiveNetPnl = totalOpenValue - totalCashSpent + totalCashPnl;

  const realizedBreakdown = perMarket
    .filter((r) => r.sells.length > 0)
    .map((r) => ({ title: r.title, contract: r.contract, sells: r.sells, value: r.totalRealized, hasMoreTrades: r.hasMoreTrades }))
    .sort((a, b) => b.value - a.value);

  const unrealizedBreakdown = perMarket
    .filter((r) => r.dbQty > 0.001 && r.currentBid != null)
    .map((r) => ({ title: r.title, contract: r.contract, quantity: r.dbQty, avgEntry: r.avgEntry, currentBid: r.currentBid!, value: r.openValue }))
    .sort((a, b) => b.value - a.value);

  const cashBalance =
    balance == null
      ? null
      : balance.balance;

  const alertErrors = alerts.filter((a) => a.severity === "error").length;
  const alertWarnings = alerts.filter((a) => a.severity === "warning").length;

  return (
    <main className="min-h-screen bg-t-bg">
      {/* Header */}
      <header className="border-b border-t-border bg-t-panel/90 backdrop-blur-md sticky top-0 z-20 overflow-hidden">
        <div className="max-w-[1800px] mx-auto px-5 h-11 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-sm font-semibold text-txt-primary tracking-tight">
              Prophet Arena
            </span>
            <InstanceTabs
              instances={dashboardInstances}
              selectedKey={selectedInstance.key}
              loadingKey={loadingInstanceKey}
              onSelect={setSelectedInstanceKey}
            />
            <button
              onClick={() => fetchAll()}
              disabled={refreshing}
              className="flex items-center gap-1 text-[10px] text-txt-muted font-mono hover:text-txt-primary transition-colors"
              title="Click to refresh now"
            >
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${refreshing ? "bg-accent animate-pulse" : "bg-profit"}`} />
              {lastUpdate || "--:--:--"}
            </button>
            {selectedInstance.description && (
              <span className="hidden xl:inline-flex text-[9px] font-mono text-txt-secondary border border-t-border rounded px-1.5 py-0.5">
                {selectedInstance.description}
              </span>
            )}
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
            <Link href="/comparison" className="text-[10px] text-txt-muted hover:text-accent transition-colors px-2 py-1 rounded border border-t-border">
              Model Arena
            </Link>
            <Link href="/docs" className="text-[10px] text-txt-muted hover:text-accent transition-colors px-2 py-1 rounded border border-t-border">
              Docs
            </Link>
            <CycleCountdown health={health} />
            <SystemHealth health={health} />
          </div>
        </div>
        {/* Indeterminate progress bar — shows only while switching to an uncached instance */}
        {isSwitchingInstance && (
          <div className="absolute bottom-0 left-0 right-0 h-[2px] overflow-hidden">
            <div className="animate-progress-bar" />
          </div>
        )}
      </header>

      <div className="max-w-[1800px] mx-auto px-5 py-3 space-y-3 relative">
        <div className="space-y-3">
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
            label="Net P&L"
            value={fmtDollar(totalLiveNetPnl)}
            pnl={totalLiveNetPnl}
            tooltip="Open Value − Cash Spent + Cash P&L"
            onClick={() => setExpandedMetric(expandedMetric === "netpnl" ? null : "netpnl")}
            active={expandedMetric === "netpnl"}
          />
          <MetricCard
            label="Cash P&L"
            value={fmtDollar(totalCashPnl)}
            pnl={totalCashPnl}
            tooltip="Gains/losses locked in through completed sells: (sell_price − avg_entry) × qty_sold"
            onClick={() => setExpandedMetric(expandedMetric === "realized" ? null : "realized")}
            active={expandedMetric === "realized"}
          />
          <MetricCard
            label="Open Value"
            value={fmtDollar(totalOpenValue)}
            pnl={totalOpenValue}
            tooltip="Current market value of open shares: bid × qty"
            onClick={() => setExpandedMetric(expandedMetric === "unrealized" ? null : "unrealized")}
            active={expandedMetric === "unrealized"}
          />
          <MetricCard
            label="Cash Spent"
            value={fmtDollar(totalCashSpent)}
            tooltip="Total cost of your open shares: avg entry price × quantity held."
          />
          <MetricCard
            label="Markets"
            value={`${metrics.marketsTraded} / ${metrics.openPositions}`}
            sub="markets / positions"
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

        {/* Net P&L calculation breakdown */}
        {expandedMetric === "netpnl" && (
          <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
            <div className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest mb-2">Net P&L Calculation</div>
            <div className="text-[11px] font-mono space-y-1">
              <div className="flex justify-between">
                <span className="text-txt-muted">Open Value <span className="text-[9px]">(bid × qty)</span></span>
                <span className={totalOpenValue >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(totalOpenValue)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-txt-muted">Cash Spent <span className="text-[9px]">(avg × qty)</span></span>
                <span className="text-loss">−{fmtDollar(totalCashSpent)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-txt-muted">Cash P&L <span className="text-[9px]">(realized)</span></span>
                <span className={totalCashPnl >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(totalCashPnl)}</span>
              </div>
              <div className="flex justify-between border-t border-t-border pt-1 mt-1">
                <span className="text-txt-primary font-medium">= Net P&L</span>
                <span className={`font-medium ${totalLiveNetPnl >= 0 ? "text-profit" : "text-loss"}`}>{fmtDollar(totalLiveNetPnl)}</span>
              </div>
            </div>
          </div>
        )}

        {/* Expandable P&L breakdown */}
        {expandedMetric && expandedMetric !== "netpnl" && (
          <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest">
                {expandedMetric === "realized" ? "Realized P&L Breakdown" : "Unrealized P&L Breakdown"}
              </span>
              <span className="text-[9px] text-txt-muted font-mono">
                {expandedMetric === "realized"
                  ? "(sell_price − avg_entry) × qty_sold"
                  : "current_bid × open_qty"}
              </span>
            </div>
            {expandedMetric === "realized" && (
              realizedBreakdown.length === 0
                ? <p className="text-[10px] text-txt-muted font-mono">No realized trades yet.</p>
                : <table className="w-full text-[10px] font-mono">
                    <thead>
                      <tr className="text-txt-muted border-b border-t-border">
                        <th className="text-left pb-1 font-medium">Market</th>
                        <th className="text-center pb-1 font-medium w-12">Side</th>
                        <th className="text-left pb-1 font-medium pl-4">Calculation</th>
                        <th className="text-right pb-1 font-medium w-20">Realized</th>
                      </tr>
                    </thead>
                    <tbody>
                      {realizedBreakdown.map((row, i) => (
                        <tr key={i} className="border-b border-t-border/40 last:border-0">
                          <td className="py-1.5 pr-3 text-txt-primary truncate max-w-[300px]">{row.title}</td>
                          <td className="py-1.5 text-center">
                            <span className={`px-1 rounded text-[8px] font-bold ${row.contract.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                              {row.contract.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-1.5 pl-4 text-txt-muted space-y-0.5">
                            {row.sells.length > 0
                              ? <>
                                  {row.sells.map((s, j) => (
                                    <div key={j}>
                                      ({Math.round(s.sellPrice * 100)}¢ − {Math.round(s.avgAtSell * 100)}¢) × {s.qty} = <span className={s.contribution >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(s.contribution)}</span>
                                    </div>
                                  ))}
                                  {row.hasMoreTrades && <div className="text-txt-muted/50 text-[9px]">* additional historical trades not shown</div>}
                                </>
                              : <span className="text-txt-muted/50">no sell trades in current view</span>
                            }
                          </td>
                          <td className={`py-1.5 text-right ${row.value >= 0 ? "text-profit" : "text-loss"}`}>
                            {fmtDollar(row.value)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
            )}
            {expandedMetric === "unrealized" && (
              unrealizedBreakdown.length === 0
                ? <p className="text-[10px] text-txt-muted font-mono">No open positions.</p>
                : <table className="w-full text-[10px] font-mono">
                    <thead>
                      <tr className="text-txt-muted border-b border-t-border">
                        <th className="text-left pb-1 font-medium">Market</th>
                        <th className="text-center pb-1 font-medium w-12">Side</th>
                        <th className="text-left pb-1 font-medium pl-4">Calculation</th>
                        <th className="text-right pb-1 font-medium w-20">Unrealized</th>
                      </tr>
                    </thead>
                    <tbody>
                      {unrealizedBreakdown.map((row, i) => (
                        <tr key={i} className="border-b border-t-border/40 last:border-0">
                          <td className="py-1.5 pr-3 text-txt-primary truncate max-w-[300px]">{row.title}</td>
                          <td className="py-1.5 text-center">
                            <span className={`px-1 rounded text-[8px] font-bold ${row.contract.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                              {row.contract.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-1.5 pl-4 text-txt-muted">
                            {Math.round(row.currentBid * 100)}¢ × {row.quantity} shares = <span className="text-profit">{fmtDollar(row.value)}</span>
                          </td>
                          <td className={`py-1.5 text-right ${row.value >= 0 ? "text-profit" : "text-loss"}`}>
                            {fmtDollar(row.value)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
            )}
          </div>
        )}

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
            <div className="flex items-center gap-1.5 mb-1.5">
              {[
                { key: "risk" as const, label: "Risk & Performance", count: undefined },
                { key: "alerts" as const, label: "Alerts", count: alerts.length > 0 ? alerts.length : undefined },
                { key: "activity" as const, label: "System Activity", count: undefined },
              ].map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  onClick={() => setSupportTab(tab.key)}
                  className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
                    supportTab === tab.key
                      ? "bg-accent/20 text-accent"
                      : "text-txt-muted hover:text-txt-primary hover:bg-t-panel"
                  }`}
                >
                  {tab.label}
                  {tab.count != null && (
                    <span className="ml-1 rounded bg-t-border px-1.5 py-px font-mono text-[9px] text-txt-muted">
                      {tab.count}
                    </span>
                  )}
                </button>
              ))}
            </div>
            {supportTab === "risk" && <RiskMetrics analytics={analytics} />}
            {supportTab === "alerts" && (
              <AlertsPanel
                alerts={alerts}
                onAlertClick={focusMarket}
                onAlertClear={clearAlert}
                onClearAll={clearAllAlerts}
                clearingAlertKey={clearingAlertKey}
                clearingAll={clearingAll}
              />
            )}
            {supportTab === "activity" && <LiveActivity logs={logs} />}
          </div>
        </div>

        {/* Row 3: P&L Attribution — commented out, reinstate when needed
        <div>
          <SectionLabel text="P&L Attribution" />
          <PnLAttribution analytics={analytics} />
        </div>
        */}

        {/* Row 4: Market Views */}
        <div>
          <div className="flex items-center gap-1.5 mb-1.5">
            {[
              { key: "activity" as const, label: "Market Activity", count: markets.length > 0 ? markets.length : undefined },
              { key: "heatmap" as const, label: "Position Heatmap", count: positions.length > 0 ? positions.length : undefined },
            ].map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setMarketViewTab(tab.key)}
                className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
                  marketViewTab === tab.key
                    ? "bg-accent/20 text-accent"
                    : "text-txt-muted hover:text-txt-primary hover:bg-t-panel"
                }`}
              >
                {tab.label}
                {tab.count != null && (
                  <span className="ml-1 rounded bg-t-border px-1.5 py-px font-mono text-[9px] text-txt-muted">
                    {tab.count}
                  </span>
                )}
              </button>
            ))}
          </div>
          {marketViewTab === "activity" && (
            <UnifiedMarketTable
              key={selectedInstance.key}
              markets={markets}
              positions={positions}
              trades={trades}
              apiClient={instanceApi}
              instanceCacheKey={selectedInstance.key}
              scrollToMarketId={scrollToMarketId}
              onScrollComplete={() => setScrollToMarketId(null)}
            />
          )}
          {marketViewTab === "heatmap" && (
            positions.length > 0 ? (
              <PositionHeatmap
                positions={positions}
                markets={markets}
                pnlByMarket={livePnlByMarket}
                onCellClick={focusMarket}
              />
            ) : (
              <div className="bg-t-panel border border-t-border rounded p-6 text-center text-txt-muted text-[10px]">
                No positions to visualize
              </div>
            )
          )}
        </div>

        {/* Row 5: Resolved Markets */}
        <div>
          <SectionLabel text="Resolved Markets" />
          <ModelCalibration resolvedMarkets={resolvedMarkets} />
        </div>

        </div>
      </div>
    </main>
  );
}

function InstanceTabs({
  instances,
  selectedKey,
  loadingKey,
  onSelect,
}: {
  instances: DashboardInstance[];
  selectedKey: string;
  loadingKey: string | null;
  onSelect: (key: string) => void;
}) {
  if (instances.length <= 1) return null;

  return (
    <div className="flex items-center gap-1 rounded border border-t-border bg-t-panel-hover/70 p-0.5">
      {instances.map((instance) => {
        const active = instance.key === selectedKey;
        const loading = loadingKey === instance.key;
        return (
          <button
            key={instance.key}
            type="button"
            onClick={() => onSelect(instance.key)}
            disabled={loading}
            className={`rounded px-2 py-1 text-[10px] font-medium transition-colors ${
              active
                ? "bg-accent text-black"
                : "text-txt-muted hover:text-txt-primary hover:bg-t-panel"
            }`}
            title={instance.description || instance.apiUrl}
          >
            <span className="inline-flex items-center gap-1">
              {loading && (
                <span className="h-2.5 w-2.5 rounded-full border border-current border-t-transparent animate-spin" />
              )}
              {instance.label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function InfoDot({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0, below: false });

  return (
    <span
      ref={ref}
      className="inline-flex items-center justify-center w-3 h-3 ml-1 rounded border border-txt-muted/30 text-[7px] text-txt-muted cursor-help hover:border-accent hover:text-accent transition-colors align-middle"
      onMouseEnter={() => {
        if (ref.current) {
          const r = ref.current.getBoundingClientRect();
          const cx = r.left + r.width / 2;
          const left = Math.max(8, Math.min(cx - 130, window.innerWidth - 268));
          // Show below if not enough room above (assume tooltip up to ~120px tall)
          const below = r.top < 130;
          const top = below ? r.bottom + 6 : r.top - 8;
          setPos({ top, left, below });
        }
        setShow(true);
      }}
      onMouseLeave={() => setShow(false)}
    >
      ?
      {show && (
        <span
          className={`fixed w-max max-w-[260px] whitespace-normal rounded border border-t-border bg-[#141a22] px-3 py-2 text-[10px] text-left font-mono font-normal normal-case tracking-normal leading-snug text-txt-primary shadow-xl z-[9999] pointer-events-none ${pos.below ? "" : "-translate-y-full"}`}
          style={{ top: pos.top, left: pos.left }}
        >
          {text}
        </span>
      )}
    </span>
  );
}

function MetricCard({
  label,
  value,
  pnl,
  sub,
  tooltip,
  onClick,
  active,
}: {
  label: string;
  value: string;
  pnl?: number;
  sub?: string;
  tooltip?: string;
  onClick?: () => void;
  active?: boolean;
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
    <div
      className={`bg-t-panel border rounded px-2.5 py-2 transition-colors ${onClick ? "cursor-pointer" : ""} ${active ? "border-accent/60 bg-t-panel-hover" : "border-t-border hover:bg-t-panel-hover"}`}
      onClick={onClick}
    >
      <div className="text-[9px] text-txt-muted uppercase tracking-widest font-medium leading-none flex items-center">
        {label}
        {tooltip && <InfoDot text={tooltip} />}
        {onClick && <span className="ml-auto text-[8px] text-txt-muted/50">{active ? "▲" : "▼"}</span>}
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

  const cycleEndStr = health?.effective_last_cycle_end ?? health?.last_cycle_end;
  if (!cycleEndStr || !health?.poll_interval_sec) {
    return (
      <span className="text-[10px] text-txt-muted font-mono">
        Next cycle: --:--
      </span>
    );
  }

  const cycleEndMs = new Date(cycleEndStr).getTime();
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
