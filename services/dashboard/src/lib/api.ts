const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function fetchJSON<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

// ── Core types ──────────────────────────────────────────────

export interface Trade {
  id: number;
  order_id: string;
  ticker: string;
  action: string;
  side: string;
  count: number;
  price_cents: number;
  status: string;
  filled_shares: number;
  fill_price: number;
  exchange_order_id: string | null;
  dry_run: boolean;
  created_at: string;
  market_title: string | null;
  prediction: {
    p_yes: number;
    yes_ask: number;
    no_ask: number;
    source: string;
    market_id: string;
  } | null;
}

export interface TradesResponse {
  trades: Trade[];
  total: number;
  has_more: boolean;
}

export interface ModelPrediction {
  model_name: string;
  decision: string;
  confidence: number | null;
  p_yes: number | null;
  timestamp: string;
  reasoning?: string | null;
  models?: Record<string, { p_yes: number; confidence: number }> | null;
}

export interface Market {
  id: number;
  market_id: string;
  ticker: string;
  event_ticker: string;
  title: string;
  category: string | null;
  expiration: string | null;
  last_price: number | null;
  yes_ask: number | null;
  no_ask: number | null;
  volume_24h: number | null;
  updated_at: string;
  model_prediction: ModelPrediction | null;
  model_predictions?: ModelPrediction[];
  aggregated_p_yes?: number | null;
}

export interface Position {
  id: number;
  market_id: string;
  ticker: string | null;
  event_ticker: string | null;
  market_title: string | null;
  contract: string;
  quantity: number;
  avg_price: number;
  realized_pnl: number;
  unrealized_pnl: number;
  max_position: number;
  realized_trades: number;
  updated_at: string;
}

export interface PnLPoint {
  timestamp: string;
  pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  trade_cost: number;
  ticker: string;
  side: string;
  action: string;
}

export interface TradeMarker {
  timestamp: string;
  ticker: string;
  side: string;
  action: string;
  count: number;
  price_cents: number;
  pnl_impact: number;
}

export interface PnLData {
  series: PnLPoint[];
  trade_markers: TradeMarker[];
  summary: {
    total_pnl: number;
    total_trades: number;
    total_volume: number;
    active_positions: number;
  };
}

export interface HealthData {
  status: string;
  database: string;
  worker: string;
  last_heartbeat: string | null;
  last_cycle_end: string | null;
  poll_interval_sec: number;
  mode: string;
  betting_enabled: boolean;
  timestamp: string;
}

export interface ModelRun {
  id: number;
  model_name: string;
  timestamp: string;
  decision: string;
  confidence: number | null;
  market_id: string;
  p_yes: number | null;
  reasoning: string | null;
}

export interface SystemLogEntry {
  id: number;
  level: string;
  message: string;
  component: string;
  created_at: string;
}

export interface KalshiBalanceData {
  balance: number;
  dry_run: boolean;
  error?: string;
  timestamp: string;
}

export interface KalshiPositionsData {
  positions: Array<{
    ticker: string;
    market_exposure: number;
    realized_pnl: number;
    resting_orders_count: number;
    total_traded: number;
    [key: string]: unknown;
  }>;
  dry_run: boolean;
  error?: string;
  timestamp: string;
}

// ── Analytics types ─────────────────────────────────────────

export interface ModelPnLBreakdown {
  pnl: number;
  trades: number;
  win_rate: number;
}

export interface MarketPnLBreakdown {
  pnl: number;
  trades: number;
  title: string;
}

export interface AnalyticsSummary {
  sharpe_ratio: number;
  max_drawdown: number;
  max_drawdown_pct: number;
  volatility: number;
  sortino_ratio: number;
  profit_factor: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  pnl_by_model: Record<string, ModelPnLBreakdown>;
  pnl_by_market: Record<string, MarketPnLBreakdown>;
  today_pnl: number;
  total_exposure: number;
}

export interface CalibrationBin {
  bin_center: number;
  predicted_avg: number;
  observed_freq: number;
  count: number;
}

export interface ModelCalibrationData {
  calibration: CalibrationBin[];
  brier_score: number;
  market_baseline_brier: number;
  models: string[];
  by_model: Record<string, {
    brier_score: number;
    total_predictions: number;
    calibration: CalibrationBin[];
  }>;
}

