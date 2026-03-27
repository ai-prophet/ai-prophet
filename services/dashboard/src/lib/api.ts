const DEFAULT_API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const INSTANCE_CONFIG_ENV = process.env.NEXT_PUBLIC_INSTANCE_CONFIG;
const DEFAULT_INSTANCE_KEY_ENV = process.env.NEXT_PUBLIC_DEFAULT_INSTANCE;

async function fetchJSON<T>(baseUrl: string, path: string): Promise<T> {
  const res = await fetch(`${baseUrl}${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status} ${res.statusText}`);
  return res.json();
}

export interface DashboardInstance {
  key: string;
  label: string;
  apiUrl: string;
  instanceName?: string;
  description?: string;
}

function normalizeBaseUrl(url: string): string {
  return url.replace(/\/+$/, "");
}

function parseDashboardInstances(): DashboardInstance[] {
  if (!INSTANCE_CONFIG_ENV) {
    return [
      {
        key: "default",
        label: "Default",
        apiUrl: normalizeBaseUrl(DEFAULT_API_URL),
      },
    ];
  }

  try {
    const parsed = JSON.parse(INSTANCE_CONFIG_ENV);
    if (!Array.isArray(parsed) || parsed.length === 0) {
      throw new Error("Instance config must be a non-empty array");
    }

    return parsed.map((item, index) => ({
      key: String(item.key || `instance-${index + 1}`),
      label: String(item.label || `Instance ${index + 1}`),
      apiUrl: normalizeBaseUrl(String(item.apiUrl || item.api_url || DEFAULT_API_URL)),
      instanceName: item.instanceName ? String(item.instanceName) : item.instance_name ? String(item.instance_name) : undefined,
      description: item.description ? String(item.description) : undefined,
    }));
  } catch (error) {
    console.warn("Failed to parse NEXT_PUBLIC_INSTANCE_CONFIG, falling back to default API URL.", error);
    return [
      {
        key: "default",
        label: "Default",
        apiUrl: normalizeBaseUrl(DEFAULT_API_URL),
      },
    ];
  }
}

export const dashboardInstances = parseDashboardInstances();

export function getDefaultDashboardInstance(): DashboardInstance {
  return (
    dashboardInstances.find((instance) => instance.key === DEFAULT_INSTANCE_KEY_ENV) ||
    dashboardInstances[0]
  );
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
  fee_paid: number;
  dry_run: boolean;
  created_at: string;
  market_title: string | null;
  prediction: {
    p_yes: number;
    yes_ask: number;
    no_ask: number;
    source: string;
    market_id: string;
    reasoning?: string | null;
    sources?: PredictionSource[] | null;
  } | null;
}

export interface TradesResponse {
  trades: Trade[];
  total: number;
  has_more: boolean;
}

export interface PredictionSource {
  url: string;
  title: string;
}

export interface ModelPrediction {
  model_name: string;
  decision: string;
  confidence: number | null;
  p_yes: number | null;
  timestamp: string;
  reasoning?: string | null;
  sources?: PredictionSource[] | null;
  models?: Record<string, { p_yes: number; confidence: number }> | null;
}

export interface PendingOrder {
  order_id: string;
  action: string;
  side: string;
  count: number;
  filled_shares: number;
  price_cents: number;
  created_at: string;
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
  yes_bid: number | null;
  yes_ask: number | null;
  no_bid: number | null;
  no_ask: number | null;
  volume_24h: number | null;
  updated_at: string;
  model_prediction: ModelPrediction | null;
  model_predictions?: ModelPrediction[];
  aggregated_p_yes?: number | null;
  pending_orders?: PendingOrder[];
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
  market_exposure?: number | null;
  total_cost?: number | null;
  fees_paid?: number | null;
  updated_at: string;
}

