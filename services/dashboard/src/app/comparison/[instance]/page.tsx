"use client";

import { useEffect, useState, useCallback, useRef, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  createApiClient,
  computePortfolioMetrics,
  buildUnifiedMarketRows,
  type Trade,
  type Market,
  type Position,
  type PnLData,
  type HealthData,
  type SystemLogEntry,
  type AnalyticsSummary,
  type ResolvedMarketsData,
  type Alert,
} from "@/lib/api";
import { fmtDollar } from "@/lib/utils";
import { PnLChart } from "@/components/PnLChart";
import { LiveActivity } from "@/components/LiveActivity";
import { RiskMetrics } from "@/components/RiskMetrics";
import { PositionHeatmap } from "@/components/PositionHeatmap";
import { AlertsPanel } from "@/components/AlertsPanel";
import { UnifiedMarketTable } from "@/components/UnifiedMarketTable";
import { ModelCalibration } from "@/components/ModelCalibration";

const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const REFRESH_INTERVAL = 30_000;
const DISPLAY_CUTOFF_MS = new Date("2026-03-24T00:00:00-05:00").getTime();

const MODEL_META: Record<string, { label: string; color: string }> = {
  GPT5: { label: "GPT-5.4", color: "#10a37f" },
  Grok4: { label: "Grok 4", color: "#1da1f2" },
  Opus46: { label: "Claude Opus 4.6", color: "#d4a017" },
};

const STARTING_CASH = 500;
const WIN_RATE_TOOLTIP =
  "A win means positive realized P&L. Losses have negative realized P&L. Zero realized P&L counts as neither, so this is not the same as final market resolution.";

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

// ── Helpers ─────────────────────────────────────────────────────

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

// ── Main page ───────────────────────────────────────────────────