export interface Alert {
  type: string;
  severity: "info" | "warning" | "error";
  message: string;
  market_id?: string;
  timestamp: string;
}

export interface AlertsData {
  alerts: Alert[];
}

export interface PredictionPoint {
  timestamp: string;
  p_yes: number;
  yes_ask: number;
  no_ask: number;
  source: string;
  edge: number;
}

export interface PredictionTimeSeries {
  market_id: string;
  series: PredictionPoint[];
}

export interface PriceHistoryPoint {
  timestamp: string;
  yes_ask: number;
  no_ask: number;
  volume_24h: number | null;
  model_p_yes: number | null;
  model_name: string | null;
}

// ── Unified market row ──────────────────────────────────────

export interface UnifiedMarketRow {
  market_id: string;
  ticker: string;
  event_ticker: string;
  title: string;
  category: string | null;
  expiration: string | null;
  yes_ask: number | null;
  no_ask: number | null;
  volume_24h: number | null;

  aggregated_p_yes: number | null;
  edge: number | null;
  model_predictions: ModelPrediction[];

  position: {
    contract: string;
    quantity: number;
    avg_price: number;
    realized_pnl: number;
    unrealized_pnl: number;
    capital: number;
    return_pct: number;
  } | null;

  trades: Trade[];
  trade_count: number;
  last_trade_time: string | null;

  has_position: boolean;
  has_prediction: boolean;
  has_trades: boolean;
  updated_at: string;
}

export function buildUnifiedMarketRows(
  markets: Market[],
  positions: Position[],
  trades: Trade[],
): UnifiedMarketRow[] {
  // Index positions by market_id
  const posMap = new Map<string, Position>();
  for (const pos of positions) posMap.set(pos.market_id, pos);

  // Index trades by ticker
  const tradesByTicker = new Map<string, Trade[]>();
  // Also index by prediction.market_id for fallback matching
  const tradesByMarketId = new Map<string, Trade[]>();
  for (const t of trades) {
    if (t.status !== "FILLED" && t.status !== "DRY_RUN") continue;
    const existing = tradesByTicker.get(t.ticker);
    if (existing) existing.push(t);
    else tradesByTicker.set(t.ticker, [t]);

    if (t.prediction?.market_id) {
      const mid = t.prediction.market_id;
      const ex2 = tradesByMarketId.get(mid);
      if (ex2) ex2.push(t);
      else tradesByMarketId.set(mid, [t]);
    }
  }

  const seenMarketIds = new Set<string>();
  const rows: UnifiedMarketRow[] = [];

  // Process each market
  for (const mkt of markets) {
    seenMarketIds.add(mkt.market_id);
    const pos = posMap.get(mkt.market_id);
    const mktTrades = tradesByTicker.get(mkt.ticker)
      ?? tradesByMarketId.get(mkt.market_id)
      ?? [];
    // Sort trades by time descending
    const sortedTrades = [...mktTrades].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );

    const predicted = mkt.aggregated_p_yes ?? mkt.model_prediction?.p_yes ?? null;
    const edge = predicted != null && mkt.yes_ask != null ? predicted - mkt.yes_ask : null;
    const modelPreds = mkt.model_predictions?.filter((p) => p.model_name !== "aggregated") ?? [];

    let positionData: UnifiedMarketRow["position"] = null;
    if (pos) {
      const capital = pos.avg_price * pos.quantity;
      const totalPnl = pos.realized_pnl + pos.unrealized_pnl;
      positionData = {
        contract: pos.contract,
        quantity: pos.quantity,
        avg_price: pos.avg_price,
        realized_pnl: pos.realized_pnl,
        unrealized_pnl: pos.unrealized_pnl,
        capital,
        return_pct: capital > 0 ? (totalPnl / capital) * 100 : 0,
      };
    }

    rows.push({
      market_id: mkt.market_id,
      ticker: mkt.ticker,
      event_ticker: mkt.event_ticker,
      title: mkt.title,
      category: mkt.category,
      expiration: mkt.expiration,
      yes_ask: mkt.yes_ask,
      no_ask: mkt.no_ask,
      volume_24h: mkt.volume_24h,
      aggregated_p_yes: predicted,
      edge,
      model_predictions: modelPreds,
      position: positionData,
      trades: sortedTrades,
      trade_count: sortedTrades.length,
      last_trade_time: sortedTrades[0]?.created_at ?? null,
      has_position: pos != null,
      has_prediction: predicted != null,
      has_trades: sortedTrades.length > 0,
      updated_at: mkt.updated_at,
    });
  }

  // Handle orphan positions (position exists but no matching market)
  for (const pos of positions) {
    if (seenMarketIds.has(pos.market_id)) continue;
    const capital = pos.avg_price * pos.quantity;
    const totalPnl = pos.realized_pnl + pos.unrealized_pnl;
    const mktTrades = (pos.ticker ? tradesByTicker.get(pos.ticker) : null) ?? [];
    const sortedTrades = [...mktTrades].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    );
    rows.push({
      market_id: pos.market_id,
      ticker: pos.ticker ?? "",
      event_ticker: pos.event_ticker ?? "",
      title: pos.market_title ?? pos.ticker ?? pos.market_id,
      category: null,
      expiration: null,
      yes_ask: null,
      no_ask: null,
      volume_24h: null,
      aggregated_p_yes: null,
      edge: null,
      model_predictions: [],
      position: {
        contract: pos.contract,
        quantity: pos.quantity,
        avg_price: pos.avg_price,
        realized_pnl: pos.realized_pnl,
        unrealized_pnl: pos.unrealized_pnl,
        capital,
        return_pct: capital > 0 ? (totalPnl / capital) * 100 : 0,
      },
      trades: sortedTrades,
      trade_count: sortedTrades.length,
      last_trade_time: sortedTrades[0]?.created_at ?? null,
      has_position: true,
      has_prediction: false,
      has_trades: sortedTrades.length > 0,
      updated_at: pos.updated_at,
    });
  }

  return rows;
}