export interface PnLPoint {
  timestamp: string;
  pnl: number;
  cash_pnl: number;
  open_value: number;
  cash_spent: number;
  /** @deprecated Use cash_pnl instead */
  realized_pnl?: number;
  /** @deprecated Use open_value - cash_spent instead */
  unrealized_pnl?: number;
  trade_cost: number;
  trade_fee?: number;
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
  effective_last_cycle_end?: string | null;
  last_sync_end?: string | null;
  poll_interval_sec: number;
  sync_interval_sec?: number;
  cycle_running?: boolean;
  sync_running?: boolean;
  mode: string;
  betting_enabled: boolean;
  instance_name?: string;
  worker_models?: string[];
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
  sources?: PredictionSource[] | null;
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
    fees_paid_dollars?: number;
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
  cash_balance: number;
  cash_pnl: number;
  open_value: number;
  cash_spent: number;
  net_pnl: number;
  total_fees: number;
  open_positions: number;
  active_markets: number;
  return_pct: number;
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

export interface BrierScorePoint {
  timestamp: string;
  market_id: string;
  market_title: string;
  source: string;
  outcome: "YES" | "NO";
  model_prob: number;
  market_prob: number;
  model_brier: number;
  market_brier: number;
}

export interface BrierScoresData {
  series: BrierScorePoint[];
  summary: {
    model_avg_brier: number;
    market_avg_brier: number;
    total_predictions: number;
    total_markets: number;
  };
  by_model: Record<string, {
    model_avg_brier: number;
    market_avg_brier: number;
    count: number;
  }>;
}

export interface ResolvedMarketRow {
  market_id: string;
  title: string;
  ticker: string;
  category: string | null;
  resolved_at: string | null;
  outcome: "YES" | "NO";
  position_side: "YES" | "NO" | null;
  quantity: number;
  avg_price: number;
  capital: number;
  pnl: number;
  return_pct: number;
  correct: boolean | null;
}

export interface ResolvedMarketsSummary {
  total_pnl: number;
  total_markets: number;
  markets_with_position: number;
  win_count: number;
  loss_count: number;
  total_capital: number;
  win_rate: number;
  brier_score?: number;
  market_baseline_brier?: number;
}

export interface ResolvedMarketsData {
  markets: ResolvedMarketRow[];
  summary: ResolvedMarketsSummary;
}

export interface Alert {
  key: string;
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

// ── Comparison model types ──────────────────────────────────────

export interface ComparisonModelData {
  instance_name: string;
  model_label: string;
  balance: number;
  total_pnl: number;
  starting_cash: number;
  trade_count: number;
  open_positions: number;
  win_rate: number;
  last_updated: string | null;
  error?: string;
}

export interface ComparisonModelsData {
  models: Record<string, ComparisonModelData>;
  timestamp: string;
}

export interface CycleEvaluation {
  id: number;
  ticker: string | null;
  market_id: string;
  market_title: string | null;
  timestamp: string | null;
  model: string | null;
  prediction: {
    p_yes: number | null;
    edge: number | null;
    yes_ask: number | null;
    no_ask: number | null;
  };
  action: {
    type: "buy" | "sell" | "hold" | "dry_run" | "pending" | "skip";
    description: string;
    reason: string | null;
    rationale?: string | null;  // The actual LLM reasoning
  };
  order: {
    action?: string | null;
    side?: string | null;
    count: number;
    filled: number;
    price_cents: number;
    status: string;
    fee_paid: number;
  } | null;
}

export interface CycleEvaluationsData {
  evaluations: CycleEvaluation[];
  total: number;
  has_more: boolean;
  ticker: string | null;
}

// ── Unified market row ──────────────────────────────────────

export interface UnifiedMarketRow {
  market_id: string;
  ticker: string;
  event_ticker: string;
  title: string;
  category: string | null;
  expiration: string | null;
  last_price: number | null;
  yes_bid: number | null;
  yes_ask: number | null;
  no_bid: number | null;
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
    capital: number;
    market_exposure: number | null;
    total_cost: number | null;
    fees_paid: number | null;
  } | null;
  fees_paid_total: number;

  trades: Trade[];
  trade_count: number;
  last_trade_time: string | null;

  // Position breakdown
  target_shares: number | null;
  filled_shares: number | null;
  pending_shares: number | null;
  pending_orders: PendingOrder[];

