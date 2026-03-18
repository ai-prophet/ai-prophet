"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import Link from "next/link";
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
import { LiveActivity } from "@/components/LiveActivity";
import { SystemHealth } from "@/components/SystemHealth";
import { PositionHeatmap } from "@/components/PositionHeatmap";
import { RiskMetrics } from "@/components/RiskMetrics";
// import { PnLAttribution } from "@/components/PnLAttribution"; // commented out — reinstate when needed
import { ModelCalibration } from "@/components/ModelCalibration";
import { AlertsPanel } from "@/components/AlertsPanel";
import { UnifiedMarketTable } from "@/components/UnifiedMarketTable";

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
  const [scrollToMarketId, setScrollToMarketId] = useState<string | null>(null);

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

  const metrics = computePortfolioMetrics(positions, trades, pnl, markets);
  const [expandedMetric, setExpandedMetric] = useState<"realized" | "unrealized" | null>(null);

  // Per-position P&L breakdowns
  const marketById = new Map(markets.map((m) => [m.market_id, m]));

  const realizedBreakdown = positions
    .filter((p) => p.realized_pnl !== 0)
    .map((p) => {
      const sellTrades = trades.filter(
        (t) => t.ticker === p.ticker && t.action?.toUpperCase() === "SELL" &&
               (t.status === "FILLED" || t.status === "DRY_RUN")
      );
      const sells = sellTrades.map((t) => {
        const qty = t.filled_shares || t.count;
        const sellPrice = t.price_cents / 100;
        return { qty, sellPrice, contribution: (sellPrice - p.avg_price) * qty };
      });
      return { title: p.market_title ?? p.market_id, contract: p.contract, avgEntry: p.avg_price, sells, value: p.realized_pnl };
    })
    .sort((a, b) => b.value - a.value);

  const unrealizedBreakdown = positions.map((p) => {
    const mkt = marketById.get(p.market_id);
    let unrealized = 0;
    let currentBid: number | null = null;
    if (mkt) {
      currentBid = p.contract.toLowerCase() === "yes"
        ? (mkt.yes_bid ?? (mkt.no_ask != null ? 1.0 - mkt.no_ask : null))
        : (mkt.no_bid ?? (mkt.yes_ask != null ? 1.0 - mkt.yes_ask : null));
      if (currentBid != null) unrealized = (currentBid - p.avg_price) * p.quantity;
    }
    return { market_id: p.market_id, title: p.market_title ?? p.market_id, contract: p.contract, quantity: p.quantity, avg_price: p.avg_price, currentBid, value: unrealized };
  }).sort((a, b) => b.value - a.value);

  const cashBalance =
    balance == null
      ? null
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
            <Link href="/docs" className="text-[10px] text-txt-muted hover:text-accent transition-colors px-2 py-1 rounded border border-t-border">
              Docs
            </Link>
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
            tooltip="Realized + Unrealized P&L"
          />
          <MetricCard
            label="Realized"
            value={fmtDollar(metrics.totalRealizedPnl)}
            pnl={metrics.totalRealizedPnl}
            tooltip="(sell_price − avg_entry) × shares_sold — locked in at time of SELL, computed from actual fill prices"
            onClick={() => setExpandedMetric(expandedMetric === "realized" ? null : "realized")}
            active={expandedMetric === "realized"}
          />
          <MetricCard
            label="Unrealized"
            value={fmtDollar(metrics.totalUnrealizedPnl)}
            pnl={metrics.totalUnrealizedPnl}
            tooltip="(current_bid − avg_entry) × open_qty — live, current_bid = 1 − no_ask (YES) or 1 − yes_ask (NO)"
            onClick={() => setExpandedMetric(expandedMetric === "unrealized" ? null : "unrealized")}
            active={expandedMetric === "unrealized"}
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

        {/* Expandable P&L breakdown */}
        {expandedMetric && (
          <div className="bg-t-panel border border-accent/30 rounded px-3 py-2">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] font-medium text-txt-secondary uppercase tracking-widest">
                {expandedMetric === "realized" ? "Realized P&L Breakdown" : "Unrealized P&L Breakdown"}
              </span>
              <span className="text-[9px] text-txt-muted font-mono">
                {expandedMetric === "realized"
                  ? "(sell_price − avg_entry) × qty_sold"
                  : "(current_bid − avg_entry) × open_qty"}
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
                              ? row.sells.map((s, j) => (
                                  <div key={j}>
                                    ({Math.round(s.sellPrice * 100)}¢ − {Math.round(row.avgEntry * 100)}¢) × {s.qty} = <span className={s.contribution >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(s.contribution)}</span>
                                  </div>
                                ))
                              : <span className="text-txt-muted/50">avg entry {Math.round(row.avgEntry * 100)}¢</span>
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
                            {row.currentBid != null
                              ? <>({Math.round(row.currentBid * 100)}¢ bid − {Math.round(row.avg_price * 100)}¢ avg) × {row.quantity} = <span className={row.value >= 0 ? "text-profit" : "text-loss"}>{fmtDollar(row.value)}</span></>
                              : <span>{row.quantity} @ {Math.round(row.avg_price * 100)}¢ avg (no bid)</span>
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
            <SectionLabel text="Risk & Performance" />
            <RiskMetrics analytics={analytics} />
          </div>
        </div>

        {/* Row 3: P&L Attribution — commented out, reinstate when needed
        <div>
          <SectionLabel text="P&L Attribution" />
          <PnLAttribution analytics={analytics} />
        </div>
        */}

        {/* Row 4: Position Heatmap */}
        {positions.length > 0 && (
          <div>
            <SectionLabel text="Position Heatmap" count={positions.length} />
            <PositionHeatmap
              positions={positions}
              markets={markets}
              pnlByMarket={livePnlByMarket}
              onCellClick={setScrollToMarketId}
            />
          </div>
        )}

        {/* Row 5: Unified Market Table */}
        <div>
          <SectionLabel text="Market Activity" count={markets.length} />
          <UnifiedMarketTable
            markets={markets}
            positions={positions}
            trades={trades}
            scrollToMarketId={scrollToMarketId}
            onScrollComplete={() => setScrollToMarketId(null)}
          />
        </div>

        {/* Row 6: Model Calibration */}
        <div>
          <SectionLabel text="Model Calibration" />
          <ModelCalibration calibration={calibration} />
        </div>

        {/* Row 7: Alerts + System Activity */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
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

function InfoDot({ text }: { text: string }) {
  const [show, setShow] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const [pos, setPos] = useState({ top: 0, left: 0 });

  return (
    <span
      ref={ref}
      className="inline-flex items-center justify-center w-3 h-3 ml-1 rounded border border-txt-muted/30 text-[7px] text-txt-muted cursor-help hover:border-accent hover:text-accent transition-colors align-middle"
      onMouseEnter={() => {
        if (ref.current) {
          const r = ref.current.getBoundingClientRect();
          const cx = r.left + r.width / 2;
          const clamped = Math.max(128, Math.min(cx, window.innerWidth - 128));
          setPos({ top: r.top - 8, left: clamped });
        }
        setShow(true);
      }}
      onMouseLeave={() => setShow(false)}
    >
      ?
      {show && (
        <span
          className="fixed -translate-x-1/2 -translate-y-full w-max max-w-[260px] whitespace-normal rounded border border-t-border bg-[#141a22] px-3 py-2 text-[10px] text-left font-mono font-normal normal-case tracking-normal leading-snug text-txt-primary shadow-xl z-[9999] pointer-events-none"
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
