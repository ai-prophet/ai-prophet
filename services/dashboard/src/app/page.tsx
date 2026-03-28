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
  type KalshiPositionsData,
  type AnalyticsSummary,
  type ResolvedMarketsData,
  type Alert,
  type DisplayBaselineData,
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
import { OrderMonitoringPanel } from "@/components/OrderMonitoringPanel";

const REFRESH_INTERVAL = 5_000;
const INSTANCE_STORAGE_KEY = "dashboard-instance-key";
const DISPLAY_CUTOFF_MS = new Date("2026-03-24T18:00:00-05:00").getTime();
const SYNTHETIC_BACKFILL_SOURCE_PREFIX = "kalshi:";
const WIN_RATE_TOOLTIP =
  "A win means positive realized P&L. Losses have negative realized P&L. Zero realized P&L counts as neither, so this is not the same as final market resolution.";

type DashboardSnapshot = {
  trades: Trade[];
  markets: Market[];
  positions: Position[];
  pnl: PnLData | null;
  health: HealthData | null;
  logs: SystemLogEntry[];
  balance: KalshiBalanceData | null;
  kalshiPositions: KalshiPositionsData | null;
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

function isOnOrAfterDisplayCutoff(timestamp: string | null | undefined): boolean {
  if (!timestamp) return false;
  return new Date(timestamp).getTime() >= DISPLAY_CUTOFF_MS;
}

function shouldDisplayTrade(trade: Trade): boolean {
  const status = trade.status?.toUpperCase() ?? "";
  if (status === "PENDING") return true;
  return isOnOrAfterDisplayCutoff(trade.created_at);
}

function filterTradesForDisplay(trades: Trade[]): Trade[] {
  return trades.filter(shouldDisplayTrade);
}

function isSyntheticBackfillTrade(trade: Trade): boolean {
  const source = trade.prediction?.source?.toLowerCase() ?? "";
  return source.startsWith(SYNTHETIC_BACKFILL_SOURCE_PREFIX);
}

function parseFiniteNumber(value: unknown): number {
  const parsed = Number(value ?? 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function isResolvedMarketResult(result: string | null | undefined): boolean {
  const normalized = (result ?? "").trim().toLowerCase();
  return normalized === "yes" || normalized === "no";
}

function isArchivedLifecycleStatus(status: string | null | undefined): boolean {
  const normalized = (status ?? "").trim().toLowerCase();
  return normalized === "closed" || normalized === "inactive" || normalized === "finalized";
}

function filterDashboardSubset(markets: Market[], positions: Position[], trades: Trade[]) {
  const marketIds = new Set(markets.map((market) => market.market_id));
  const tickers = new Set(markets.map((market) => market.ticker).filter((ticker) => !!ticker));

  return {
    positions: positions.filter((position) =>
      marketIds.has(position.market_id) || (position.ticker != null && tickers.has(position.ticker))
    ),
    trades: trades.filter((trade) =>
      tickers.has(trade.ticker) || (trade.prediction?.market_id != null && marketIds.has(trade.prediction.market_id))
    ),
  };
}

function inferLivePositionFee(position: KalshiPositionsData["positions"][number]): number {
  const directFee = parseFiniteNumber((position as any).fees_paid_dollars);
  if (directFee > 0) return directFee;

  const qty = Math.abs(parseFiniteNumber((position as any).position_fp));
  const avgPrice = parseFiniteNumber(
    (position as any).average_price_dollars
    ?? (position as any).avg_price_dollars
    ?? (position as any).avg_price
  );
  const actualPaid = parseFiniteNumber(
    (position as any).market_exposure_dollars
    ?? (position as any).market_exposure
  );

  const impliedFee = actualPaid - (qty * avgPrice);
  return impliedFee > 0 ? impliedFee : 0;
}

function reconcileBackfillTradeFees(
  trades: Trade[],
  livePositions: KalshiPositionsData | null,
): Trade[] {
  if (!livePositions?.positions?.length || trades.length === 0) return trades;

  const liveFeesByTicker = new Map<string, number>();
  for (const position of livePositions.positions) {
    const ticker = typeof position.ticker === "string" ? position.ticker : null;
    if (!ticker) continue;
    const fee = inferLivePositionFee(position);
    if (fee > 0) liveFeesByTicker.set(ticker, fee);
  }
  if (liveFeesByTicker.size === 0) return trades;

  const candidateIndexesByTicker = new Map<string, number[]>();
  trades.forEach((trade, index) => {
    const status = trade.status?.toUpperCase() ?? "";
    if (trade.dry_run || trade.fee_paid > 0 || !isSyntheticBackfillTrade(trade) || status !== "FILLED") {
      return;
    }
    const existing = candidateIndexesByTicker.get(trade.ticker) ?? [];
    existing.push(index);
    candidateIndexesByTicker.set(trade.ticker, existing);
  });

  if (candidateIndexesByTicker.size === 0) return trades;

  const nextTrades = [...trades];
  let changed = false;

  for (const [ticker, indexes] of Array.from(candidateIndexesByTicker.entries())) {
    const liveFee = liveFeesByTicker.get(ticker);
    if (!liveFee || liveFee <= 0) continue;

    const weights = indexes.map((index) => {
      const trade = trades[index];
      return Math.max(trade.filled_shares || trade.count || 0, 1);
    });
    const totalWeight = weights.reduce((sum, weight) => sum + weight, 0);
    if (totalWeight <= 0) continue;

    let allocated = 0;
    indexes.forEach((index, idx) => {
      const isLast = idx === indexes.length - 1;
      const inferredFee = isLast
        ? Math.max(0, liveFee - allocated)
        : Math.round((liveFee * (weights[idx] / totalWeight)) * 10000) / 10000;
      allocated += inferredFee;
      nextTrades[index] = { ...nextTrades[index], fee_paid: inferredFee };
      changed = true;
    });
  }

  return changed ? nextTrades : trades;
}

function filterPnlForDisplay(pnlData: PnLData | null): PnLData | null {
  if (!pnlData) return null;

  const series = pnlData.series.filter((point) => isOnOrAfterDisplayCutoff(point.timestamp));
  const trade_markers = pnlData.trade_markers.filter((marker) =>
    isOnOrAfterDisplayCutoff(marker.timestamp)
  );

  return {
    ...pnlData,
    series,
    trade_markers,
    summary: {
      ...pnlData.summary,
      total_pnl: series.length > 0 ? series[series.length - 1].pnl : 0,
      total_trades: trade_markers.length,
      total_volume: trade_markers.reduce((sum, marker) => sum + marker.count, 0),
    },
  };
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
  const [kalshiPositions, setKalshiPositions] = useState<KalshiPositionsData | null>(null);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [resolvedMarkets, setResolvedMarkets] = useState<ResolvedMarketsData | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [displayBaseline, setDisplayBaseline] = useState<DisplayBaselineData | null>(null);
  const [clearingAlertKey, setClearingAlertKey] = useState<string | null>(null);
  const [clearingAll, setClearingAll] = useState(false);
  const [lastUpdate, setLastUpdate] = useState<string>("");
  const [error, setError] = useState<string>("");
  const [refreshing, setRefreshing] = useState(false);
  const [scrollToMarketId, setScrollToMarketId] = useState<string | null>(null);
  const [supportTab, setSupportTab] = useState<"risk" | "alerts" | "activity" | "monitoring">("risk");
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
      const currentUnitValue = mkt?.last_price != null
        ? (pos.contract.toLowerCase() === "yes" ? mkt.last_price : 1 - mkt.last_price)
        : (
          pos.market_exposure != null && pos.quantity > 0
            ? pos.market_exposure / pos.quantity
            : null
        );
      if (currentUnitValue != null && pos.total_cost != null) {
        map.set(
          pos.market_id,
          (currentUnitValue * pos.quantity) - pos.total_cost + (pos.realized_pnl ?? 0)
        );
        continue;
      }

      const mktTrades = tradesByTicker.get(pos.ticker ?? "") ?? [];
      const cashFlow = mktTrades.reduce((sum, t) => {
        const qty = t.filled_shares || t.count;
        const price = t.price_cents / 100;
        const fee = t.fee_paid || 0;
        return sum + (t.action?.toUpperCase() === "SELL" ? (qty * price) - fee : -((qty * price) + fee));
      }, 0);
      const fallbackValue = currentUnitValue != null ? pos.quantity * currentUnitValue : 0;
      map.set(pos.market_id, cashFlow + fallbackValue);
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
    setKalshiPositions(snapshot.kalshiPositions);
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
    setKalshiPositions(null);
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
      instanceApi.getDisplayBaseline().then((baseline) => {
        if (activeRequestRef.current !== requestId || !baseline) return;
        setDisplayBaseline(baseline);
      }).catch(() => {});

      // Tier 1: Critical data — renders header, metrics, markets, alerts immediately
      const [t, m, posData, h, b, kp, al] = await Promise.all([
        instanceApi.getTrades(500),
        instanceApi.getMarkets(200),
        instanceApi.getPositions(200),
        instanceApi.getHealth(),
        instanceApi.getKalshiBalance().catch((e) => {
          console.error(`Failed to fetch balance for ${instanceKey}:`, e);
          return null;
        }),
        instanceApi.getKalshiPositions().catch((e) => {
          console.error(`Failed to fetch live Kalshi positions for ${instanceKey}:`, e);
          return null;
        }),
        instanceApi.getAlerts(),
      ]);
      if (activeRequestRef.current !== requestId) return;
      const displayTrades = reconcileBackfillTradeFees(filterTradesForDisplay(t), kp);

      // Apply Tier 1 immediately so the page renders
      const cached = dataCacheRef.current[instanceKey];
      // Filter out "Large edge detected" alerts
      const filteredAlerts = al.alerts.filter(
        (alert) => !alert.message.toLowerCase().includes("large edge detected")
      );
      const tier1Snapshot: DashboardSnapshot = {
        trades: displayTrades,
        markets: m,
        positions: posData.positions,
        pnl: cached?.pnl ?? null,
        health: h,
        logs: cached?.logs ?? [],
        balance: b,
        kalshiPositions: kp,
        analytics: cached?.analytics ?? null,
        resolvedMarkets: cached?.resolvedMarkets ?? null,
        alerts: filteredAlerts,
        lastUpdate: formatLastUpdateTime(),
      };
      dataCacheRef.current[instanceKey] = tier1Snapshot;
      applySnapshot(tier1Snapshot);
      setError("");
      setLoadingInstanceKey((current) => (current === instanceKey ? null : current));

      // Tier 2: Heavy analytics — start all in parallel, update UI as each completes
      instanceApi.getSystemLogs(40).then(l => {
        if (activeRequestRef.current !== requestId) return;
        const current = dataCacheRef.current[instanceKey];
        if (current) {
          const updated = { ...current, logs: l, lastUpdate: formatLastUpdateTime() };
          dataCacheRef.current[instanceKey] = updated;
          applySnapshot(updated);
        }
      }).catch(() => {});

      instanceApi.getPnL().then(pnlData => {
        if (activeRequestRef.current !== requestId) return;
        const current = dataCacheRef.current[instanceKey];
        if (current) {
          const updated = {
            ...current,
            pnl: filterPnlForDisplay(pnlData),
            lastUpdate: formatLastUpdateTime(),
          };
          dataCacheRef.current[instanceKey] = updated;
          applySnapshot(updated);
        }
      }).catch(() => {});

      instanceApi.getAnalyticsSummary().then(an => {
        if (activeRequestRef.current !== requestId) return;
        const current = dataCacheRef.current[instanceKey];
        if (current) {
          const updated = { ...current, analytics: an, lastUpdate: formatLastUpdateTime() };
          dataCacheRef.current[instanceKey] = updated;
          applySnapshot(updated);
        }
      }).catch(() => {});

      instanceApi.getResolvedMarkets().then(resolved => {
        if (activeRequestRef.current !== requestId) return;
        const current = dataCacheRef.current[instanceKey];
        if (current) {
          const updated = { ...current, resolvedMarkets: resolved, lastUpdate: formatLastUpdateTime() };
          dataCacheRef.current[instanceKey] = updated;
          applySnapshot(updated);
        }
      }).catch(() => {});
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
    // Cancel any pending requests when switching instances
    activeRequestRef.current++;

    const cachedSnapshot = dataCacheRef.current[selectedInstance.key];
    if (cachedSnapshot) {
      // Apply cached data but clear balance to force fresh fetch
      const clearedSnapshot = {
        ...cachedSnapshot,
        balance: null,  // Clear balance to show loading state
        pnl: null,      // Clear P&L as well since it's instance-specific
      };
      // Update the cache to reflect cleared values
      dataCacheRef.current[selectedInstance.key] = clearedSnapshot;
      applySnapshot(clearedSnapshot);
    } else {
      // No cache - clear everything
      clearSnapshot();
    }
    setError("");
    fetchAll();
    intervalRef.current = setInterval(fetchAll, REFRESH_INTERVAL);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      // Cancel any pending requests when unmounting or switching
      activeRequestRef.current++;
    };
  }, [applySnapshot, clearSnapshot, fetchAll, selectedInstance.key]);

  const metrics = computePortfolioMetrics(
    positions,
    trades,
    pnl,
    markets,
    displayBaseline?.starting_total ?? null,
  );
  const liveActiveMarketCount = useMemo(() => {
    if (!kalshiPositions?.positions) return metrics.marketsTraded;

    const activeTickers = new Set<string>();
    for (const position of kalshiPositions.positions) {
      const ticker = typeof position.ticker === "string" ? position.ticker : null;
      if (!ticker) continue;

      const positionFp = Number((position as any).position_fp ?? 0);
      const restingOrders = Number(position.resting_orders_count ?? 0);
      const exposure = Number((position as any).market_exposure_dollars ?? (position as any).market_exposure ?? 0);

      if (Math.abs(positionFp) > 0 || restingOrders > 0 || exposure > 0) {
        activeTickers.add(ticker);
      }
    }

    return activeTickers.size;
  }, [kalshiPositions, metrics.marketsTraded]);

  const totalFeesPaid = useMemo(() => {
    return trades.reduce((sum, trade) => sum + (trade.fee_paid || 0), 0);
  }, [trades]);

  const archivedMarkets = useMemo(
    () => markets.filter((market) =>
      !isResolvedMarketResult(market.market_result) && isArchivedLifecycleStatus(market.market_status)
    ),
    [markets]
  );
  const resolvedTrackedMarkets = useMemo(
    () => markets.filter((market) => isResolvedMarketResult(market.market_result)),
    [markets]
  );
  const hiddenMarketIds = useMemo(
    () => new Set([...archivedMarkets, ...resolvedTrackedMarkets].map((market) => market.market_id)),
    [archivedMarkets, resolvedTrackedMarkets]
  );
  const activeMarkets = useMemo(
    () => markets.filter((market) => !hiddenMarketIds.has(market.market_id)),
    [markets, hiddenMarketIds]
  );
  const activeDashboardData = useMemo(
    () => filterDashboardSubset(activeMarkets, positions, trades),
    [activeMarkets, positions, trades]
  );
  const archivedDashboardData = useMemo(
    () => filterDashboardSubset(archivedMarkets, positions, trades),
    [archivedMarkets, positions, trades]
  );

  const unifiedRows = buildUnifiedMarketRows(markets, positions, trades);
  const [expandedMetric, setExpandedMetric] = useState<"equity" | "equitypnl" | "unrealized" | "winrate" | "fees" | null>(null);

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
    const sells: { qty: number; sellPrice: number; avgAtSell: number; contribution: number; feePaid: number }[] = [];
    let totalRealized = 0;
    for (const t of relevant) {
      const qty = t.filled_shares || t.count;
      let price = t.price_cents / 100;
      if (price > 1.0) price /= 100; // fix corrupted fill_price stored as cents
      const fee = t.fee_paid || 0;
      const action = (t.action ?? "BUY").toUpperCase();
      const isYes = (t.side ?? "yes").toLowerCase() === "yes";
      if (action === "SELL") {
        const avgAtSell = Math.abs(netShares) > 0.001 ? Math.abs(totalCost / netShares) : 0;
        const contribution = (price - avgAtSell) * qty - fee;
        totalRealized += contribution;
        sells.push({ qty, sellPrice: price, avgAtSell, contribution, feePaid: fee });
        if (isYes) { netShares -= qty; totalCost -= avgAtSell * qty; }
        else { netShares += qty; totalCost += avgAtSell * qty; }
        if (Math.abs(netShares) < 0.001) { netShares = 0; totalCost = 0; }
      } else {
        if (isYes) { netShares += qty; totalCost += qty * price + fee; }
        else { netShares -= qty; totalCost -= qty * price + fee; }
      }
    }
    const remainingQty = Math.abs(netShares);
    const remainingAvgPrice = remainingQty > 0.001 ? Math.abs(totalCost / netShares) : 0;
    const remainingSide = netShares >= 0 ? "yes" : "no";
    return { sells, totalRealized, remainingQty, remainingAvgPrice, remainingSide };
  }

  // Single pass — prefer Kalshi-backed position values when available, and only
  // fall back to local replay/quote math when the API does not yet provide them.
  let totalOpenValue = 0;
  const seenMarketIds = new Set<string>();

  type PerMarketResult = {
    title: string;
    contract: string;
    totalRealized: number;
    avgEntry: number;
    currentUnitValue: number | null;
    dbQty: number;
    openValue: number;
    feeTotal: number;
  };
  const perMarket: PerMarketResult[] = [];

  for (const row of unifiedRows) {
    if (seenMarketIds.has(row.market_id)) continue;
    seenMarketIds.add(row.market_id);

    const { totalRealized, remainingAvgPrice } = replayTrades(row.trades);
    const dbQty = row.position?.quantity ?? 0;
    const contract = row.position?.contract ?? (row.trades[0]?.side ?? "yes");
    const avgEntry = row.position?.avg_price ?? (row.trades.length > 0 ? remainingAvgPrice : 0);
    const realizedValue = row.position?.realized_pnl ?? (row.trades.length > 0 ? totalRealized : 0);

    const mkt = marketById.get(row.market_id);
    let currentUnitValue: number | null = null;
    if (mkt?.last_price != null && dbQty > 0.001) {
      currentUnitValue = contract === "yes"
        ? mkt.last_price
        : 1.0 - mkt.last_price;
    }
    const openValue = currentUnitValue != null
      ? currentUnitValue * dbQty
      : (row.position?.market_exposure ?? 0);
    if (dbQty > 0.001 && currentUnitValue == null && row.position?.market_exposure != null) {
      currentUnitValue = openValue / dbQty;
    }
    totalOpenValue += openValue;
    const feeTotal = row.position?.fees_paid ?? row.fees_paid_total ?? 0;

    perMarket.push({
      title: row.title,
      contract,
      totalRealized: realizedValue,
      avgEntry,
      currentUnitValue,
      dbQty,
      openValue,
      feeTotal,
    });
  }

  const displayedOpenValue = analytics?.open_value ?? totalOpenValue;
  const cashBalance =
    analytics?.cash_balance
    ?? (balance == null ? null : balance.balance);
  const displayedStartingTotal = analytics?.starting_total ?? displayBaseline?.starting_total ?? null;
  const displayedCurrentEquity = cashBalance != null ? cashBalance + displayedOpenValue : null;
  const hasEquityPnl = displayedStartingTotal != null && displayedCurrentEquity != null;
  const displayedEquityPnl = hasEquityPnl
    ? displayedCurrentEquity - displayedStartingTotal
    : (analytics?.net_pnl ?? metrics.totalPnl);
  const displayedFeesPaid = analytics?.total_fees ?? totalFeesPaid;
  const displayedActiveMarkets = analytics?.active_markets ?? liveActiveMarketCount;
  const displayedOpenPositions = analytics?.open_positions ?? liveActiveMarketCount;
  const displayedWinRate = analytics?.win_rate ?? metrics.winRate;
  const displayedReturnPct =
    hasEquityPnl && displayedStartingTotal > 1e-9
      ? (displayedEquityPnl / displayedStartingTotal) * 100
      : (analytics ? analytics.return_pct * 100 : metrics.avgReturn);
  const formatSignedTerm = (value: number) => `${value >= 0 ? "+" : "-"}${fmtDollar(Math.abs(value))}`;

  const unrealizedBreakdown = perMarket
    .filter((r) => r.dbQty > 0.001 && r.currentUnitValue != null)
    .map((r) => ({ title: r.title, contract: r.contract, quantity: r.dbQty, avgEntry: r.avgEntry, currentUnitValue: r.currentUnitValue!, value: r.openValue }))
    .sort((a, b) => b.value - a.value);
  const openValueAdjustment = displayedOpenValue - totalOpenValue;
  const hasOpenValueAdjustment = Math.abs(openValueAdjustment) > 0.005;
  const openValueBreakdown = hasOpenValueAdjustment
    ? [
        ...unrealizedBreakdown,
        {
          title: "Kalshi Sync Adjustment",
          contract: "sync",
          quantity: 0,
          avgEntry: 0,
          currentUnitValue: 0,
          value: openValueAdjustment,
        },
      ]
    : unrealizedBreakdown;

  // Win rate breakdown: show markets with wins/losses
  const winRateBreakdown = perMarket
    .filter((r) => r.totalRealized !== 0) // Only markets with realized P&L
    .map((r) => ({
      title: r.title,
      contract: r.contract,
      value: r.totalRealized,
      isWin: r.totalRealized > 0,
    }))
    .sort((a, b) => Math.abs(b.value) - Math.abs(a.value)); // Sort by absolute value

  const feesBreakdown = perMarket
    .filter((r) => r.feeTotal > 0)
    .map((r) => ({
      title: r.title,
      contract: r.contract,
      value: r.feeTotal,
    }))
    .sort((a, b) => b.value - a.value);

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

      {isSwitchingInstance ? (
        <div className="flex flex-col items-center justify-center min-h-[70vh] gap-4">
          <div className="relative w-10 h-10">
            <div className="absolute inset-0 rounded-full border-2 border-t-border" />
            <div className="absolute inset-0 rounded-full border-2 border-t-transparent border-accent animate-spin" />
          </div>
          <span className="text-xs font-mono text-txt-muted tracking-wider">
            Loading {selectedInstance.label}…
          </span>
        </div>
      ) : (
      <div className="max-w-[1800px] mx-auto px-5 py-3 space-y-3 relative">
        <div className="space-y-3">
        {error && (
          <div className="bg-loss-dim border border-loss/20 text-loss px-3 py-2 rounded text-xs font-mono">
            {error}
          </div>
        )}

        {/* Row 1: Portfolio Summary Metrics */}
        <div className="grid grid-cols-3 md:grid-cols-5 xl:grid-cols-10 gap-2">
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
            label="Open Value"
            value={fmtDollar(displayedOpenValue)}
            pnl={displayedOpenValue}
            tooltip="Headline uses the synced Kalshi portfolio valuation. The breakdown shows per-market marks plus a Kalshi sync adjustment when needed."
            onClick={() => setExpandedMetric(expandedMetric === "unrealized" ? null : "unrealized")}
            active={expandedMetric === "unrealized"}
          />
          <MetricCard
            label="Current Equity"
            value={displayedCurrentEquity != null ? fmtDollar(displayedCurrentEquity) : "--"}
            tooltip="Cash Balance + Open Value"
            onClick={() => setExpandedMetric(expandedMetric === "equity" ? null : "equity")}
            active={expandedMetric === "equity"}
          />
          <MetricCard
            label="Equity P&L"
            value={fmtDollar(displayedEquityPnl)}
            pnl={displayedEquityPnl}
            tooltip="Current Equity − Starting Balance"
            onClick={() => setExpandedMetric(expandedMetric === "equitypnl" ? null : "equitypnl")}
            active={expandedMetric === "equitypnl"}
          />
          <MetricCard
            label="Markets"
            value={`${displayedActiveMarkets} / ${displayedOpenPositions}`}
            sub="markets / positions"
          />
          <MetricCard
            label="Win Rate"
            value={`${(displayedWinRate * 100).toFixed(0)}%`}
            pnl={displayedWinRate >= 0.5 ? 1 : -1}
            tooltip={WIN_RATE_TOOLTIP}
            onClick={() => setExpandedMetric(expandedMetric === "winrate" ? null : "winrate")}
            active={expandedMetric === "winrate"}
          />
          <MetricCard
            label="Return"
            value={`${displayedReturnPct >= 0 ? "+" : ""}${displayedReturnPct.toFixed(1)}%`}
            pnl={displayedReturnPct}
          />
          <MetricCard
            label="Total Fees"
            value={fmtDollar(displayedFeesPaid)}
            pnl={-Math.abs(displayedFeesPaid)}
            tooltip="Total fees paid across recorded trades in the current display window."
            onClick={() => setExpandedMetric(expandedMetric === "fees" ? null : "fees")}
            active={expandedMetric === "fees"}
          />
          <MetricCard
            label="Max DD"
            value={analytics ? fmtDollar(analytics.max_drawdown) : "--"}
            pnl={analytics ? -Math.abs(analytics.max_drawdown) : undefined}
          />
          <MetricCard
            label="Starting Balance"
            value={displayedStartingTotal != null ? fmtDollar(displayedStartingTotal) : "--"}
            sub={displayBaseline ? `${displayBaseline.instance_name} baseline` : undefined}
            tooltip={displayBaseline ? `Hardcoded baseline for ${displayBaseline.instance_name}. Loaded amount was ${fmtDollar(displayBaseline.initial_loaded)}.` : "Per-run starting balance baseline used for return calculations."}
          />
        </div>

        {/* Current equity breakdown */}
        {expandedMetric === "equity" && (
          <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
            <div className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest mb-2">Current Equity Calculation</div>
            <div className="text-[11px] font-mono space-y-1">
              {displayedCurrentEquity != null ? (
                <>
                  <div className="flex justify-between">
                    <span className="text-txt-muted">Cash Balance <span className="text-[9px]">(Kalshi cash)</span></span>
                    <span className={cashBalance != null && cashBalance >= 0 ? "text-profit" : "text-loss"}>
                      {formatSignedTerm(cashBalance ?? 0)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-txt-muted">Open Value <span className="text-[9px]">(synced Kalshi portfolio value)</span></span>
                    <span className={displayedOpenValue >= 0 ? "text-profit" : "text-loss"}>{formatSignedTerm(displayedOpenValue)}</span>
                  </div>
                  <div className="flex justify-between border-t border-t-border pt-1 mt-1">
                    <span className="text-txt-primary font-medium">= Current Equity</span>
                    <span className={`font-medium ${displayedCurrentEquity >= 0 ? "text-profit" : "text-loss"}`}>{fmtDollar(displayedCurrentEquity)}</span>
                  </div>
                </>
              ) : (
                <p className="text-[10px] text-txt-muted font-mono">
                  Waiting for the current cash balance so current equity can be computed from the displayed values.
                </p>
              )}
            </div>
          </div>
        )}

        {/* Equity P&L calculation breakdown */}
        {expandedMetric === "equitypnl" && (
          <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
            <div className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest mb-2">Equity P&L Calculation</div>
            <div className="text-[11px] font-mono space-y-1">
              {hasEquityPnl ? (
                <>
                  {(() => {
                    const baselineCashBalance = cashBalance ?? 0;
                    const baselineEquity = displayedCurrentEquity ?? 0;
                    const baselineStartingTotal = displayedStartingTotal ?? 0;
                    return (
                      <>
                  <div className="flex justify-between">
                    <span className="text-txt-muted">Cash Balance <span className="text-[9px]">(Kalshi cash)</span></span>
                    <span className={baselineCashBalance >= 0 ? "text-profit" : "text-loss"}>{formatSignedTerm(baselineCashBalance)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-txt-muted">Open Value <span className="text-[9px]">(synced Kalshi portfolio value)</span></span>
                    <span className={displayedOpenValue >= 0 ? "text-profit" : "text-loss"}>{formatSignedTerm(displayedOpenValue)}</span>
                  </div>
                  <div className="flex justify-between border-t border-t-border/50 pt-1 mt-1">
                    <span className="text-txt-muted">Current Equity</span>
                    <span className={baselineEquity >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(baselineEquity)}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-txt-muted">Starting Balance</span>
                    <span className="text-loss">{formatSignedTerm(-baselineStartingTotal)}</span>
                  </div>
                  <div className="flex justify-between border-t border-t-border pt-1 mt-1">
                    <span className="text-txt-primary font-medium">= Equity P&L</span>
                    <span className={`font-medium ${displayedEquityPnl >= 0 ? "text-profit" : "text-loss"}`}>{fmtDollar(displayedEquityPnl)}</span>
                  </div>
                      </>
                    );
                  })()}
                </>
              ) : (
                <p className="text-[10px] text-txt-muted font-mono">
                  Waiting for cash balance and starting balance so equity P&amp;L can be computed from the displayed values.
                </p>
              )}
            </div>
          </div>
        )}

        {/* Expandable supporting breakdowns */}
        {expandedMetric && expandedMetric !== "equity" && expandedMetric !== "equitypnl" && (
          <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest">
                {expandedMetric === "unrealized"
                    ? "Open Value Breakdown"
                    : expandedMetric === "fees"
                      ? "Fee Breakdown"
                      : "Win Rate Breakdown"}
              </span>
              <span className="text-[9px] text-txt-muted font-mono">
                {expandedMetric === "unrealized"
                    ? (hasOpenValueAdjustment ? "Per-market marks + Kalshi sync adjustment" : "Per-market marks")
                    : expandedMetric === "fees"
                      ? "Recorded by market, plus live Kalshi fee reconciliation if needed"
                      : "Markets with realized wins/losses"}
              </span>
            </div>
            {expandedMetric === "unrealized" && (
              openValueBreakdown.length === 0
                ? <p className="text-[10px] text-txt-muted font-mono">No open positions.</p>
	                : <>
	                    <table className="w-full text-[10px] font-mono">
	                      <thead>
	                        <tr className="text-txt-muted border-b border-t-border">
	                          <th className="text-left pb-1 font-medium">Market</th>
	                          <th className="text-center pb-1 font-medium w-12">Side</th>
	                          <th className="text-left pb-1 font-medium pl-4">Calculation</th>
	                          <th className="text-right pb-1 font-medium w-20">Open Value</th>
	                        </tr>
	                      </thead>
	                      <tbody>
	                        {openValueBreakdown.map((row, i) => (
	                          <tr key={i} className="border-b border-t-border/40 last:border-0">
	                            <td className="py-1.5 pr-3 text-txt-primary truncate max-w-[300px]">{row.title}</td>
	                            <td className="py-1.5 text-center">
	                              {row.contract === "sync" ? (
	                                <span className="px-1 rounded text-[8px] font-bold bg-accent-dim text-accent">
	                                  SYNC
	                                </span>
	                              ) : (
	                                <span className={`px-1 rounded text-[8px] font-bold ${row.contract.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
	                                  {row.contract.toUpperCase()}
	                                </span>
	                              )}
	                            </td>
	                            <td className="py-1.5 pl-4 text-txt-muted">
	                              {row.contract === "sync"
	                                ? <>synced Kalshi portfolio value − summed market marks = <span className={row.value >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(row.value)}</span></>
	                                : <>{Math.round(row.currentUnitValue * 100)}¢ × {row.quantity} shares = <span className="text-profit">{fmtDollar(row.value)}</span></>
	                              }
	                            </td>
	                            <td className={`py-1.5 text-right ${row.value >= 0 ? "text-profit" : "text-loss"}`}>
	                              {fmtDollar(row.value)}
	                            </td>
	                          </tr>
	                        ))}
	                      </tbody>
	                      <tfoot>
	                        <tr className="border-t border-t-border/60 text-[10px]">
	                          <td colSpan={3} className="py-1.5 pr-3 text-right font-medium text-txt-secondary">
	                            Total
	                          </td>
	                          <td className={`py-1.5 text-right font-medium ${displayedOpenValue >= 0 ? "text-profit" : "text-loss"}`}>
	                            {fmtDollar(displayedOpenValue)}
	                          </td>
	                        </tr>
	                      </tfoot>
	                    </table>
	                    {hasOpenValueAdjustment && (
	                      <p className="mt-2 text-[10px] text-txt-muted font-mono">
	                        Note: The sync adjustment reflects the gap between our per-market estimates and Kalshi&apos;s synced portfolio value. We do not know Kalshi&apos;s full internal valuation details, so the exact per-market calculation may differ.
	                      </p>
	                    )}
	                  </>
	            )}
            {expandedMetric === "winrate" && (
              winRateBreakdown.length === 0
                ? <p className="text-[10px] text-txt-muted font-mono">No closed positions yet.</p>
                : <>
                    <div className="grid grid-cols-2 gap-3 mb-3">
                      <div className="bg-profit-dim/20 border border-profit/30 rounded px-3 py-2">
                        <div className="text-[9px] text-txt-muted uppercase tracking-wider mb-1">Wins</div>
                        <div className="text-lg font-bold font-mono text-profit">
                          {winRateBreakdown.filter(r => r.isWin).length}
                        </div>
                        <div className="text-[9px] text-profit font-mono">
                          +{fmtDollar(winRateBreakdown.filter(r => r.isWin).reduce((sum, r) => sum + r.value, 0))}
                        </div>
                      </div>
                      <div className="bg-loss-dim/20 border border-loss/30 rounded px-3 py-2">
                        <div className="text-[9px] text-txt-muted uppercase tracking-wider mb-1">Losses</div>
                        <div className="text-lg font-bold font-mono text-loss">
                          {winRateBreakdown.filter(r => !r.isWin).length}
                        </div>
                        <div className="text-[9px] text-loss font-mono">
                          {fmtDollar(winRateBreakdown.filter(r => !r.isWin).reduce((sum, r) => sum + r.value, 0))}
                        </div>
                      </div>
                    </div>
                    <table className="w-full text-[10px] font-mono">
                      <thead>
                        <tr className="text-txt-muted border-b border-t-border">
                          <th className="text-left pb-1 font-medium">Market</th>
                          <th className="text-center pb-1 font-medium w-12">Side</th>
                          <th className="text-center pb-1 font-medium w-16">Result</th>
                          <th className="text-right pb-1 font-medium w-20">P&L</th>
                        </tr>
                      </thead>
                      <tbody>
                        {winRateBreakdown.map((row, i) => (
                          <tr key={i} className="border-b border-t-border/40 last:border-0">
                            <td className="py-1.5 pr-3 text-txt-primary truncate max-w-[300px]">{row.title}</td>
                            <td className="py-1.5 text-center">
                              <span className={`px-1 rounded text-[8px] font-bold ${row.contract.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                                {row.contract.toUpperCase()}
                              </span>
                            </td>
                            <td className="py-1.5 text-center">
                              <span className={`px-1.5 py-0.5 rounded text-[8px] font-bold ${row.isWin ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                                {row.isWin ? "WIN" : "LOSS"}
                              </span>
                            </td>
                            <td className={`py-1.5 text-right font-medium ${row.value >= 0 ? "text-profit" : "text-loss"}`}>
                              {row.value >= 0 ? "+" : ""}{fmtDollar(row.value)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </>
            )}
            {expandedMetric === "fees" && (
              feesBreakdown.length === 0
                ? <p className="text-[10px] text-txt-muted font-mono">No fees recorded yet.</p>
                : <table className="w-full text-[10px] font-mono">
                    <thead>
                      <tr className="text-txt-muted border-b border-t-border">
                        <th className="text-left pb-1 font-medium">Market</th>
                        <th className="text-center pb-1 font-medium w-12">Side</th>
                        <th className="text-left pb-1 font-medium pl-4">Source</th>
                        <th className="text-right pb-1 font-medium w-20">Fees</th>
                      </tr>
                    </thead>
                    <tbody>
                      {feesBreakdown.map((row, i) => (
                        <tr key={i} className="border-b border-t-border/40 last:border-0">
                          <td className="py-1.5 pr-3 text-txt-primary truncate max-w-[300px]">{row.title}</td>
                          <td className="py-1.5 text-center">
                            <span className={`px-1 rounded text-[8px] font-bold ${row.contract.toLowerCase() === "yes" ? "bg-profit-dim text-profit" : "bg-loss-dim text-loss"}`}>
                              {row.contract.toUpperCase()}
                            </span>
                          </td>
                          <td className="py-1.5 pl-4 text-txt-muted">
                            Recorded trade fees
                          </td>
                          <td className="py-1.5 text-right text-warn">
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
                { key: "monitoring" as const, label: "Order Monitoring", count: undefined },
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
            {supportTab === "monitoring" && (
              <OrderMonitoringPanel
                instance={selectedInstance.instanceName || selectedInstance.key}
                apiUrl={selectedInstance.apiUrl}
                onMarketClick={focusMarket}
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
              { key: "activity" as const, label: "Market Activity", count: activeMarkets.length > 0 ? activeMarkets.length : undefined },
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
            <div className="space-y-2">
              <UnifiedMarketTable
                key={selectedInstance.key}
                markets={activeMarkets}
                positions={activeDashboardData.positions}
                trades={activeDashboardData.trades}
                apiClient={instanceApi}
                instanceCacheKey={selectedInstance.key}
                scrollToMarketId={scrollToMarketId}
                onScrollComplete={() => setScrollToMarketId(null)}
              />
              <div className="rounded border border-t-border bg-t-panel px-3 py-2 text-[10px] font-mono text-txt-muted">
                <span className="text-txt-secondary">Note:</span> this table focuses on active and still-tradable markets.
                Closed or inactive holdings that do not yet have an official settlement outcome are listed below in `Closed / Inactive Holdings`.
                Headline `Open Value` still includes both sections, using the synced Kalshi portfolio valuation.
              </div>
            </div>
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

        {/* Row 5: Closed / inactive holdings */}
        {archivedMarkets.length > 0 && (
          <div>
            <SectionLabel text="Closed / Inactive Holdings" count={archivedMarkets.length} />
            <div className="space-y-2">
              <div className="rounded border border-t-border bg-t-panel px-3 py-2 text-[10px] font-mono text-txt-muted">
                <span className="text-txt-secondary">Note:</span> these markets are no longer tradable on Kalshi, but they do not yet have an official settlement outcome.
                They stay out of `Resolved Markets` until Kalshi posts the final result.
              </div>
              <UnifiedMarketTable
                key={`${selectedInstance.key}-archived`}
                markets={archivedMarkets}
                positions={archivedDashboardData.positions}
                trades={archivedDashboardData.trades}
                apiClient={instanceApi}
                instanceCacheKey={`${selectedInstance.key}:archived`}
              />
            </div>
          </div>
        )}

        {/* Row 6: Resolved Markets */}
        <div>
          <SectionLabel text="Resolved Markets" />
          <ModelCalibration resolvedMarkets={resolvedMarkets} />
        </div>

        </div>
      </div>
      )}
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

  // Only show status when there's actual worker or sync timing data.
  const cycleEndStr = health?.effective_last_cycle_end ?? health?.last_cycle_end;
  const syncEndStr = health?.last_sync_end ?? null;
  if ((!cycleEndStr && !syncEndStr) || !health?.poll_interval_sec) {
    return (
      <span className="text-[10px] text-txt-muted font-mono">
        Worker inactive
      </span>
    );
  }

  const cycleIntervalMs = health.poll_interval_sec * 1000;
  const nextCycleMs = (Math.floor(now / cycleIntervalMs) + 1) * cycleIntervalMs;
  const cycleRemainingSec = Math.max(0, Math.floor((nextCycleMs - now) / 1000));
  const syncIntervalMs = (health.sync_interval_sec ?? 1800) * 1000;
  const nextSyncMs = (Math.floor(now / syncIntervalMs) + 1) * syncIntervalMs;
  const syncRemainingSec = Math.max(0, Math.floor((nextSyncMs - now) / 1000));

  const formatCountdown = (remainingSec: number) => {
    const min = Math.floor(remainingSec / 60);
    const sec = remainingSec % 60;
    return `${min}:${sec.toString().padStart(2, "0")}`;
  };

  const workerBadge = (
    <span
      className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
        cycleRemainingSec < 60
          ? "text-accent bg-accent-dim"
          : "text-txt-muted"
      }`}
      title={`Last cycle ended: ${health.last_cycle_end || "unknown"}`}
    >
      Next cycle: {formatCountdown(cycleRemainingSec)}
    </span>
  );

  const syncBadge = syncEndStr ? (
    <span
      className={`text-[10px] font-mono px-1.5 py-0.5 rounded ${
        syncRemainingSec < 60
          ? "text-sky-300 bg-sky-500/10"
          : "text-txt-muted"
      }`}
      title={`Last sync ended: ${health.last_sync_end || "unknown"}`}
    >
      Next sync: {formatCountdown(syncRemainingSec)}
    </span>
  ) : null;

  if (health.cycle_running) {
    return (
      <span className="inline-flex items-center gap-1.5">
        <span
          className="text-[10px] font-mono px-1.5 py-0.5 rounded text-accent bg-accent-dim animate-pulse"
          title="Worker cycle in progress"
        >
          ● Cycle running...
        </span>
        {syncBadge}
      </span>
    );
  }

  if (health.sync_running) {
    return (
      <span className="inline-flex items-center gap-1.5">
        {workerBadge}
        <span
          className="text-[10px] font-mono px-1.5 py-0.5 rounded text-sky-300 bg-sky-500/10 animate-pulse"
          title={`Last sync ended: ${health.last_sync_end || "unknown"}`}
        >
          Syncing with Kalshi...
        </span>
      </span>
    );
  }

  return (
    <span className="inline-flex items-center gap-1.5">
      {workerBadge}
      {syncBadge}
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