  has_position: boolean;
  has_prediction: boolean;
  has_trades: boolean;
  updated_at: string;
}

function normalizeTradeStatus(status: string | null | undefined): string {
  return status?.toUpperCase() ?? "";
}

function getExecutedTradeQuantity(trade: Trade): number {
  const filledShares = trade.filled_shares ?? 0;
  if (filledShares > 0) return filledShares;

  const status = normalizeTradeStatus(trade.status);
  if (status === "FILLED" || status === "DRY_RUN") {
    return trade.count;
  }

  return 0;
}

function tradeCashFlow(trade: Trade): number {
  const qty = getExecutedTradeQuantity(trade);
  if (qty <= 0) return 0;

  const fee = trade.fee_paid || 0;
  let price = trade.price_cents / 100;
  if (price > 1.0) price /= 100; // fix corrupted fill_price stored as cents-of-cents

  return trade.action?.toUpperCase() === "SELL"
    ? (qty * price) - fee
    : -((qty * price) + fee);
}

export function liveNetPnl(row: UnifiedMarketRow): number | null {
  if (!row.position && row.trades.length === 0) return null;
  const pos = row.position;
  if (!pos) return null;

  const currentUnitValue =
    row.last_price == null
      ? null
      : pos.contract.toLowerCase() === "yes"
        ? row.last_price
        : 1.0 - row.last_price;
  if (currentUnitValue == null) return null;

  const openValue = currentUnitValue * pos.quantity;
  const costBasis = pos.total_cost ?? pos.capital;
  return openValue - costBasis;
}

export function buildUnifiedMarketRows(
  markets: Market[],
  positions: Position[],
  trades: Trade[],
): UnifiedMarketRow[] {
  const isSyntheticTradePrediction = (trade: Trade): boolean => {
    const source = trade.prediction?.source?.toLowerCase() ?? "";
    return source.startsWith("kalshi:");
  };

  // Index positions by market_id
  const posMap = new Map<string, Position>();
  for (const pos of positions) posMap.set(pos.market_id, pos);

  // Index all orders/trades by ticker for display history
  const tradesByTicker = new Map<string, Trade[]>();
  // Also index by prediction.market_id for fallback matching
  const tradesByMarketId = new Map<string, Trade[]>();
  // Separately track filled-style trades for position math
  const filledTradesByTicker = new Map<string, Trade[]>();
  const filledTradesByMarketId = new Map<string, Trade[]>();
  const edgeTradesByTicker = new Map<string, Trade[]>();
  const edgeTradesByMarketId = new Map<string, Trade[]>();
  for (const t of trades) {
    const existing = tradesByTicker.get(t.ticker);
    if (existing) existing.push(t);
    else tradesByTicker.set(t.ticker, [t]);

    if (t.prediction?.market_id) {
      const mid = t.prediction.market_id;
      const ex = tradesByMarketId.get(mid);
      if (ex) ex.push(t);
      else tradesByMarketId.set(mid, [t]);
    }

    if (t.status === "FILLED" || t.status === "DRY_RUN" || t.status === "PENDING") {
      const edgeExisting = edgeTradesByTicker.get(t.ticker);
      if (edgeExisting) edgeExisting.push(t);
      else edgeTradesByTicker.set(t.ticker, [t]);

      if (t.prediction?.market_id) {
        const mid = t.prediction.market_id;
        const edgeExistingByMarket = edgeTradesByMarketId.get(mid);
        if (edgeExistingByMarket) edgeExistingByMarket.push(t);
        else edgeTradesByMarketId.set(mid, [t]);
      }
    }

    if (t.status !== "FILLED" && t.status !== "DRY_RUN") continue;
    const filledExisting = filledTradesByTicker.get(t.ticker);
    if (filledExisting) filledExisting.push(t);
    else filledTradesByTicker.set(t.ticker, [t]);

    if (t.prediction?.market_id) {
      const mid = t.prediction.market_id;
      const ex2 = filledTradesByMarketId.get(mid);
      if (ex2) ex2.push(t);
      else filledTradesByMarketId.set(mid, [t]);
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
    const filledMktTrades = filledTradesByTicker.get(mkt.ticker)
      ?? filledTradesByMarketId.get(mkt.market_id)
      ?? [];
    const mktEdgeTrades = edgeTradesByTicker.get(mkt.ticker)
      ?? edgeTradesByMarketId.get(mkt.market_id)
      ?? [];
    // Sort trades by time descending
    const sortedTrades = [...mktTrades].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    );
    const sortedEdgeTrades = [...mktEdgeTrades].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    );

    const predicted = mkt.aggregated_p_yes ?? mkt.model_prediction?.p_yes ?? null;

    // Calculate edge from most recent trade's prediction
    let edge: number | null = null;
    const edgeEligibleTrades = sortedEdgeTrades.filter((trade) => !isSyntheticTradePrediction(trade));

    if (edgeEligibleTrades.length > 0) {
      // Get the most recent trade (last in chronologically sorted array)
      const mostRecentTrade = edgeEligibleTrades[edgeEligibleTrades.length - 1];
      const pred = mostRecentTrade.prediction;
      // Edge is always YES-framed: p_yes - yes_ask
      if (pred) {
        edge = pred.p_yes - pred.yes_ask;
      }
    }

    const modelPreds = mkt.model_predictions?.filter((p) => p.model_name !== "aggregated") ?? [];
    const latestActionablePrediction =
      [...modelPreds]
        .reverse()
        .find((p) => {
          const decision = p.decision?.toUpperCase();
          return decision != null && decision !== "HOLD" && decision !== "CYCLE_SKIPPED";
        })
      ?? null;

    // Fallback to the latest actionable model edge. Ignore HOLD-only predictions.
    if (edge === null && latestActionablePrediction?.p_yes != null && mkt.yes_ask != null) {
      edge = latestActionablePrediction.p_yes - mkt.yes_ask;
    }

    let positionData: UnifiedMarketRow["position"] = null;
    const fees_paid_total = filledMktTrades.reduce((sum, trade) => sum + (trade.fee_paid || 0), 0);
    if (pos) {
      positionData = {
        contract: pos.contract,
        quantity: pos.quantity,
        avg_price: pos.avg_price,
        realized_pnl: pos.realized_pnl,
        capital: (pos.total_cost ?? (pos.avg_price * pos.quantity)),
        market_exposure: pos.market_exposure ?? null,
        total_cost: pos.total_cost ?? null,
        fees_paid: pos.fees_paid ?? null,
      };
    }

    // Calculate position breakdown
    const pendingOrders = mkt.pending_orders ?? [];
    const latestResolvedTradeMs = sortedTrades.reduce<number | null>((latest, trade) => {
      if (trade.status?.toUpperCase() === "PENDING") return latest;
      const createdMs = new Date(trade.created_at).getTime();
      if (Number.isNaN(createdMs)) return latest;
      return latest == null || createdMs > latest ? createdMs : latest;
    }, null);
    const effectivePendingOrders = pendingOrders.filter((order) => {
      if (latestResolvedTradeMs == null) return true;
      const createdMs = new Date(order.created_at).getTime();
      if (Number.isNaN(createdMs)) return true;
      return createdMs >= latestResolvedTradeMs;
    });
    let target_shares: number | null = null;
    let filled_shares: number | null = null;
    let pending_shares: number | null = null;

    // Calculate filled shares from BUY trades
    const buyTrades = filledMktTrades.filter((t) => t.action?.toUpperCase() === "BUY");
    if (buyTrades.length > 0) {
      filled_shares = buyTrades.reduce((sum, t) => sum + (t.filled_shares || t.count), 0);
    }

    // Calculate pending shares from pending orders
    if (effectivePendingOrders.length > 0) {
      pending_shares = effectivePendingOrders.reduce((sum, order) => {
        const remaining = order.count - order.filled_shares;
        return sum + remaining;
      }, 0);

      const currentSignedQuantity = pos
        ? (pos.contract.toLowerCase() === "yes" ? pos.quantity : -pos.quantity)
        : 0;

      const targetSignedQuantity = effectivePendingOrders.reduce((signedQty, order) => {
        const remaining = Math.max(0, order.count - order.filled_shares);
        if (remaining <= 0) return signedQty;

        const sideSign = order.side?.toUpperCase() === "YES" ? 1 : -1;
        const action = order.action?.toUpperCase() ?? "BUY";

        if (action === "SELL") {
          return signedQty - sideSign * remaining;
        }

        return signedQty + sideSign * remaining;
      }, currentSignedQuantity);

      target_shares = Math.abs(targetSignedQuantity);
    }

    rows.push({
      market_id: mkt.market_id,
      ticker: mkt.ticker,
      event_ticker: mkt.event_ticker,
      title: mkt.title,
      category: mkt.category,
      expiration: mkt.expiration,
      last_price: mkt.last_price,
      yes_bid: mkt.yes_bid ?? (mkt.no_ask != null ? 1.0 - mkt.no_ask : null),
      yes_ask: mkt.yes_ask,
      no_bid: mkt.no_bid ?? (mkt.yes_ask != null ? 1.0 - mkt.yes_ask : null),
      no_ask: mkt.no_ask,
      volume_24h: mkt.volume_24h,
      aggregated_p_yes: predicted,
      edge,
      model_predictions: modelPreds,
      position: positionData,
      fees_paid_total,
      trades: sortedTrades,
      trade_count: sortedTrades.length,
      last_trade_time: sortedTrades[0]?.created_at ?? null,
      target_shares,
      filled_shares,
      pending_shares,
      pending_orders: effectivePendingOrders,
      has_position: pos != null,
      has_prediction: predicted != null,
      has_trades: sortedTrades.length > 0,
      updated_at: mkt.updated_at,
    });
  }