export default function ModelDetailPage() {
  const params = useParams();
  const instanceKey = params.instance as string;
  const meta = MODEL_META[instanceKey] ?? { label: instanceKey, color: "#888" };

  const [trades, setTrades] = useState<Trade[]>([]);
  const [markets, setMarkets] = useState<Market[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [pnl, setPnl] = useState<PnLData | null>(null);
  const [health, setHealth] = useState<HealthData | null>(null);
  const [logs, setLogs] = useState<SystemLogEntry[]>([]);
  const [analytics, setAnalytics] = useState<AnalyticsSummary | null>(null);
  const [resolvedMarkets, setResolvedMarkets] = useState<ResolvedMarketsData | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [lastUpdate, setLastUpdate] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [supportTab, setSupportTab] = useState<"risk" | "alerts" | "activity">("risk");
  const [marketViewTab, setMarketViewTab] = useState<"activity" | "heatmap">("activity");
  const [scrollToMarketId, setScrollToMarketId] = useState<string | null>(null);
  const [expandedMetric, setExpandedMetric] = useState<"netpnl" | "realized" | "unrealized" | "fees" | null>(null);
  const activeRequestRef = useRef(0);

  const apiClient = useMemo(
    () => createApiClient(DEFAULT_API_URL, instanceKey),
    [instanceKey]
  );

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

  const fetchAll = useCallback(async () => {
    const requestId = activeRequestRef.current + 1;
    activeRequestRef.current = requestId;
    setRefreshing(true);

    try {
      const [t, m, posData, h, al] = await Promise.all([
        apiClient.getTrades(100),
        apiClient.getMarkets(200),
        apiClient.getPositions(200),
        apiClient.getHealth(),
        apiClient.getAlerts(),
      ]);
      if (activeRequestRef.current !== requestId) return;
      const displayTrades = filterTradesForDisplay(t);

      setTrades(displayTrades);
      setMarkets(m);
      setPositions(posData.positions);
      setHealth(h);
      setAlerts(al.alerts);
      setError("");
      setLoading(false);
      setLastUpdate(
        new Date().toLocaleTimeString("en-US", {
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hour12: false,
        })
      );

      const [pnlData, l, an, resolved] = await Promise.all([
        apiClient.getPnL(),
        apiClient.getSystemLogs(40),
        apiClient.getAnalyticsSummary(),
        apiClient.getResolvedMarkets(),
      ]);
      if (activeRequestRef.current !== requestId) return;

      setPnl(filterPnlForDisplay(pnlData));
      setLogs(l);
      setAnalytics(an);
      setResolvedMarkets(resolved);
    } catch (e) {
      if (activeRequestRef.current !== requestId) return;
      const message = e instanceof Error ? e.message : "Failed to fetch data";
      setError(`${meta.label}: ${message}`);
      setLoading(false);
    } finally {
      if (activeRequestRef.current !== requestId) return;
      setRefreshing(false);
    }
  }, [apiClient, meta.label]);

  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, REFRESH_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchAll]);

  const metrics = computePortfolioMetrics(positions, trades, pnl, markets);
  const unifiedRows = buildUnifiedMarketRows(markets, positions, trades);

  // ── P&L computation (same algorithm as main dashboard) ─────────

  const marketById = new Map(markets.map((m) => [m.market_id, m]));

  function replayTrades(rowTrades: Trade[]) {
    const relevant = [...rowTrades].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    );
    let netShares = 0;
    let totalCost = 0;
    const sells: { qty: number; sellPrice: number; avgAtSell: number; contribution: number; feePaid: number }[] = [];
    let totalRealized = 0;
    for (const t of relevant) {
      const qty = t.filled_shares || t.count;
      let price = t.price_cents / 100;
      if (price > 1.0) price /= 100;
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
    return { sells, totalRealized, remainingQty, remainingAvgPrice };
  }

  let totalCashPnl = 0;
  let totalOpenValue = 0;
  let totalCashSpent = 0;
  const seenMarketIds = new Set<string>();

  type PerMarketResult = {
    title: string;
    contract: string;
    sells: { qty: number; sellPrice: number; avgAtSell: number; contribution: number; feePaid: number }[];
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
    const dbQty = row.position?.quantity ?? 0;
    const contract = row.position?.contract ?? (row.trades[0]?.side ?? "yes");
    const avgEntry = row.trades.length > 0 ? remainingAvgPrice : (row.position?.avg_price ?? 0);
    const realizedValue = row.trades.length > 0 ? totalRealized : (row.position?.realized_pnl ?? 0);
    totalCashPnl += realizedValue;

    const mkt = marketById.get(row.market_id);
    let currentBid: number | null = null;
    if (mkt && dbQty > 0.001) {
      currentBid = contract === "yes"
        ? (mkt.yes_bid ?? (mkt.no_ask != null ? 1.0 - mkt.no_ask : null))
        : (mkt.no_bid ?? (mkt.yes_ask != null ? 1.0 - mkt.yes_ask : null));
    }
    const cashSpent = avgEntry * dbQty;
    const openValue = currentBid != null ? currentBid * dbQty : 0;
    totalOpenValue += openValue;
    totalCashSpent += cashSpent;

    const dbPos = positions.find((p) => p.market_id === row.market_id);
    const hasMoreTrades = dbPos != null && Math.abs(totalRealized - dbPos.realized_pnl) > 0.005;

    perMarket.push({ title: row.title, contract, sells, totalRealized: realizedValue, avgEntry, currentBid, dbQty, openValue, cashSpent, hasMoreTrades });
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

  const feesBreakdown = unifiedRows
    .map((row) => ({
      title: row.title,
      contract: row.position?.contract ?? (row.trades[0]?.side ?? "yes"),
      value: row.fees_paid_total ?? row.trades.reduce((sum, trade) => sum + (trade.fee_paid || 0), 0),
    }))
    .filter((row) => row.value > 0)
    .sort((a, b) => b.value - a.value);

  const balance = STARTING_CASH - totalCashSpent + totalCashPnl;

  const livePnlByMarket = (() => {
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
        const fee = t.fee_paid || 0;
        return sum + (t.action?.toUpperCase() === "SELL" ? (qty * price) - fee : -((qty * price) + fee));
      }, 0);
      const currentBid = pos.contract.toLowerCase() === "yes"
        ? (mkt?.yes_bid ?? (mkt?.no_ask != null ? 1 - mkt.no_ask : null))
        : (mkt?.no_bid ?? (mkt?.yes_ask != null ? 1 - mkt.yes_ask : null));
      const net = cashFlow + (currentBid != null ? pos.quantity * currentBid : 0);
      map.set(pos.market_id, net);
    }
    return map;
  })();

  const alertCount = alerts.length;
  const totalFeesPaid = trades.reduce((sum, trade) => sum + (trade.fee_paid || 0), 0);

  // ── Render ────────────────────────────────────────────────────

  return (
    <main className="min-h-screen bg-t-bg">
      {/* Header */}
      <header className="border-b border-t-border bg-t-panel/90 backdrop-blur-md sticky top-0 z-20">
        <div className="max-w-[1800px] mx-auto px-5 h-11 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/comparison"
              className="text-[10px] text-txt-muted hover:text-txt-primary transition-colors"
            >
              ← Model Arena
            </Link>
            <span className="text-txt-secondary text-[10px]">/</span>
            <div className="flex items-center gap-2">
              <div
                className="w-2.5 h-2.5 rounded-full"
                style={{ backgroundColor: meta.color }}
              />
              <span className="text-sm font-semibold text-txt-primary tracking-tight">
                {meta.label}
              </span>
              <span className="text-[9px] font-mono text-txt-muted border border-t-border rounded px-1.5 py-0.5">
                DRY RUN
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <CycleCountdown health={health} />
            <button
              onClick={() => fetchAll()}
              disabled={refreshing}
              className="flex items-center gap-1 text-[10px] text-txt-muted font-mono hover:text-txt-primary transition-colors"
              title="Click to refresh now"
            >
              <span className={`inline-block w-1.5 h-1.5 rounded-full ${refreshing ? "bg-accent animate-pulse" : "bg-profit"}`} />
              {lastUpdate || "--:--:--"}
            </button>
          </div>
        </div>
      </header>

      {loading ? (
        <div className="flex flex-col items-center justify-center min-h-[70vh] gap-4">
          <div className="relative w-10 h-10">
            <div className="absolute inset-0 rounded-full border-2 border-t-border" />
            <div className="absolute inset-0 rounded-full border-2 border-t-transparent border-accent animate-spin" />
          </div>
          <span className="text-xs font-mono text-txt-muted tracking-wider">
            Loading {meta.label}…
          </span>
        </div>
      ) : (
        <div className="max-w-[1800px] mx-auto px-5 py-3 space-y-3">
          {error && (
            <div className="bg-loss-dim border border-loss/20 text-loss px-3 py-2 rounded text-xs font-mono">
              {error}
            </div>
          )}

          {/* Row 1: Portfolio Metrics */}
          <div className="grid grid-cols-3 md:grid-cols-5 xl:grid-cols-9 gap-2">
            <MetricCard
              label="Balance"
              value={`$${balance.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
              sub={`started $${STARTING_CASH.toLocaleString()}`}
              pnl={balance - STARTING_CASH}
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
              tooltip="Locked-in gains/losses from completed sells"
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
              tooltip="Total cost of open shares: avg entry × qty held"
            />
            <MetricCard
              label="Markets"
              value={`${metrics.marketsTraded} / ${metrics.openPositions}`}
              sub="markets / positions"
            />
            <MetricCard
              label="Win Rate"
              value={`${(metrics.winRate * 100).toFixed(0)}%`}
              pnl={metrics.winRate >= 0.5 ? 1 : metrics.winRate > 0 ? -1 : undefined}
              tooltip={WIN_RATE_TOOLTIP}
            />
            <MetricCard
              label="Return"
              value={`${metrics.avgReturn >= 0 ? "+" : ""}${metrics.avgReturn.toFixed(1)}%`}
              pnl={metrics.avgReturn}
            />
            <MetricCard
              label="Total Fees"
              value={fmtDollar(totalFeesPaid)}
              pnl={-Math.abs(totalFeesPaid)}
              onClick={() => setExpandedMetric(expandedMetric === "fees" ? null : "fees")}
              active={expandedMetric === "fees"}
            />
          </div>

          {/* Expanded metric breakdowns */}
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

          {expandedMetric && expandedMetric !== "netpnl" && (
            <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
              <div className="flex items-center justify-between mb-2">
                <span className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest">
                  {expandedMetric === "realized"
                    ? "Realized P&L Breakdown"
                    : expandedMetric === "unrealized"
                      ? "Unrealized P&L Breakdown"
                      : "Fee Breakdown"}
                </span>
                <span className="text-[9px] text-txt-muted font-mono">
                  {expandedMetric === "realized"
                    ? "(sell_price − avg_entry) × qty_sold"
                    : expandedMetric === "unrealized"
                      ? "current_bid × open_qty"
                      : "Recorded trade fees by market"}
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
                              {row.sells.map((s, j) => (
                                <div key={j}>
                                  ({Math.round(s.sellPrice * 100)}¢ − {Math.round(s.avgAtSell * 100)}¢) × {s.qty}{s.feePaid > 0 ? ` − fee ${fmtDollar(s.feePaid)}` : ""} = <span className={s.contribution >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(s.contribution)}</span>
                                </div>
                              ))}
                              {row.hasMoreTrades && <div className="text-txt-muted/50 text-[9px]">* additional historical trades not shown</div>}
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

          {/* Row 2: P&L Chart + Risk/Alerts/Activity */}
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
                  { key: "alerts" as const, label: "Alerts", count: alertCount > 0 ? alertCount : undefined },
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
                  onAlertClear={() => {}}
                  onClearAll={() => {}}
                  clearingAlertKey={null}
                  clearingAll={false}
                />
              )}
              {supportTab === "activity" && <LiveActivity logs={logs} />}
            </div>
          </div>

          {/* Row 3: Market Views */}
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
                key={instanceKey}
                markets={markets}
                positions={positions}
                trades={trades}
                apiClient={apiClient}
                instanceCacheKey={instanceKey}
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

          {/* Row 4: Resolved Markets */}
          <div>
            <SectionLabel text="Resolved Markets" />
            <ModelCalibration resolvedMarkets={resolvedMarkets} />
          </div>
        </div>
      )}
    </main>
  );
}