// ── URL helpers ─────────────────────────────────────────────

export function kalshiMarketUrl(eventTicker: string): string {
  return `https://kalshi.com/events/${eventTicker}`;
}

export function kalshiEventUrl(eventTicker: string): string {
  return `https://kalshi.com/events/${eventTicker}`;
}

// ── Derived data helpers ────────────────────────────────────

export function computePortfolioMetrics(
  positions: Position[],
  trades: Trade[],
  pnl: PnLData | null
) {
  const totalRealizedPnl = positions.reduce(
    (sum, p) => sum + p.realized_pnl,
    0
  );
  const totalUnrealizedPnl = positions.reduce(
    (sum, p) => sum + p.unrealized_pnl,
    0
  );
  const totalPnl = totalRealizedPnl + totalUnrealizedPnl;

  const capitalDeployed = positions.reduce(
    (sum, p) => sum + p.avg_price * p.quantity,
    0
  );

  const uniqueMarkets = new Set(positions.map((p) => p.market_id));

  const filledTrades = trades.filter(
    (t) => t.status === "FILLED" || t.status === "DRY_RUN"
  );
  const winningTrades = positions.filter((p) => p.realized_pnl > 0).length;
  const totalWithPnl = positions.filter((p) => p.realized_pnl !== 0).length;
  const winRate = totalWithPnl > 0 ? winningTrades / totalWithPnl : 0;

  const avgReturn =
    capitalDeployed > 0 ? (totalPnl / capitalDeployed) * 100 : 0;

  return {
    totalPnl,
    totalRealizedPnl,
    totalUnrealizedPnl,
    capitalDeployed,
    openPositions: positions.length,
    marketsTraded: uniqueMarkets.size,
    winRate,
    avgReturn,
    totalTrades: pnl?.summary.total_trades ?? filledTrades.length,
    totalVolume: pnl?.summary.total_volume ?? 0,
  };
}

export function groupByMarket(positions: Position[], trades: Trade[]) {
  const marketMap = new Map<
    string,
    {
      marketId: string;
      title: string;
      capitalDeployed: number;
      pnl: number;
      tradeCount: number;
      openSize: number;
      contract: string;
    }
  >();

  for (const pos of positions) {
    const key = pos.market_id;
    const existing = marketMap.get(key);
    const capital = pos.avg_price * pos.quantity;
    if (existing) {
      existing.capitalDeployed += capital;
      existing.pnl += pos.realized_pnl + pos.unrealized_pnl;
      existing.openSize += pos.quantity;
    } else {
      marketMap.set(key, {
        marketId: key,
        title: pos.market_title ?? pos.ticker ?? key,
        capitalDeployed: capital,
        pnl: pos.realized_pnl + pos.unrealized_pnl,
        tradeCount: 0,
        openSize: pos.quantity,
        contract: pos.contract,
      });
    }
  }

  const marketValues = Array.from(marketMap.values());
  for (const trade of trades) {
    for (const mkt of marketValues) {
      if (trade.ticker === mkt.title || trade.market_title === mkt.title) {
        mkt.tradeCount++;
      }
    }
  }

  return marketValues;
}

