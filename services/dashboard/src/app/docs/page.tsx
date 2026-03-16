"use client";

import Link from "next/link";

/* ═══════════════════════════════════════════════════════════════
   Prophet Arena — System Documentation
   ═══════════════════════════════════════════════════════════════ */

// ── Reusable doc primitives ─────────────────────────────────────

function H1({ children }: { children: React.ReactNode }) {
  return <h1 className="text-xl font-bold text-txt-primary mb-4 mt-10 first:mt-0 border-b border-t-border pb-2">{children}</h1>;
}
function H2({ children, id }: { children: React.ReactNode; id?: string }) {
  return <h2 id={id} className="text-base font-semibold text-txt-primary mt-8 mb-3 scroll-mt-16">{children}</h2>;
}
function H3({ children }: { children: React.ReactNode }) {
  return <h3 className="text-sm font-semibold text-txt-secondary mt-5 mb-2">{children}</h3>;
}
function P({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-txt-secondary leading-relaxed mb-3">{children}</p>;
}
function Code({ children }: { children: React.ReactNode }) {
  return <code className="text-[11px] bg-t-panel-alt px-1.5 py-0.5 rounded text-accent font-mono">{children}</code>;
}
function Pre({ children }: { children: React.ReactNode }) {
  return <pre className="text-[11px] bg-t-panel-alt border border-t-border rounded p-4 mb-4 overflow-x-auto font-mono text-txt-secondary leading-relaxed whitespace-pre">{children}</pre>;
}
function Formula({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] bg-t-panel-alt border border-t-border/50 rounded px-4 py-3 mb-4 font-mono text-accent text-center tracking-wide">{children}</div>;
}
function Ul({ children }: { children: React.ReactNode }) {
  return <ul className="text-xs text-txt-secondary leading-relaxed mb-3 ml-4 space-y-1 list-disc marker:text-txt-muted">{children}</ul>;
}
function Li({ children }: { children: React.ReactNode }) {
  return <li>{children}</li>;
}
function Note({ children }: { children: React.ReactNode }) {
  return <div className="text-[10px] bg-accent/5 border border-accent/20 rounded px-3 py-2 mb-4 text-txt-secondary"><span className="text-accent font-semibold">Note: </span>{children}</div>;
}
function Diagram({ children }: { children: React.ReactNode }) {
  return <pre className="text-[10px] bg-t-bg border border-t-border rounded p-4 mb-4 font-mono text-txt-muted leading-snug overflow-x-auto">{children}</pre>;
}

// ── Table of contents ───────────────────────────────────────────

const TOC = [
  { id: "architecture", label: "System Architecture" },
  { id: "market-data", label: "Market Data & Ingestion" },
  { id: "prediction", label: "Model Prediction Pipeline" },
  { id: "aggregation", label: "Model Aggregation" },
  { id: "edge", label: "Edge Calculation" },
  { id: "strategy", label: "Trading Strategy" },
  { id: "execution", label: "Trade Execution" },
  { id: "positions", label: "Position Management" },
  { id: "pnl", label: "P&L Calculations" },
  { id: "risk", label: "Risk & Capital Allocation" },
  { id: "activity", label: "Activity Logging" },
  { id: "config", label: "Configuration Reference" },
];

// ── Main page ───────────────────────────────────────────────────

export default function DocsPage() {
  return (
    <main className="min-h-screen bg-t-bg">
      {/* Header */}
      <header className="border-b border-t-border bg-t-panel/90 backdrop-blur-md sticky top-0 z-20">
        <div className="max-w-[1400px] mx-auto px-5 h-11 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/" className="text-sm font-semibold text-txt-primary tracking-tight hover:text-accent transition-colors">
              Prophet Arena
            </Link>
            <span className="text-txt-muted text-[10px]">/</span>
            <span className="text-xs text-txt-secondary">System Documentation</span>
          </div>
          <Link href="/" className="text-[10px] text-txt-muted hover:text-accent transition-colors px-2 py-1 rounded border border-t-border">
            Back to Dashboard
          </Link>
        </div>
      </header>

      <div className="max-w-[1400px] mx-auto px-5 py-6 flex gap-8">
        {/* Sidebar TOC */}
        <nav className="hidden lg:block w-48 flex-shrink-0 sticky top-16 self-start">
          <div className="text-[9px] uppercase tracking-widest text-txt-muted font-semibold mb-3">Contents</div>
          <div className="space-y-1.5">
            {TOC.map((item) => (
              <a
                key={item.id}
                href={`#${item.id}`}
                className="block text-[10px] text-txt-muted hover:text-accent transition-colors leading-snug py-0.5"
              >
                {item.label}
              </a>
            ))}
          </div>
        </nav>

        {/* Content */}
        <article className="flex-1 min-w-0 max-w-[900px]">
          <H1>Prophet Arena — Trading System Documentation</H1>
          <P>
            Technical reference for the Prophet Arena automated prediction market
            trading system. This document describes the complete pipeline from
            market discovery through model inference, trade execution, position
            management, and performance tracking.
          </P>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="architecture">1. System Architecture</H2>
          <P>
            The system is composed of four primary components that operate in a
            continuous loop. Each component is independently deployable but shares
            a common PostgreSQL database for state persistence.
          </P>
          <Diagram>{`┌─────────────────────────────────────────────────────────────┐
│                        TRADING LOOP                         │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌────────┐ │
│  │  Market   │───▶│  Model   │───▶│ Strategy │───▶│ Engine │ │
│  │  Fetcher  │    │ Inference│    │ Evaluate │    │Execute │ │
│  └──────────┘    └──────────┘    └──────────┘    └────────┘ │
│       │                                              │      │
│       ▼                                              ▼      │
│  ┌──────────────────────────────────────────────────────┐   │
│  │                   PostgreSQL DB                       │   │
│  │  trading_markets │ betting_predictions │ betting_orders│   │
│  │  trading_positions │ model_runs │ system_logs          │   │
│  └──────────────────────────────────────────────────────┘   │
│                           │                                  │
│                           ▼                                  │
│                  ┌─────────────────┐                         │
│                  │  Dashboard API  │                         │
│                  │   (FastAPI)     │                         │
│                  └────────┬────────┘                         │
│                           │                                  │
│                           ▼                                  │
│                  ┌─────────────────┐                         │
│                  │  Next.js        │                         │
│                  │  Dashboard      │                         │
│                  └─────────────────┘                         │
└─────────────────────────────────────────────────────────────┘`}</Diagram>

          <H3>Component Overview</H3>
          <Ul>
            <Li><strong>Worker</strong> (<Code>services/worker/main.py</Code>) — Orchestrates the trading cycle: fetches markets, runs model inference, aggregates predictions, feeds signals to the betting engine. Runs on a configurable poll interval (default: 15 minutes).</Li>
            <Li><strong>Betting Engine</strong> (<Code>packages/core/ai_prophet_core/betting/engine.py</Code>) — Core execution engine. Evaluates strategy signals, manages NET positions, enforces cash constraints, and submits orders to the exchange adapter.</Li>
            <Li><strong>API Server</strong> (<Code>services/api/main.py</Code>) — FastAPI application serving portfolio data, P&L calculations, analytics, and order history to the dashboard.</Li>
            <Li><strong>Dashboard</strong> (<Code>services/dashboard/</Code>) — Next.js frontend displaying real-time portfolio state, P&L charts, position heatmaps, model calibration, and risk metrics.</Li>
          </Ul>

          <H3>Database Schema</H3>
          <Ul>
            <Li><Code>betting_predictions</Code> — Raw model predictions with timestamps and market context</Li>
            <Li><Code>betting_signals</Code> — Strategy evaluation outputs (side, shares, price, cost)</Li>
            <Li><Code>betting_orders</Code> — Executed orders with fill information</Li>
            <Li><Code>trading_markets</Code> — Tracked market metadata and current prices</Li>
            <Li><Code>trading_positions</Code> — Aggregated position state per market</Li>
            <Li><Code>model_runs</Code> — Per-model decision log with metadata</Li>
            <Li><Code>market_price_snapshots</Code> — Time-series price history</Li>
            <Li><Code>system_logs</Code> — System events and health monitoring</Li>
          </Ul>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="market-data">2. Market Data & Ingestion</H2>

          <H3>Market Discovery</H3>
          <P>
            Each cycle, the worker fetches all open events from the Kalshi API
            via <Code>GET /trade-api/v2/events</Code> with nested markets.
            It paginates through up to 10 pages (200 events per page), collecting
            every binary market that meets the following criteria:
          </P>
          <Ul>
            <Li><strong>Status:</strong> Must be <Code>open</Code> or <Code>active</Code></Li>
            <Li><strong>Expiration:</strong> Must close within 30 days of the current time</Li>
            <Li><strong>Pricing:</strong> Must have either <Code>yes_ask</Code> or <Code>last_price</Code> available</Li>
            <Li><strong>Spread:</strong> <Code>yes_ask + no_ask ≤ 1.03</Code> (the <Code>MAX_SPREAD</Code> filter eliminates illiquid markets)</Li>
          </Ul>

          <H3>Market Ranking</H3>
          <P>
            Eligible markets are ranked by a composite score and the top
            N (<Code>WORKER_MAX_MARKETS</Code>, default 25) are selected:
          </P>
          <Formula>score = volume_24h + proximity_bonus</Formula>
          <Formula>proximity_bonus = 1.0 − 2.0 × |price − 0.5|</Formula>
          <P>
            High-volume markets are preferred. The proximity bonus gives a slight
            edge to markets priced near 50¢ (maximum uncertainty), where model
            predictions can add the most value. Markets priced at extreme ends
            (1¢ or 99¢) have less room for profitable edge.
          </P>

          <H3>Sticky Market Re-inclusion</H3>
          <P>
            After the fresh top-N selection, the worker checks for any previously-traded
            markets (those with orders in the last 30 days) that were not included in the
            top-N. These are individually re-fetched and appended to ensure the system
            continues to monitor and rebalance markets where it holds positions,
            regardless of their current volume ranking.
          </P>

          <H3>Price Representation</H3>
          <Ul>
            <Li><strong>YES price</strong> (<Code>yes_ask</Code>): Cost to buy a YES contract, range [0.01, 0.99]</Li>
            <Li><strong>NO price</strong> (<Code>no_ask</Code>): Cost to buy a NO contract, range [0.01, 0.99]</Li>
            <Li><strong>Spread:</strong> <Code>yes_ask + no_ask</Code> — a perfectly efficient market has spread = 1.00; values above 1.00 represent the market maker&apos;s edge</Li>
            <Li>Prices are stored in dollar units internally (0.05 = 5¢ per share)</Li>
            <Li>Orders use cent-based pricing (<Code>price_cents</Code>) for exchange compatibility</Li>
          </Ul>

          <H3>Data Fields Stored</H3>
          <P>
            For each tracked market, the following fields are persisted
            to <Code>trading_markets</Code>:
          </P>
          <Pre>{`market_id       — "kalshi:{ticker}" canonical identifier
ticker          — Exchange-specific ticker (e.g. KXDIAZOUT-MDC-26APR01)
event_ticker    — Parent event ticker
title           — Human-readable market name
category        — Event category (Politics, Sports, Entertainment, etc.)
expiration      — Market close/resolution time
last_price      — Last traded price
yes_ask / no_ask — Current ask prices
volume_24h      — 24-hour trading volume
updated_at      — Last refresh timestamp`}</Pre>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="prediction">3. Model Prediction Pipeline</H2>

          <H3>Model Configuration</H3>
          <P>
            Models are specified via the <Code>WORKER_MODELS</Code> environment
            variable as a comma-separated list of model specs:
          </P>
          <Pre>{`# Format: provider:model_name[:market]
# The optional :market suffix includes market prices in the prompt

WORKER_MODELS="gemini:gemini-3.1-pro-preview,gemini:gemini-3.1-pro-preview:market"

# Supported providers: openai, anthropic, gemini`}</Pre>

          <H3>Prompt Construction</H3>
          <P>
            Each model receives a structured prompt containing the market title,
            description, category, and resolution criteria. When
            the <Code>:market</Code> suffix is present, current market prices
            (YES ask, NO ask, last traded price, 24h volume) are also included
            in the prompt. This allows the model to incorporate market sentiment
            into its prediction.
          </P>

          <H3>Inference Flow</H3>
          <Ul>
            <Li>For each market, every configured model is called sequentially</Li>
            <Li>A 2-second delay is inserted between model calls to avoid API rate limits</Li>
            <Li>Failed calls are retried up to 2 times with a 5-second backoff</Li>
            <Li>Each model returns: <Code>p_yes</Code> (probability of YES), <Code>confidence</Code>, and <Code>reasoning</Code></Li>
            <Li>Individual model runs are logged to <Code>model_runs</Code> with full metadata</Li>
          </Ul>

          <H3>Prediction Output</H3>
          <Pre>{`{
  "p_yes": 0.72,        // Model's estimated probability of YES outcome
  "confidence": 0.85,   // Self-assessed confidence (0–1)
  "reasoning": "...",    // Natural language explanation
  "analysis": { ... }   // Structured analysis breakdown
}`}</Pre>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="aggregation">4. Model Aggregation</H2>

          <H3>Signed-Sum Edge Aggregation</H3>
          <P>
            Multiple model predictions are combined using a <strong>signed-sum of
            edges</strong> method. Each model independently contributes its
            disagreement with the market price:
          </P>
          <Formula>edge_i = P_i − yes_ask</Formula>
          <Formula>edge_agg = Σ edge_i</Formula>
          <Formula>P_agg = yes_ask + edge_agg</Formula>
          <P>
            This approach has several key properties:
          </P>
          <Ul>
            <Li><strong>Signal amplification:</strong> When multiple models agree on a direction, the summed edge is larger, producing a stronger signal</Li>
            <Li><strong>Disagreement damping:</strong> When models disagree, edges partially cancel, producing a conservative signal</Li>
            <Li><strong>Market-relative:</strong> Each model&apos;s contribution is measured relative to the current market price, not in absolute terms</Li>
          </Ul>

          <Note>
            The synthetic <Code>P_agg</Code> can exceed [0, 1] when models strongly agree.
            This is intentional — the rebalancing strategy uses <Code>P_agg − yes_ask</Code>
            as the target position size, so a larger sum means a larger desired position.
          </Note>

          <H3>Edge Threshold</H3>
          <P>
            If the aggregated edge is between −1¢ and +1¢ (<Code>|edge_agg| &lt; 0.01</Code>),
            the system classifies the market as <Code>HOLD</Code> and skips trading
            entirely. This prevents placing orders where the edge is smaller
            than the typical spread cost.
          </P>

          <H3>P&L Attribution</H3>
          <P>
            For P&L attribution by model, each model&apos;s contribution to
            a market&apos;s P&L is proportional to its edge fraction:
          </P>
          <Formula>fraction_i = |edge_i| / Σ|edge_j|</Formula>
          <Formula>PnL_model_i = PnL_market × fraction_i</Formula>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="edge">5. Edge Calculation</H2>

          <H3>Market Implied Probability</H3>
          <P>
            In a binary prediction market, the YES ask price approximates
            the market-implied probability:
          </P>
          <Formula>P_market ≈ yes_ask</Formula>
          <P>
            The model&apos;s edge is the difference between its estimated
            probability and the market price:
          </P>
          <Formula>edge = P_model − P_market</Formula>
          <Ul>
            <Li><strong>Positive edge:</strong> Model thinks YES is underpriced → buy YES</Li>
            <Li><strong>Negative edge:</strong> Model thinks YES is overpriced → buy NO</Li>
            <Li><strong>Zero edge:</strong> No disagreement → hold</Li>
          </Ul>

          <H3>Spread Filter</H3>
          <P>
            Before any edge calculation, the system checks the bid-ask spread:
          </P>
          <Formula>spread = yes_ask + no_ask</Formula>
          <P>
            If <Code>spread &gt; MAX_SPREAD (1.03)</Code>, the market is skipped.
            A spread of 1.03 means the market maker takes 3¢ per round-trip.
            Markets with wider spreads are too illiquid for profitable trading.
          </P>

          <H3>Bid-Ask Band Filter</H3>
          <P>
            Even if the spread is acceptable, if the model&apos;s prediction
            falls inside the bid-ask band, there is no actionable edge:
          </P>
          <Formula>lower_bound = 1.0 − no_ask</Formula>
          <Formula>upper_bound = yes_ask</Formula>
          <P>
            If <Code>lower_bound ≤ P_model ≤ upper_bound</Code>, the prediction
            is within the band and the market is skipped.
          </P>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="strategy">6. Trading Strategy</H2>

          <H3>Rebalancing Strategy (Default)</H3>
          <P>
            The system uses a <strong>rebalancing strategy</strong> that
            maintains a position proportional to the model&apos;s edge.
            At each evaluation, the desired position in YES-equivalent
            fractional units is:
          </P>
          <Formula>target = P_agg − yes_ask</Formula>
          <P>
            The strategy reads the actual portfolio position and computes
            the delta needed to reach the target:
          </P>
          <Formula>delta = target − current_position</Formula>
          <Pre>{`if delta > 0:
    → BUY YES (increase YES exposure)
if delta < 0:
    → BUY NO  (decrease YES exposure / increase NO exposure)
if |delta| < min_trade (0.005):
    → SKIP    (change too small to be worth trading)`}</Pre>

          <H3>Position Sizing</H3>
          <P>
            The fractional share quantity from the strategy is converted to
            whole contracts at the engine level:
          </P>
          <Formula>contracts = max(1, round(|shares| × 100))</Formula>
          <P>
            For example, an edge of +0.12 produces a target of 0.12, which
            translates to 12 contracts at the current YES ask price.
          </P>

          <H3>Strategy Behavior</H3>
          <Ul>
            <Li><strong>Edge increases:</strong> The target position grows, so the strategy buys more contracts to reach the new target.</Li>
            <Li><strong>Edge disappears:</strong> The target shrinks toward zero. The strategy sells existing contracts to reduce exposure. If the edge flips sign, it sells the current side and buys the opposite.</Li>
            <Li><strong>Market moves against position:</strong> If the market price moves toward the model&apos;s prediction, the edge shrinks and the strategy naturally takes profit by reducing position size.</Li>
            <Li><strong>Spread widens past MAX_SPREAD:</strong> The strategy returns None (no signal), halting all trading in that market until the spread narrows.</Li>
          </Ul>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="execution">7. Trade Execution</H2>

          <H3>Order Flow</H3>
          <Pre>{`Strategy.evaluate()
    │
    ▼
BetSignal { side, shares, price, cost }
    │
    ▼
Engine._place_order()
    ├── NET position check
    │     ├── Same side: BUY directly
    │     └── Opposite side:
    │           ├── count ≤ held → SELL existing (partial unwind)
    │           └── count > held → SELL all, then BUY remainder
    ├── Cash constraint check
    │     ├── Sufficient → proceed
    │     ├── Partial → reduce order size
    │     └── Insufficient → reject order
    ▼
Exchange Adapter (submit order)
    │
    ▼
Save to betting_orders`}</Pre>

          <H3>NET Position Management</H3>
          <P>
            When the strategy wants to buy one side but the portfolio holds
            the opposite, the engine implements NET position management:
          </P>
          <Ul>
            <Li>If the desired buy quantity ≤ the held quantity: <strong>SELL</strong> that many of the existing contracts (partial unwind, no new purchase)</Li>
            <Li>If the desired buy quantity &gt; the held quantity: <strong>SELL ALL</strong> existing contracts, then <strong>BUY</strong> the remainder on the new side</Li>
          </Ul>
          <P>
            This ensures the portfolio only ever holds one side (YES or NO)
            per market, never both simultaneously.
          </P>

          <H3>Cash Constraints</H3>
          <P>
            Before every BUY order, the engine checks available cash:
          </P>
          <Formula>order_cost = contracts × price_per_contract</Formula>
          <Ul>
            <Li>If <Code>order_cost ≤ available_cash</Code>: order proceeds normally</Li>
            <Li>If <Code>order_cost &gt; available_cash</Code> but partial fill possible: order is reduced to <Code>max_shares = floor(cash / price)</Code></Li>
            <Li>If <Code>max_shares = 0</Code>: order is rejected with an &quot;Insufficient cash&quot; error</Li>
          </Ul>
          <P>
            Cash is also capped at the strategy level: the rebalancing strategy
            caps buy-side orders by available portfolio cash before sending the
            signal to the engine.
          </P>

          <H3>Dry-Run Mode</H3>
          <P>
            When <Code>LIVE_BETTING_DRY_RUN=true</Code>, orders are simulated
            locally. The adapter returns synthetic fills at the requested price.
            All orders are tagged with <Code>dry_run=true</Code> in the database
            and displayed with a yellow &quot;DRY&quot; badge in the dashboard.
          </P>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="positions">8. Position Management</H2>

          <H3>Position Aggregation</H3>
          <P>
            After each trading cycle, the worker runs <Code>update_positions()</Code>
            which replays the entire order history to compute the current state:
          </P>
          <Pre>{`For each order (chronological):
  if BUY YES:  yes_shares += qty,  total_cost += qty × price
  if BUY NO:   yes_shares -= qty,  total_cost -= qty × price
  if SELL YES: yes_shares -= qty,  realized_pnl += (price - avg_entry) × qty
  if SELL NO:  yes_shares += qty,  realized_pnl += (price - avg_entry) × qty

Net position:
  yes_shares > 0 → holding YES contracts
  yes_shares < 0 → holding NO contracts (stored as positive NO qty)
  yes_shares ≈ 0 → flat (position deleted)`}</Pre>

          <H3>Position Fields</H3>
          <Ul>
            <Li><Code>contract</Code> — Side held: &quot;yes&quot; or &quot;no&quot;</Li>
            <Li><Code>quantity</Code> — Number of contracts held</Li>
            <Li><Code>avg_price</Code> — Weighted average entry price</Li>
            <Li><Code>realized_pnl</Code> — Cumulative profit/loss from closed trades</Li>
            <Li><Code>unrealized_pnl</Code> — Mark-to-market P&L on open position</Li>
            <Li><Code>max_position</Code> — High-water mark of position size</Li>
            <Li><Code>realized_trades</Code> — Number of sell transactions</Li>
          </Ul>

          <H3>Portfolio Snapshot</H3>
          <P>
            Before each market evaluation, the engine receives
            a <Code>PortfolioSnapshot</Code> containing:
          </P>
          <Pre>{`PortfolioSnapshot:
  cash                    — Available cash balance
  market_position_shares  — Current position size in this specific market
  market_position_side    — "yes" or "no" (or None if flat)`}</Pre>
          <P>
            The cash balance is computed as:
          </P>
          <Formula>cash = exchange_balance − capital_deployed + total_realized_pnl</Formula>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="pnl">9. P&L Calculations</H2>

          <H3>Unrealized P&L</H3>
          <P>
            Computed for each open position using current market prices:
          </P>
          <Formula>PnL_unrealized = (current_price − avg_entry) × quantity</Formula>
          <P>
            Where <Code>current_price</Code> is <Code>yes_ask</Code> for YES
            positions or <Code>no_ask</Code> for NO positions.
          </P>

          <H3>Realized P&L</H3>
          <P>
            Computed at the time of each SELL order:
          </P>
          <Formula>PnL_realized = (sell_price − avg_entry) × shares_sold</Formula>
          <P>
            Realized P&L accumulates across all sells for a given market.
            The <Code>TradingPosition</Code> table stores the running total.
          </P>

          <H3>Total P&L</H3>
          <Formula>PnL_total = Σ (realized_pnl + unrealized_pnl) for all positions</Formula>

          <H3>P&L Over Time Chart</H3>
          <P>
            The P&L time series is constructed by replaying the order history
            chronologically. At each trade point, cumulative realized P&L is
            updated for sells, and unrealized P&L is recomputed across all
            open positions using market prices known at that time. The final
            data point is corrected to match the position-based totals
            (the source of truth) to prevent drift between the two calculations.
          </P>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="risk">10. Risk & Capital Allocation</H2>

          <H3>Risk Metrics</H3>
          <Ul>
            <Li><strong>Sharpe Ratio:</strong> Risk-adjusted return computed from the per-trade P&L distribution</Li>
            <Li><strong>Sortino Ratio:</strong> Like Sharpe but only penalizes downside volatility</Li>
            <Li><strong>Max Drawdown:</strong> Largest peak-to-trough decline in cumulative P&L</Li>
            <Li><strong>Win Rate:</strong> Percentage of markets with positive total P&L</Li>
            <Li><strong>Profit Factor:</strong> Ratio of gross profit to gross loss</Li>
            <Li><strong>Win/Loss Ratio:</strong> Average winning trade / average losing trade</Li>
          </Ul>

          <H3>Capital Constraints</H3>
          <P>
            Capital allocation is enforced at two levels:
          </P>
          <Ul>
            <Li><strong>Strategy level:</strong> The RebalancingStrategy caps buy orders to available cash before emitting a signal</Li>
            <Li><strong>Engine level:</strong> A hard cash check rejects or reduces any BUY order that exceeds available cash, ensuring the portfolio never goes negative</Li>
          </Ul>

          <H3>Position Limits</H3>
          <P>
            Position sizing is implicitly bounded by the edge magnitude.
            Since <Code>target = P_agg − yes_ask</Code> and prices are bounded
            [0, 1], the maximum single-market position is limited by:
          </P>
          <Ul>
            <Li>The summed edge across models (typically 0.01–0.50 in fractional units)</Li>
            <Li>Available cash (hard cap at engine level)</Li>
            <Li>The <Code>min_trade</Code> threshold (0.005) prevents micro-positions</Li>
          </Ul>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="activity">11. Activity Logging</H2>

          <H3>System Logs</H3>
          <P>
            The <Code>system_logs</Code> table captures operational events
            at multiple severity levels:
          </P>
          <Ul>
            <Li><Code>INFO</Code> — Cycle completions, position updates, normal operations</Li>
            <Li><Code>WARNING</Code> — Non-critical issues (market fetch failures, empty cycles)</Li>
            <Li><Code>ERROR</Code> — Predictor failures, order rejections, database errors</Li>
            <Li><Code>HEARTBEAT</Code> — Periodic health pings from the worker</Li>
            <Li><Code>ALERT</Code> — High-edge opportunities or unusual market conditions</Li>
          </Ul>

          <H3>Model Run Logging</H3>
          <P>
            Every model evaluation is logged to <Code>model_runs</Code> with:
          </P>
          <Ul>
            <Li>Model identifier (e.g. <Code>gemini:gemini-3.1-pro-preview:market</Code>)</Li>
            <Li>Decision classification: <Code>BUY_YES</Code>, <Code>BUY_NO</Code>, <Code>HOLD</Code>, <Code>SKIP</Code></Li>
            <Li>Confidence score</Li>
            <Li>Full metadata JSON including <Code>p_yes</Code>, <Code>reasoning</Code>, <Code>analysis</Code>, and market prices at time of prediction</Li>
          </Ul>
          <P>
            Aggregated runs store additional metadata including per-model edge
            breakdowns, enabling P&L attribution and model comparison.
          </P>

          <H3>Price Snapshots</H3>
          <P>
            Market prices are periodically captured
            to <Code>market_price_snapshots</Code> for time-series analysis.
            Each snapshot records: market prices, volume, and the model&apos;s
            aggregated probability at that point in time. These power the
            &quot;Market Price vs Model Probability&quot; charts on the dashboard.
          </P>

          {/* ────────────────────────────────────────────────────── */}
          <H2 id="config">12. Configuration Reference</H2>

          <Pre>{`# ── Core Trading ──────────────────────────────────────────────
LIVE_BETTING_ENABLED=true      # Master kill switch
LIVE_BETTING_DRY_RUN=true      # Simulate orders (no real money)
DATABASE_URL=postgresql://...   # PostgreSQL connection string

# ── Kalshi API ────────────────────────────────────────────────
KALSHI_API_KEY_ID=...          # API key ID
KALSHI_PRIVATE_KEY_B64=...     # Base64-encoded RSA private key
KALSHI_BASE_URL=https://api.elections.kalshi.com

# ── Worker ────────────────────────────────────────────────────
WORKER_POLL_INTERVAL_SEC=900   # Seconds between cycles (default: 15 min)
WORKER_MODELS=gemini:gemini-3.1-pro-preview,gemini:gemini-3.1-pro-preview:market
WORKER_STRATEGY=rebalancing    # Strategy: "default" or "rebalancing"
WORKER_MAX_MARKETS=25          # Max markets to analyze per cycle

# ── Model API Keys ────────────────────────────────────────────
GOOGLE_API_KEY=...             # For Gemini models
OPENAI_API_KEY=...             # For OpenAI models
ANTHROPIC_API_KEY=...          # For Anthropic models

# ── Strategy Constants ────────────────────────────────────────
MAX_SPREAD=1.03                # Max yes_ask + no_ask (config.py)
MIN_TRADE=0.005                # Minimum position delta to trade
MAX_MARKETS_PER_TICK=10        # Max orders per cycle`}</Pre>

          <div className="mt-12 mb-8 border-t border-t-border pt-6 text-[10px] text-txt-muted">
            Last updated: {new Date().toLocaleDateString("en-US", { year: "numeric", month: "long", day: "numeric" })}
          </div>
        </article>
      </div>
    </main>
  );
}