  // Handle orphan positions (position exists but no matching market)
  for (const pos of positions) {
    if (seenMarketIds.has(pos.market_id)) continue;
    const mktTrades = (pos.ticker ? tradesByTicker.get(pos.ticker) : null) ?? [];
    const filledMktTrades = (pos.ticker ? filledTradesByTicker.get(pos.ticker) : null) ?? [];
    const sortedTrades = [...mktTrades].sort(
      (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime()
    );

    // Calculate filled shares for orphan positions
    const buyTrades = filledMktTrades.filter((t) => t.action?.toUpperCase() === "BUY");
    const filled_shares = buyTrades.length > 0
      ? buyTrades.reduce((sum, t) => sum + (t.filled_shares || t.count), 0)
      : null;

    rows.push({
      market_id: pos.market_id,
      ticker: pos.ticker ?? "",
      event_ticker: pos.event_ticker ?? "",
      title: pos.market_title ?? pos.ticker ?? pos.market_id,
      category: null,
      expiration: null,
      last_price: null,
      yes_bid: null,
      yes_ask: null,
      no_bid: null,
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
        capital: (pos.total_cost ?? (pos.avg_price * pos.quantity)),
        market_exposure: pos.market_exposure ?? null,
        total_cost: pos.total_cost ?? null,
        fees_paid: pos.fees_paid ?? null,
      },
      fees_paid_total: filledMktTrades.reduce((sum, trade) => sum + (trade.fee_paid || 0), 0),
      trades: sortedTrades,
      trade_count: sortedTrades.length,
      last_trade_time: sortedTrades[0]?.created_at ?? null,
      target_shares: null,
      filled_shares,
      pending_shares: null,
      pending_orders: [],
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
  pnl: PnLData | null,
  markets?: Market[]
) {
  const totalRealizedPnl = positions.reduce(
    (sum, p) => sum + p.realized_pnl,
    0
  );

  const marketById = markets ? new Map(markets.map((m) => [m.market_id, m])) : null;
  const totalUnrealizedPnl = positions.reduce((sum, p) => {
    if (marketById) {
      const mkt = marketById.get(p.market_id);
      if (mkt) {
        const currentBid = p.contract.toLowerCase() === "yes"
          ? (mkt.yes_bid ?? (mkt.no_ask != null ? 1.0 - mkt.no_ask : null))
          : (mkt.no_bid ?? (mkt.yes_ask != null ? 1.0 - mkt.yes_ask : null));
        if (currentBid != null) {
          return sum + (currentBid - p.avg_price) * p.quantity;
        }
      }
    }
    return sum; // no market data available, skip
  }, 0);

  const totalPnl = totalRealizedPnl + totalUnrealizedPnl;

  const capitalDeployed = positions.reduce(
    (sum, p) => sum + p.avg_price * p.quantity,
    0
  );

  const activeMarketIds = new Set(positions.map((p) => p.market_id));
  let pendingOnlyMarketCount = 0;
  if (markets) {
    for (const market of markets) {
      const hasPendingExposure = (market.pending_orders ?? []).some(
        (order) => order.count - order.filled_shares > 0
      );
      if (!hasPendingExposure) continue;
      if (!activeMarketIds.has(market.market_id)) {
        pendingOnlyMarketCount += 1;
      }
      activeMarketIds.add(market.market_id);
    }
  }

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
    openPositions: positions.length + pendingOnlyMarketCount,
    marketsTraded: activeMarketIds.size,
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
      existing.pnl += pos.realized_pnl;
      existing.openSize += pos.quantity;
    } else {
      marketMap.set(key, {
        marketId: key,
        title: pos.market_title ?? pos.ticker ?? key,
        capitalDeployed: capital,
        pnl: pos.realized_pnl,
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

export function createApiClient(baseUrl: string, instanceName?: string) {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  const buildPath = (path: string) => {
    if (!instanceName) return path;
    const separator = path.includes("?") ? "&" : "?";
    return `${path}${separator}instance_name=${encodeURIComponent(instanceName)}`;
  };

  return {
    getTrades: (limit = 50, offset = 0) => {
      return fetchJSON<Trade[] | TradesResponse>(normalizedBaseUrl, buildPath(`/trades?limit=${limit}&offset=${offset}`))
        .then((data) => {
          if (Array.isArray(data)) return data;
          return data.trades;
        });
    },
    getTradesPaginated: (limit = 50, offset = 0) =>
      fetchJSON<TradesResponse>(normalizedBaseUrl, buildPath(`/trades?limit=${limit}&offset=${offset}`)).catch(() =>
        fetchJSON<Trade[]>(normalizedBaseUrl, buildPath(`/trades?limit=${limit}&offset=${offset}`)).then((trades) => ({
          trades,
          total: trades.length,
          has_more: trades.length === limit,
        }))
      ),
    getMarkets: (limit = 50) => fetchJSON<Market[]>(normalizedBaseUrl, buildPath(`/markets?limit=${limit}`)),
    getPositions: (limit = 50, offset = 0, search?: string) => {
      let url = `/positions?limit=${limit}&offset=${offset}`;
      if (search) url += `&search=${encodeURIComponent(search)}`;
      return fetchJSON<Position[] | { positions: Position[]; total: number; has_more: boolean }>(normalizedBaseUrl, buildPath(url))
        .then((data) => {
          if (Array.isArray(data)) return { positions: data, total: data.length, has_more: false };
          return data;
        });
    },
    getPnL: (days = 30, marketId?: string, model?: string) => {
      let url = `/pnl?days=${days}`;
      if (marketId) url += `&market_id=${encodeURIComponent(marketId)}`;
      if (model) url += `&model=${encodeURIComponent(model)}`;
      return fetchJSON<PnLData>(normalizedBaseUrl, buildPath(url));
    },
    getHealth: () => fetchJSON<HealthData>(normalizedBaseUrl, buildPath("/health")),
    getModelRuns: (limit = 100) =>
      fetchJSON<ModelRun[]>(normalizedBaseUrl, buildPath(`/model-runs?limit=${limit}`)),
    getMarketModelRuns: (marketId: string, limit = 200) =>
      fetchJSON<ModelRun[]>(normalizedBaseUrl, buildPath(`/model-runs?market_id=${encodeURIComponent(marketId)}&limit=${limit}`)).catch(() => [] as ModelRun[]),
    getSystemLogs: (limit = 50) =>
      fetchJSON<SystemLogEntry[]>(normalizedBaseUrl, buildPath(`/system-logs?limit=${limit}`)),
    getKalshiBalance: () => fetchJSON<KalshiBalanceData>(normalizedBaseUrl, buildPath("/kalshi/balance")),
    getKalshiPositions: () =>
      fetchJSON<KalshiPositionsData>(normalizedBaseUrl, buildPath("/kalshi/positions")),
    getAnalyticsSummary: () =>
      fetchJSON<AnalyticsSummary>(normalizedBaseUrl, buildPath("/analytics/summary")).catch((e) => {
        console.warn("Failed to fetch analytics summary:", e);
        return null;
      }),
    getModelCalibration: (modelName?: string) => {
      let url = "/analytics/model-calibration";
      if (modelName) url += `?model_name=${encodeURIComponent(modelName)}`;
      return fetchJSON<ModelCalibrationData>(normalizedBaseUrl, buildPath(url)).catch((e) => {
        console.warn("Failed to fetch model calibration:", e);
        return null;
      });
    },
    getBrierScores: (modelName?: string) => {
      let url = "/analytics/brier-scores";
      if (modelName) url += `?model_name=${encodeURIComponent(modelName)}`;
      return fetchJSON<BrierScoresData>(normalizedBaseUrl, buildPath(url)).catch((e) => {
        console.warn("Failed to fetch brier scores:", e);
        return null;
      });
    },
    getResolvedMarkets: () =>
      fetchJSON<ResolvedMarketsData>(normalizedBaseUrl, buildPath("/analytics/resolved-markets")).catch((e) => {
        console.warn("Failed to fetch resolved markets:", e);
        return null;
      }),
    getAlerts: () =>
      fetchJSON<AlertsData>(normalizedBaseUrl, buildPath("/alerts")).catch((e) => {
        console.warn("Failed to fetch alerts:", e);
        return { alerts: [] };
      }),
    clearAlert: (alertKey: string) =>
      fetch(`${normalizedBaseUrl}${buildPath("/alerts/clear")}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ alert_key: alertKey, instance_name: instanceName || undefined }),
      }).then((r) => r.json()),
    clearAllAlerts: () =>
      fetch(`${normalizedBaseUrl}${buildPath("/alerts/clear-all")}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instance_name: instanceName || undefined }),
      }).then((r) => r.json()),
    getPredictions: (marketId: string) =>
      fetchJSON<PredictionTimeSeries>(normalizedBaseUrl, buildPath(`/predictions/${encodeURIComponent(marketId)}`)).catch(() => null),
    getPriceHistory: (marketId: string) =>
      fetchJSON<{ market_id: string; series: PriceHistoryPoint[]; count: number }>(
        normalizedBaseUrl,
        buildPath(`/market-price-history/${encodeURIComponent(marketId)}`)
      )
        .then((data) => data?.series ?? [])
        .catch((e) => {
          console.warn("Failed to fetch price history:", e);
          return [];
        }),
    clearAllData: () =>
      fetch(`${normalizedBaseUrl}${buildPath("/data/clear")}`, { method: "DELETE" }).then((r) => r.json()),
    getComparisonModels: () =>
      fetchJSON<ComparisonModelsData>(normalizedBaseUrl, buildPath("/comparison-models")).catch((e) => {
        console.warn("Failed to fetch comparison models:", e);
        return null;
      }),
    getCycleEvaluations: (ticker?: string, limit = 100, offset = 0) => {
      let url = `/cycle-evaluations?limit=${limit}&offset=${offset}`;
      if (ticker) url += `&ticker=${encodeURIComponent(ticker)}`;
      return fetchJSON<CycleEvaluationsData>(normalizedBaseUrl, buildPath(url)).catch((e) => {
        console.warn("Failed to fetch cycle evaluations:", e);
        return { evaluations: [], total: 0, has_more: false, ticker: ticker || null };
      });
    },
  };
}

export type ApiClient = ReturnType<typeof createApiClient>;

export const api = createApiClient(DEFAULT_API_URL);