// ── API client ──────────────────────────────────────────────

export const api = {
  // Existing endpoints (enhanced)
  getTrades: (limit = 50, offset = 0) => {
    // Support both old list format and new paginated format
    return fetchJSON<Trade[] | TradesResponse>(`/trades?limit=${limit}&offset=${offset}`)
      .then((data) => {
        if (Array.isArray(data)) return data;
        return data.trades;
      });
  },
  getTradesPaginated: (limit = 50, offset = 0) =>
    fetchJSON<TradesResponse>(`/trades?limit=${limit}&offset=${offset}`).catch(() =>
      // Fallback for old API format
      fetchJSON<Trade[]>(`/trades?limit=${limit}&offset=${offset}`).then((trades) => ({
        trades,
        total: trades.length,
        has_more: trades.length === limit,
      }))
    ),
  getMarkets: (limit = 50) => fetchJSON<Market[]>(`/markets?limit=${limit}`),
  getPositions: (limit = 50, offset = 0, search?: string) => {
    let url = `/positions?limit=${limit}&offset=${offset}`;
    if (search) url += `&search=${encodeURIComponent(search)}`;
    return fetchJSON<Position[] | { positions: Position[]; total: number; has_more: boolean }>(url)
      .then((data) => {
        if (Array.isArray(data)) return { positions: data, total: data.length, has_more: false };
        return data;
      });
  },
  getPnL: (days = 30, marketId?: string, model?: string) => {
    let url = `/pnl?days=${days}`;
    if (marketId) url += `&market_id=${encodeURIComponent(marketId)}`;
    if (model) url += `&model=${encodeURIComponent(model)}`;
    return fetchJSON<PnLData>(url);
  },
  getHealth: () => fetchJSON<HealthData>("/health"),
  getModelRuns: (limit = 100) =>
    fetchJSON<ModelRun[]>(`/model-runs?limit=${limit}`),
  getSystemLogs: (limit = 50) =>
    fetchJSON<SystemLogEntry[]>(`/system-logs?limit=${limit}`),
  getKalshiBalance: () => fetchJSON<KalshiBalanceData>("/kalshi/balance"),
  getKalshiPositions: () =>
    fetchJSON<KalshiPositionsData>("/kalshi/positions"),

  // New analytics endpoints
  getAnalyticsSummary: () =>
    fetchJSON<AnalyticsSummary>("/analytics/summary").catch((e) => {
      console.warn("Failed to fetch analytics summary:", e);
      return null;
    }),
  getModelCalibration: (modelName?: string) => {
    let url = "/analytics/model-calibration";
    if (modelName) url += `?model_name=${encodeURIComponent(modelName)}`;
    return fetchJSON<ModelCalibrationData>(url).catch((e) => {
      console.warn("Failed to fetch model calibration:", e);
      return null;
    });
  },
  getAlerts: () =>
    fetchJSON<AlertsData>("/alerts").catch((e) => {
      console.warn("Failed to fetch alerts:", e);
      return { alerts: [] };
    }),
  getPredictions: (marketId: string) =>
    fetchJSON<PredictionTimeSeries>(`/predictions/${encodeURIComponent(marketId)}`).catch(() => null),
  getPriceHistory: (marketId: string) =>
    fetchJSON<{ market_id: string; series: PriceHistoryPoint[]; count: number }>(
      `/market-price-history/${encodeURIComponent(marketId)}`
    )
      .then((data) => data?.series ?? [])
      .catch((e) => {
        console.warn("Failed to fetch price history:", e);
        return [];
      }),

  clearAllData: () =>
    fetch(`${API_URL}/data/clear`, { method: "DELETE" }).then((r) => r.json()),
};
