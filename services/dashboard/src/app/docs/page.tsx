"use client";

import Link from "next/link";

/* ═══════════════════════════════════════════════════════════════
   Prophet Arena — System Documentation
   ═══════════════════════════════════════════════════════════════ */

// ── Reusable doc primitives ─────────────────────────────────────

function H1({ children }: { children: React.ReactNode }) {
  return (
    <h1 className="text-xl font-bold text-txt-primary mb-4 mt-10 first:mt-0 border-b border-t-border pb-2">
      {children}
    </h1>
  );
}
function H2({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h2 id={id} className="text-base font-semibold text-txt-primary mt-10 mb-3 scroll-mt-20">
      {children}
    </h2>
  );
}
function H3({ children, id }: { children: React.ReactNode; id?: string }) {
  return (
    <h3 id={id} className="text-sm font-semibold text-txt-secondary mt-6 mb-2 scroll-mt-20">
      {children}
    </h3>
  );
}
function P({ children }: { children: React.ReactNode }) {
  return <p className="text-xs text-txt-secondary leading-relaxed mb-3">{children}</p>;
}
function Code({ children }: { children: React.ReactNode }) {
  return (
    <code className="text-[11px] bg-t-panel-alt px-1.5 py-0.5 rounded text-accent font-mono">
      {children}
    </code>
  );
}
function Pre({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div className="mb-4">
      {label && (
        <div className="text-[9px] uppercase tracking-widest text-txt-muted font-semibold bg-t-panel-alt border border-b-0 border-t-border rounded-t px-3 py-1.5">
          {label}
        </div>
      )}
      <pre
        className={`text-[11px] bg-t-panel-alt border border-t-border ${label ? "rounded-b rounded-tr" : "rounded"} p-4 overflow-x-auto font-mono text-txt-secondary leading-relaxed whitespace-pre`}
      >
        {children}
      </pre>
    </div>
  );
}
function Formula({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div className="mb-3">
      {label && <div className="text-[9px] text-txt-muted mb-1 ml-1">{label}</div>}
      <div className="text-[12px] bg-t-panel-alt border border-t-border/50 rounded px-4 py-3 font-mono text-accent text-center tracking-wide">
        {children}
      </div>
    </div>
  );
}
function Ul({ children }: { children: React.ReactNode }) {
  return (
    <ul className="text-xs text-txt-secondary leading-relaxed mb-3 ml-4 space-y-1.5 list-disc marker:text-txt-muted">
      {children}
    </ul>
  );
}
function Ol({ children }: { children: React.ReactNode }) {
  return (
    <ol className="text-xs text-txt-secondary leading-relaxed mb-3 ml-4 space-y-1.5 list-decimal marker:text-txt-muted">
      {children}
    </ol>
  );
}
function Li({ children }: { children: React.ReactNode }) {
  return <li>{children}</li>;
}
function Note({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] bg-accent/5 border border-accent/20 rounded px-3 py-2.5 mb-4 text-txt-secondary leading-relaxed">
      <span className="text-accent font-semibold">Note: </span>
      {children}
    </div>
  );
}
function Warn({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] bg-warn/5 border border-warn/20 rounded px-3 py-2.5 mb-4 text-txt-secondary leading-relaxed">
      <span className="text-warn font-semibold">Warning: </span>
      {children}
    </div>
  );
}
function Diagram({ children }: { children: React.ReactNode }) {
  return (
    <pre className="text-[10px] bg-t-bg border border-t-border rounded p-4 mb-4 font-mono text-txt-muted leading-snug overflow-x-auto">
      {children}
    </pre>
  );
}
function Table({
  headers,
  rows,
}: {
  headers: string[];
  rows: (string | React.ReactNode)[][];
}) {
  return (
    <div className="mb-4 overflow-x-auto">
      <table className="w-full text-[11px] border-collapse">
        <thead>
          <tr className="border-b border-t-border">
            {headers.map((h, i) => (
              <th
                key={i}
                className="text-left text-txt-muted font-semibold uppercase tracking-wider py-2 pr-4 text-[9px]"
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-t-border/40 hover:bg-t-panel-alt/30">
              {row.map((cell, j) => (
                <td key={j} className="py-2 pr-4 text-txt-secondary align-top">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function Divider() {
  return <div className="border-t border-t-border/50 my-8" />;
}

// ── Table of contents ───────────────────────────────────────────

const TOC = [
  { id: "architecture", label: "1. System Architecture" },
  { id: "market-data", label: "2. Market Data & Ingestion" },
  { id: "prediction", label: "3. Model Prediction Pipeline" },
  { id: "edge", label: "4. Edge Calculation" },
  { id: "strategy", label: "5. Trading Strategy" },
  { id: "execution", label: "6. Trade Execution" },
  { id: "positions", label: "7. Position Management" },
  { id: "pnl", label: "8. P&L Calculations" },
  { id: "risk", label: "9. Risk & Capital Allocation" },
  { id: "activity", label: "10. Activity Logging" },
  { id: "config", label: "11. Configuration Reference" },
];

// ── Main page ───────────────────────────────────────────────────

export default function DocsPage() {
  return (
    <main className="min-h-screen bg-t-bg">
      {/* Header */}
      <header className="border-b border-t-border bg-t-panel/90 backdrop-blur-md sticky top-0 z-20">
        <div className="max-w-[1400px] mx-auto px-5 h-11 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link
              href="/"
              className="text-sm font-semibold text-txt-primary tracking-tight hover:text-accent transition-colors"
            >
              Prophet Arena
            </Link>
            <span className="text-txt-muted text-[10px]">/</span>
            <span className="text-xs text-txt-secondary">System Documentation</span>
          </div>
          <Link
            href="/"
            className="text-[10px] text-txt-muted hover:text-accent transition-colors px-2 py-1 rounded border border-t-border"
          >
            ← Dashboard
          </Link>
        </div>
      </header>

      <div className="max-w-[1400px] mx-auto px-5 py-8 flex gap-10">
        {/* Sidebar TOC */}
        <nav className="hidden lg:block w-52 flex-shrink-0 sticky top-16 self-start max-h-[calc(100vh-5rem)] overflow-y-auto">
          <div className="text-[9px] uppercase tracking-widest text-txt-muted font-semibold mb-3 px-1">
            Contents
          </div>
          <div className="space-y-0.5">
            {TOC.map((item) => (
              <a
                key={item.id}
                href={`#${item.id}`}
                className="block text-[10px] text-txt-muted hover:text-accent transition-colors leading-snug py-1 px-1 rounded hover:bg-t-panel-alt/50"
              >
                {item.label}
              </a>
            ))}
          </div>
          <div className="mt-8 pt-4 border-t border-t-border/50 text-[9px] text-txt-muted px-1 leading-relaxed">
            Last updated
            <br />
            {new Date().toLocaleDateString("en-US", {
              year: "numeric",
              month: "long",
              day: "numeric",
            })}
          </div>
        </nav>

        {/* Content */}
        <article className="flex-1 min-w-0 max-w-[860px] pb-20">
          <H1>Prophet Arena — Trading System Documentation</H1>
          <P>
            Internal technical reference for the Prophet Arena automated prediction market trading
            system. This document describes every layer of the pipeline: market discovery, model
            inference, edge calculation, order execution, position tracking, and P&L accounting.
            Written for engineers and quantitative traders who need to understand or modify the
            system.
          </P>

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="architecture">1. System Architecture</H2>

          <P>
            The system runs as four independent processes sharing a single PostgreSQL database.
            There is no message bus — components communicate exclusively through the database.
            The worker is the only writer to market and prediction tables; the API server is
            read-only.
          </P>

          <Diagram>{`                      TRADING CYCLE (every 15 min)
┌─────────────────────────────────────────────────────────────────────┐
│                                                                     │
│  ┌──────────────┐   ┌─────────────────┐   ┌────────────────────┐   │
│  │   Kalshi API  │──▶│  Worker Process │──▶│  Betting Engine    │   │
│  │ (market data) │   │  services/worker│   │  packages/core/    │   │
│  └──────────────┘   │  /main.py       │   │  betting/engine.py │   │
│                     └────────┬────────┘   └────────┬───────────┘   │
│                              │                     │               │
│                              ▼                     ▼               │
│                   ┌─────────────────────────────────────────┐      │
│                   │             PostgreSQL Database          │      │
│                   │                                         │      │
│                   │  trading_markets      model_runs        │      │
│                   │  trading_positions    betting_orders     │      │
│                   │  betting_predictions  betting_signals    │      │
│                   │  market_price_snapshots  system_logs    │      │
│                   └──────────────────┬──────────────────────┘      │
│                                      │                             │
└──────────────────────────────────────┼─────────────────────────────┘
                                       │ (read-only)
                          ┌────────────▼─────────────┐
                          │   FastAPI Server          │
                          │   services/api/main.py    │
                          └────────────┬─────────────┘
                                       │ JSON/REST
                          ┌────────────▼─────────────┐
                          │   Next.js Dashboard       │
                          │   services/dashboard/     │
                          └──────────────────────────┘`}</Diagram>

          <H3>Component Responsibilities</H3>
          <Table
            headers={["Component", "Path", "Role"]}
            rows={[
              [
                <Code key="w">Worker</Code>,
                "services/worker/main.py",
                "Orchestrates each trading cycle: fetches markets, runs model inference, evaluates strategy, places orders, updates positions.",
              ],
              [
                <Code key="e">Betting Engine</Code>,
                "packages/core/ai_prophet_core/betting/engine.py",
                "Core execution layer. Receives a prediction, runs the strategy, manages NET positions, enforces cash limits, submits to exchange adapter.",
              ],
              [
                <Code key="s">Strategy</Code>,
                "packages/core/ai_prophet_core/betting/strategy.py",
                "Stateless evaluation function. Given (p_yes, yes_ask, no_ask), returns a BetSignal or None.",
              ],
              [
                <Code key="a">API Server</Code>,
                "services/api/main.py",
                "FastAPI app exposing P&L, positions, analytics, calibration, and order history endpoints. Read-only from the DB.",
              ],
              [
                <Code key="d">Dashboard</Code>,
                "services/dashboard/",
                "Next.js frontend. Polls the API every 30s. Renders P&L charts, position heatmap, calibration plots, and activity feed.",
              ],
            ]}
          />

          <H3>Database Tables</H3>
          <Table
            headers={["Table", "Purpose"]}
            rows={[
              [<Code key="bp">betting_predictions</Code>, "Every model prediction: p_yes, yes_ask, no_ask, source, timestamp"],
              [<Code key="bs">betting_signals</Code>, "Strategy evaluation output linked to a prediction: side, shares, price, cost"],
              [<Code key="bo">betting_orders</Code>, "Executed or simulated orders: fill price, filled shares, action (BUY/SELL), dry_run flag"],
              [<Code key="tm">trading_markets</Code>, "Tracked market metadata and current prices"],
              [<Code key="tp">trading_positions</Code>, "Aggregated position per market: side, qty, avg_price, realized_pnl, unrealized_pnl"],
              [<Code key="mr">model_runs</Code>, "Per-model decision log: decision label, confidence, full metadata JSON"],
              [<Code key="mp">market_price_snapshots</Code>, "Time-series price history for charting model probability vs market price"],
              [<Code key="sl">system_logs</Code>, "Operational events: INFO, WARNING, ERROR, HEARTBEAT, ALERT"],
            ]}
          />

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="market-data">2. Market Data & Ingestion</H2>

          <H3>Market Discovery</H3>
          <P>
            Each cycle the worker calls <Code>GET /trade-api/v2/events?with_nested_markets=true</Code>
            {" "}on the Kalshi API, paginating up to 10 pages of 200 events each. Every nested
            binary market is evaluated against the following filters:
          </P>
          <Ul>
            <Li>
              <strong>Status:</strong> Must be <Code>open</Code> or <Code>active</Code>
            </Li>
            <Li>
              <strong>Expiration:</strong> Must close within 30 days of the current time
            </Li>
            <Li>
              <strong>Pricing:</strong> Must have <Code>yes_ask</Code> or <Code>last_price</Code>
            </Li>
            <Li>
              <strong>Spread:</strong> <Code>yes_ask + no_ask ≤ MAX_SPREAD (1.03)</Code> — eliminates
              illiquid markets where the market maker&apos;s cut exceeds 3¢ per round-trip
            </Li>
          </Ul>

          <H3>Market Ranking and Selection</H3>
          <P>
            Passing markets are ranked by a composite score and the top{" "}
            <Code>WORKER_MAX_MARKETS</Code> (default 25) are selected:
          </P>
          <Formula label="Ranking score">score = volume_24h + proximity_bonus</Formula>
          <Formula label="Proximity bonus (max at price = 0.50)">
            proximity_bonus = 1.0 − 2.0 × |price − 0.5|
          </Formula>
          <P>
            High-volume markets are prioritised. The proximity bonus gives a slight preference to
            markets priced near 50¢ — at maximum uncertainty, model predictions have the highest
            potential informational edge. Markets priced at 1¢ or 99¢ have almost no room for
            profitable disagreement with the market.
          </P>

          <H3>Sticky Market Re-inclusion</H3>
          <P>
            After the top-N selection, the worker queries for any markets where orders were placed
            in the last 30 days that were not selected in the fresh top-N. These are individually
            re-fetched from Kalshi and appended to the active list. This ensures the system
            continues monitoring and rebalancing positions regardless of current volume ranking.
          </P>
          <Note>
            The total active list is capped at <Code>WORKER_MAX_ACTIVE_MARKETS</Code> (default 40)
            to prevent unbounded growth.
          </Note>

          <H3>Price Representation</H3>
          <P>
            Kalshi binary markets price YES and NO contracts independently. The relationship is:
          </P>
          <Formula>yes_ask + no_ask = 1.00 + market_maker_spread</Formula>
          <Table
            headers={["Field", "Range", "Description"]}
            rows={[
              [<Code key="ya">yes_ask</Code>, "[0.01, 0.99]", "Cost in dollars to buy one YES contract. Pays $1 if YES resolves."],
              [<Code key="na">no_ask</Code>, "[0.01, 0.99]", "Cost in dollars to buy one NO contract. Pays $1 if NO resolves."],
              ["spread", "[1.00, ∞)", "yes_ask + no_ask. Perfectly liquid market = 1.00. Filtered at > 1.03."],
              [<Code key="lp">last_price</Code>, "[0.01, 0.99]", "Last traded price. Used as fallback when ask prices are unavailable."],
            ]}
          />
          <P>
            Prices are stored internally in dollar units (e.g., <Code>0.17</Code> = 17¢). When
            orders are submitted to Kalshi, prices are converted to integer cents
            (<Code>price_cents = round(price × 100)</Code>), clamped to [1, 99].
          </P>

          <H3>Data Stored Per Market</H3>
          <Pre label="trading_markets fields">{`market_id         — "kalshi:{ticker}"  (canonical internal identifier)
ticker            — Exchange ticker     (e.g. KXDIAZOUT-MDC-26APR01)
event_ticker      — Parent event        (e.g. KXDIAZOUT-MDC)
title             — Human-readable name
category          — Event category      (Politics, Sports, Entertainment, ...)
expiration        — ISO timestamp of market close/resolution
last_price        — Last traded price
yes_ask / no_ask  — Current ask prices
volume_24h        — 24-hour trading volume in dollars
updated_at        — Timestamp of last data refresh`}</Pre>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="prediction">3. Model Prediction Pipeline</H2>

          <H3>Model Configuration</H3>
          <P>
            The worker runs a single model per cycle, specified via{" "}
            <Code>WORKER_MODELS</Code>. The format is{" "}
            <Code>provider:model_name</Code> with an optional <Code>:market</Code> suffix that
            includes live market prices in the prompt context:
          </P>
          <Pre label="Environment variable">{`# No market data in prompt (default — cleaner signal)
WORKER_MODELS=gemini:gemini-3.1-pro-preview

# With live market prices included in the prompt
WORKER_MODELS=gemini:gemini-3.1-pro-preview:market

# Supported providers
openai     — OpenAI models (requires OPENAI_API_KEY)
anthropic  — Anthropic models (requires ANTHROPIC_API_KEY)
gemini     — Google Gemini models (requires GOOGLE_API_KEY)`}</Pre>

          <Note>
            Only the first configured model is used per cycle. The system no longer aggregates
            across multiple models — one model produces one prediction, which feeds directly into
            the betting engine.
          </Note>

          <H3>Prompt Construction</H3>
          <P>
            Each model call receives a structured prompt containing:
          </P>
          <Ul>
            <Li>Market title and full description</Li>
            <Li>Resolution criteria</Li>
            <Li>Category and expiration date</Li>
            <Li>
              When <Code>:market</Code> suffix is set: current YES ask, NO ask, last traded price,
              and 24h volume — allowing the model to incorporate market sentiment into its estimate
            </Li>
          </Ul>
          <P>
            The model is instructed to return a structured JSON response. Only the probability
            estimate is used for trading decisions; reasoning and confidence are stored for
            diagnostic purposes.
          </P>

          <H3>Prediction Output Schema</H3>
          <Pre label="Model response">{`{
  "p_yes":      0.72,   // Estimated probability of YES outcome [0.0, 1.0]
  "confidence": 0.85,   // Self-reported confidence [0.0, 1.0] (diagnostic only)
  "reasoning":  "...",  // Natural language explanation (stored, not used in trading)
  "analysis":   { ... } // Optional structured breakdown
}`}</Pre>

          <H3>Inference Scheduling</H3>
          <Ul>
            <Li>
              The worker runs on a configurable interval (<Code>WORKER_POLL_INTERVAL_SEC</Code>,
              default 900s = 15 minutes)
            </Li>
            <Li>Each market in the active list receives one model call per cycle</Li>
            <Li>A 2-second delay is inserted between model calls to respect rate limits</Li>
            <Li>Failed calls are retried up to 2 times with 5-second backoff</Li>
            <Li>
              Each model call is logged to <Code>model_runs</Code> with the decision label,
              confidence, and full metadata JSON
            </Li>
            <Li>
              A price snapshot is written to <Code>market_price_snapshots</Code> after each
              prediction for time-series charting
            </Li>
          </Ul>

          <H3>Decision Labels</H3>
          <P>
            After the model returns <Code>p_yes</Code>, the worker computes a human-readable
            decision label stored in <Code>model_runs.decision</Code>:
          </P>
          <Pre label="Decision classification logic">{`diff = p_yes - yes_ask

if diff > 0:    decision = "BUY_YES"  # Model thinks YES is underpriced
elif diff < 0:  decision = "BUY_NO"   # Model thinks YES is overpriced
else:           decision = "HOLD"     # No disagreement with market

# Whether a trade is actually placed depends on the strategy filters
# (spread check, band check) — the label is diagnostic only`}</Pre>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="edge">4. Edge Calculation</H2>

          <H3>Market-Implied Probability</H3>
          <P>
            In a binary prediction market, the YES ask price is the market&apos;s consensus
            estimate of the probability that YES resolves:
          </P>
          <Formula>P_market ≈ yes_ask</Formula>
          <P>
            This is an approximation. The true market-implied probability sits at the midpoint
            of the bid-ask spread, but we use the ask price as a conservative reference since
            we are always buying (never selling short).
          </P>

          <H3>Edge Definition</H3>
          <Formula label="YES-framed edge (positive = YES opportunity, negative = NO opportunity)">
            edge = P_model − yes_ask
          </Formula>
          <Table
            headers={["Edge sign", "Interpretation", "Action"]}
            rows={[
              ["edge > 0", "Model thinks YES is underpriced. Market assigns 30% but model says 45%.", "BUY YES"],
              ["edge < 0", "Model thinks YES is overpriced. Market assigns 70% but model says 55%.", "BUY NO"],
              ["edge = 0", "Model agrees with the market exactly.", "No trade"],
            ]}
          />

          <H3>Filter 1 — Spread Check</H3>
          <P>
            Before evaluating edge, the strategy checks whether the market is liquid enough to
            trade profitably:
          </P>
          <Formula>spread = yes_ask + no_ask</Formula>
          <P>
            If <Code>spread &gt; MAX_SPREAD (1.03)</Code>: skip this market entirely. A 3¢
            spread means you immediately lose 3¢ per dollar on a round-trip trade. Any model
            edge smaller than the spread is not profitable.
          </P>

          <H3>Filter 2 — Bid-Ask Band Check</H3>
          <P>
            Even in a liquid market, if the model&apos;s prediction falls inside the bid-ask
            band, there is no exploitable edge. The band is:
          </P>
          <Formula label="Lower bound of the band">lower_bound = 1.0 − no_ask</Formula>
          <Formula label="Upper bound of the band">upper_bound = yes_ask</Formula>
          <P>
            If <Code>lower_bound ≤ P_model ≤ upper_bound</Code>: skip. The model is not
            disagreeing with the market — it is predicting a probability that is consistent with
            both the YES and NO prices simultaneously.
          </P>

          <H3>Worked Example</H3>
          <Pre label="Example: model disagrees with market on NO side">{`Market: KXMULLIN-26APR01
  yes_ask = 0.20,  no_ask = 0.82
  spread  = 1.02   → PASS (≤ 1.03)

  lower_bound = 1.0 - 0.82 = 0.18
  upper_bound = 0.20
  band = [0.18, 0.20]

Model: p_yes = 0.08
  Inside band? No (0.08 < 0.18)  → PASS
  edge = 0.08 - 0.20 = -0.12     → BUY NO, 12 contracts`}</Pre>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="strategy">5. Trading Strategy</H2>

          <H3>Default Betting Strategy</H3>
          <P>
            The system uses <Code>DefaultBettingStrategy</Code> — a simple, stateless
            edge-proportional strategy. Given a model prediction and current market prices, it
            decides whether to trade, which side, how many contracts, and at what price. The
            strategy has no memory between cycles.
          </P>

          <H3>Decision Logic (full pseudocode)</H3>
          <Pre label="packages/core/ai_prophet_core/betting/strategy.py">{`def evaluate(market_id, p_yes, yes_ask, no_ask) -> BetSignal | None:

    # Step 1: Spread filter — skip illiquid markets
    spread = yes_ask + no_ask
    if spread > MAX_SPREAD (1.03):
        return None

    # Step 2: Band filter — skip if model agrees with market
    lower_bound = 1.0 - no_ask
    upper_bound = yes_ask
    if lower_bound <= p_yes <= upper_bound:
        return None

    # Step 3: Size and direction
    diff = p_yes - yes_ask

    if diff > 0:
        # YES underpriced — buy YES
        shares = diff          # e.g. diff=0.12 → 12 contracts
        price  = yes_ask
        side   = "yes"

    elif diff < 0:
        # YES overpriced — buy NO
        shares = abs(diff)     # e.g. diff=-0.10 → 10 contracts
        price  = no_ask
        side   = "no"

    return BetSignal(side=side, shares=shares, price=price, cost=shares*price)`}</Pre>

          <H3>Position Sizing</H3>
          <P>
            Shares are sized by the magnitude of <Code>|edge|</Code> — the absolute disagreement
            with the market price. Both YES and NO positions use the same formula, making sizing
            symmetric:
          </P>
          <Formula>contracts = max(1, round(|p_yes − yes_ask| × 100))</Formula>
          <P>
            A 10pp edge (0.10) produces 10 contracts. A 25pp edge produces 25 contracts.
            The minimum order is always 1 contract.
          </P>

          <H3>Strategy Behaviour Under Different Market Conditions</H3>
          <Table
            headers={["Condition", "What happens"]}
            rows={[
              ["Edge grows (market moves away from model)", "Next cycle produces a larger abs(diff), more contracts are ordered — adds to the position."],
              ["Edge shrinks (market moves toward model)", "Smaller abs(diff) means fewer target contracts. If the engine holds more than the new signal, the NET logic sells the difference."],
              ["Edge flips sign (market overshoots model)", "diff changes sign. Strategy signals the opposite side. Engine sells all existing contracts, buys the new side."],
              ["Market becomes illiquid (spread > 1.03)", "Strategy returns None. No signal is emitted. Existing position is untouched until the spread narrows."],
              ["Model prediction == yes_ask exactly", "diff = 0, strategy returns None. No trade."],
            ]}
          />

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="execution">6. Trade Execution</H2>

          <H3>Order Flow</H3>
          <Diagram>{`Strategy.evaluate(p_yes, yes_ask, no_ask)
          │
          ▼
  BetSignal { side, shares, price, cost }
          │
          ▼
  Engine._place_and_log_order()
          │
          ├─ Convert fractional shares → integer contracts
          │    count = max(1, round(shares × 100))
          │    price_cents = max(1, min(99, round(price × 100)))
          │
          ├─ NET position check (see below)
          │
          ├─ Cash constraint check (see below)
          │
          ▼
  Exchange Adapter (KalshiAdapter)
  ├── dry_run=true  → synthetic fill at limit_price
  └── dry_run=false → submit to Kalshi REST API, poll for fill
          │
          ▼
  Save to betting_orders (side, count, price_cents, fill_price,
                          filled_shares, action, status, dry_run)`}</Diagram>

          <H3>NET Position Management</H3>
          <P>
            The engine enforces a one-side-per-market rule: you can hold YES or NO contracts
            for a given market, never both. When the strategy signals a side opposite to what
            is currently held, the engine handles the flip automatically.
          </P>
          <Pre label="NET logic">{`held_side  = portfolio.market_position_side   # "yes" or "no" (or None)
held_count = portfolio.market_position_shares  # current quantity
want_side  = signal.side                       # new desired side
want_count = count                             # new desired quantity

if held_side == want_side:
    # Same direction → BUY more directly
    action = "BUY"

elif want_count <= held_count:
    # Opposite side, partial unwind → convert to a SELL of existing contracts
    # (No new purchase — just reduces exposure on the held side)
    action = "SELL"
    side   = held_side
    count  = want_count

else:
    # Opposite side, full flip →
    #   1. SELL ALL held contracts (separate order)
    #   2. BUY (want_count - held_count) on the new side
    submit SELL(held_side, held_count)
    action = "BUY"
    count  = want_count - held_count`}</Pre>

          <H3>Cash Constraints</H3>
          <P>
            Before every BUY, the engine checks available cash:
          </P>
          <Formula>order_cost = contracts × price_per_contract</Formula>
          <Formula>available_cash = exchange_balance − capital_deployed + total_realized_pnl</Formula>
          <Ul>
            <Li>
              <Code>order_cost ≤ available_cash</Code>: proceed normally
            </Li>
            <Li>
              <Code>order_cost &gt; available_cash</Code>, partial fill possible:{" "}
              reduce to <Code>max_shares = floor(available_cash / price)</Code>
            </Li>
            <Li>
              <Code>max_shares = 0</Code>: reject with &quot;Insufficient cash&quot; error, log
              to <Code>system_logs</Code>
            </Li>
          </Ul>

          <H3>Dry-Run Mode</H3>
          <P>
            When <Code>LIVE_BETTING_DRY_RUN=true</Code> (the default), no orders are sent to
            Kalshi. The adapter simulates a fill at the requested limit price and returns a
            synthetic <Code>OrderResult</Code> with <Code>status=DRY_RUN</Code>. All simulated
            orders are stored in <Code>betting_orders</Code> with <Code>dry_run=true</Code> and
            shown with a yellow &quot;DRY&quot; badge in the dashboard.
          </P>
          <Warn>
            Setting <Code>LIVE_BETTING_DRY_RUN=false</Code> sends real orders to Kalshi and
            spends real money. Ensure credentials are correct and capital is intentionally
            deployed before switching.
          </Warn>

          <H3>Order Polling (live mode only)</H3>
          <P>
            In live mode, orders may initially return with status <Code>PENDING</Code> (resting
            on the order book). The engine polls the exchange up to 5 times at 2-second intervals
            waiting for a fill, cancellation, or rejection. The final status is persisted to the
            database regardless of outcome.
          </P>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="positions">7. Position Management</H2>

          <H3>Position Aggregation</H3>
          <P>
            After each trading cycle, <Code>update_positions()</Code> replays the entire{" "}
            <Code>betting_orders</Code> history in chronological order to compute the current
            state of every position from scratch. This is an event-sourcing pattern — the
            position table is a derived view, not a primary source of truth.
          </P>
          <Pre label="Position replay algorithm">{`positions = {}   # keyed by ticker

for order in all_filled_orders (chronological):
    shares = order.filled_shares or order.count
    price  = order.fill_price or (order.price_cents / 100)

    # Integrity check: fill_price stored as cents in corrupted rows
    if price > 1.0:
        price = price / 100.0    # recover fraction from accidental cents storage

    if order.action == "BUY":
        if order.side == "yes":
            pos.yes_shares  += shares
            pos.total_cost  += shares × price
        else:  # "no"
            pos.yes_shares  -= shares   # NO reduces YES-equivalent position
            pos.total_cost  -= shares × price

    elif order.action == "SELL":
        avg_entry = total_cost / yes_shares
        realized_pnl += (price - avg_entry) × shares
        adjust yes_shares and total_cost by avg_entry (not sell price)

    # Flat detection: reset when position is within 0.001 of zero
    if abs(pos.yes_shares) < 0.001:
        pos.total_cost  = 0.0
        pos.yes_shares  = 0.0

# Derive fields for TradingPosition table
side      = "yes" if yes_shares > 0 else "no"
qty       = abs(yes_shares)
avg_price = abs(total_cost / yes_shares)`}</Pre>

          <H3>Unrealized P&L Computation</H3>
          <P>
            After all positions are aggregated, the worker fetches current market prices and
            computes unrealized P&L for each open position:
          </P>
          <Pre label="Unrealized PnL per position">{`if side == "yes":
    current_price = yes_ask
else:
    current_price = no_ask

unrealized_pnl = (current_price − avg_price) × qty`}</Pre>

          <H3>Position Fields</H3>
          <Table
            headers={["Field", "Type", "Description"]}
            rows={[
              [<Code key="c">contract</Code>, "string", '"yes" or "no" — which side is held'],
              [<Code key="q">quantity</Code>, "float", "Number of contracts currently held"],
              [<Code key="ap">avg_price</Code>, "float", "Weighted average entry price across all fills for this position"],
              [<Code key="rp">realized_pnl</Code>, "float", "Cumulative profit from closed (sold) contracts"],
              [<Code key="up">unrealized_pnl</Code>, "float", "Mark-to-market profit on the current open position"],
              [<Code key="mp">max_position</Code>, "float", "High-water mark: largest position size ever held"],
              [<Code key="rt">realized_trades</Code>, "int", "Number of individual SELL transactions"],
            ]}
          />

          <H3>Portfolio Snapshot (fed to engine each cycle)</H3>
          <P>
            Before evaluating each market, the worker builds a <Code>PortfolioSnapshot</Code>
            {" "}from the current <Code>TradingPosition</Code> rows:
          </P>
          <Pre label="PortfolioSnapshot construction">{`all_positions = query TradingPosition

capital_deployed = Σ (avg_price × quantity) for all positions
total_realized   = Σ realized_pnl for all positions
exchange_balance = adapter.get_balance()

available_cash   = exchange_balance − capital_deployed + total_realized

# Per-market context
mkt_pos_shares = this_market.quantity  (or 0 if no position)
mkt_pos_side   = this_market.contract  (or None)

PortfolioSnapshot(
    cash                   = available_cash,
    market_position_shares = mkt_pos_shares,
    market_position_side   = mkt_pos_side,
)`}</Pre>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="pnl">8. P&L Calculations</H2>

          <H3>Unrealized P&L</H3>
          <P>
            Profit on an open position, computed using the current market ask price:
          </P>
          <Formula label="For a YES position">
            PnL_unrealized = (yes_ask − avg_entry) × quantity
          </Formula>
          <Formula label="For a NO position">
            PnL_unrealized = (no_ask − avg_entry) × quantity
          </Formula>
          <P>
            Example: Bought 10 NO contracts at 0.82. Current no_ask = 0.86.
            <br />
            PnL = (0.86 − 0.82) × 10 = +$0.40 unrealized.
          </P>

          <H3>Realized P&L</H3>
          <P>
            Profit locked in when contracts are sold:
          </P>
          <Formula>PnL_realized = (sell_price − avg_entry) × shares_sold</Formula>
          <P>
            The cost basis is reduced by <Code>avg_entry × shares_sold</Code> (not by the sell
            price) so that the <Code>avg_price</Code> of remaining contracts is unchanged.
          </P>
          <Pre label="Example: partial sell">{`Position: 20 YES @ avg_entry 0.40

SELL 8 YES @ 0.55:
  realized = (0.55 - 0.40) × 8 = +$1.20
  total_cost -= 0.40 × 8  (not 0.55 × 8 — avg_entry preserved for remainder)

Remaining: 12 YES @ avg_entry 0.40 (unchanged)`}</Pre>

          <H3>Total Portfolio P&L</H3>
          <Formula>
            PnL_total = Σ (realized_pnl + unrealized_pnl) across all markets
          </Formula>

          <H3>P&L Time Series (Dashboard Chart)</H3>
          <P>
            The P&L chart replays <Code>betting_orders</Code> chronologically. At each trade
            event:
          </P>
          <Ol>
            <Li>
              For SELL orders: cumulative realized P&L is updated with{" "}
              <Code>(sell_price − avg_entry) × qty</Code>
            </Li>
            <Li>
              Unrealized P&L is estimated using the market price at the time of that trade
              (from <Code>market_price_snapshots</Code>)
            </Li>
            <Li>
              The final data point is corrected to match the authoritative totals in{" "}
              <Code>TradingPosition</Code> to prevent drift between the replay and the
              position-based calculation
            </Li>
          </Ol>

          <H3>P&L Attribution by Model</H3>
          <P>
            P&L is attributed to the model that drove a given trade via the{" "}
            <Code>BettingPrediction.source</Code> field. Each order is linked:
          </P>
          <Pre>{`BettingOrder → BettingSignal → BettingPrediction.source (model name)`}</Pre>
          <P>
            All P&L for markets where a given model placed the orders is attributed to that
            model. There is no fractional attribution — one model owns each trade completely.
          </P>

          <H3>Brier Score</H3>
          <P>
            The Brier score measures the accuracy of probability forecasts for resolved markets:
          </P>
          <Formula>Brier = (1/N) × Σ (P_predicted − outcome)²</Formula>
          <P>
            Where <Code>outcome</Code> is 1 if YES resolved, 0 if NO resolved. Lower is better.
            Perfect score = 0.0. Random (always predicting 0.5) = 0.25.
          </P>
          <P>
            The dashboard shows two Brier scores side by side:
          </P>
          <Table
            headers={["Metric", "Prediction used", "Interpretation"]}
            rows={[
              ["Model Brier Score", "p_yes from the model", "How accurate the model's probability estimates are"],
              ["Market Baseline Brier", "yes_ask at time of prediction", "How accurate the market price alone would have been. If model score < baseline, the model adds value over just following the market."],
            ]}
          />

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="risk">9. Risk & Capital Allocation</H2>

          <H3>Risk Metrics (Dashboard)</H3>
          <Table
            headers={["Metric", "Formula", "What it measures"]}
            rows={[
              ["Sharpe Ratio", "(mean_return − 0) / std_return", "Risk-adjusted return. Higher = better return per unit of total volatility."],
              ["Sortino Ratio", "mean_return / downside_std", "Like Sharpe but only penalises losing trades. Better for asymmetric strategies."],
              ["Max Drawdown", "min(PnL_t − max(PnL_0..t))", "Largest peak-to-trough decline in cumulative P&L. Key capital preservation metric."],
              ["Win Rate", "winning_markets / total_markets", "Fraction of markets with positive total P&L (realized + unrealized)."],
              ["Profit Factor", "gross_profit / gross_loss", "Ratio of total wins to total losses. > 1.0 means the system earns more than it loses."],
              ["Win/Loss Ratio", "avg_win / avg_loss", "How much the average win is relative to the average loss."],
            ]}
          />

          <H3>Capital Allocation</H3>
          <P>
            There is no explicit per-market capital limit. Allocation is implicitly controlled
            by the edge magnitude and available cash:
          </P>
          <Ul>
            <Li>
              <strong>Edge-proportional sizing:</strong> A larger edge produces more contracts
              (e.g., 25pp edge → 25 contracts). This naturally concentrates capital in
              high-conviction markets.
            </Li>
            <Li>
              <strong>Cash floor:</strong> The engine hard-rejects any BUY that exceeds available
              cash. The system cannot go into debt.
            </Li>
            <Li>
              <strong>Spread filter:</strong> Illiquid markets (spread &gt; 1.03) are never
              traded, protecting capital from high-friction markets.
            </Li>
            <Li>
              <strong>Per-cycle market cap:</strong> <Code>MAX_MARKETS_PER_TICK=10</Code> limits
              simultaneous positions per cycle. If more than 10 signals fire in one cycle, the
              top 10 by edge magnitude are kept and the rest are dropped.
            </Li>
          </Ul>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="activity">10. Activity Logging</H2>

          <H3>System Logs (<Code>system_logs</Code>)</H3>
          <P>
            The worker writes structured events to the <Code>system_logs</Code> table throughout
            each cycle. These power the &quot;System Activity&quot; feed on the dashboard.
          </P>
          <Table
            headers={["Level", "When emitted"]}
            rows={[
              [<Code key="h">HEARTBEAT</Code>, "Start and end of every trading cycle (with cycle summary stats)"],
              [<Code key="i">INFO</Code>, "Market fetch completions, position updates, cycle summaries"],
              [<Code key="w">WARNING</Code>, "Non-critical failures: market fetch errors, empty cycles, model retries"],
              [<Code key="e">ERROR</Code>, "Order rejections, database errors, model inference failures (after retries)"],
              [<Code key="a">ALERT</Code>, "High-edge opportunities detected (threshold configurable)"],
            ]}
          />

          <H3>Model Run Logging (<Code>model_runs</Code>)</H3>
          <P>
            Every model evaluation writes a row to <Code>model_runs</Code> with:
          </P>
          <Pre label="model_runs fields">{`model_name  — e.g. "gemini:gemini-3.1-pro-preview"
market_id   — "kalshi:{ticker}"
decision    — "BUY_YES" | "BUY_NO" | "HOLD"
confidence  — Model's self-reported confidence [0.0, 1.0]
timestamp   — When the prediction was made
metadata    — Full JSON: { p_yes, yes_ask, no_ask, reasoning, analysis, ... }`}</Pre>

          <H3>Price Snapshots (<Code>market_price_snapshots</Code>)</H3>
          <P>
            After each model prediction, a price snapshot is written containing the market
            prices and model probability at that moment. These power the time-series charts
            showing model probability vs market price over time.
          </P>

          <H3>Order Audit Trail (<Code>betting_orders</Code>)</H3>
          <P>
            Every order — whether filled, rejected, or dry-run — is persisted with its full
            context. This table is the canonical audit trail and the source for position
            reconstruction in <Code>update_positions()</Code>.
          </P>
          <Pre label="betting_orders key fields">{`order_id          — Internal UUID
exchange_order_id — Kalshi order ID (null for dry-run)
ticker            — Market ticker
side              — "yes" or "no"
action            — "BUY" or "SELL"
count             — Number of contracts ordered
price_cents       — Limit price in cents (1–99)
fill_price        — Actual fill price as fraction (0.01–0.99)
filled_shares     — Number of contracts actually filled
status            — "FILLED" | "DRY_RUN" | "PENDING" | "REJECTED" | "ERROR"
dry_run           — true if simulated
created_at        — Order submission timestamp`}</Pre>

          <Divider />

          {/* ══════════════════════════════════════════════════════════ */}
          <H2 id="config">11. Configuration Reference</H2>

          <Pre label="Environment variables">{`# ── Core ──────────────────────────────────────────────────────
DATABASE_URL=postgresql://user:pass@host:5432/dbname
LIVE_BETTING_ENABLED=true          # Master kill switch (default: false)
LIVE_BETTING_DRY_RUN=true          # Simulate orders, no real money (default: true)

# ── Kalshi API credentials ─────────────────────────────────────
KALSHI_API_KEY_ID=...              # API key ID from Kalshi dashboard
KALSHI_PRIVATE_KEY_B64=...         # Base64-encoded RSA private key
KALSHI_BASE_URL=https://api.elections.kalshi.com

# ── Worker ────────────────────────────────────────────────────
WORKER_POLL_INTERVAL_SEC=900       # Seconds between trading cycles (default: 15 min)
WORKER_MODELS=gemini:gemini-3.1-pro-preview   # Model to use for predictions
WORKER_STRATEGY=default            # Strategy: "default" (edge-proportional)
WORKER_MAX_MARKETS=25              # Max new markets to rank and select per cycle
WORKER_MAX_ACTIVE_MARKETS=40       # Max total markets (new + sticky positions)

# ── Model provider API keys ────────────────────────────────────
GOOGLE_API_KEY=...                 # Gemini models
OPENAI_API_KEY=...                 # OpenAI models
ANTHROPIC_API_KEY=...              # Anthropic models`}</Pre>

          <H3>Strategy Constants (code-level)</H3>
          <Table
            headers={["Constant", "File", "Default", "Effect"]}
            rows={[
              [<Code key="ms">MAX_SPREAD</Code>, "betting/config.py", "1.03", "Markets with yes_ask + no_ask > 1.03 are skipped entirely."],
              [<Code key="mm">MAX_MARKETS_PER_TICK</Code>, "betting/config.py", "10", "Maximum number of trade signals executed in one cycle. Top by edge if exceeded."],
              [<Code key="mt">min_trade</Code>, "strategy.py (Rebalancing)", "0.005", "Minimum fractional position delta to place an order. Prevents micro-trades."],
            ]}
          />

          <div className="mt-14 mb-8 border-t border-t-border pt-6 flex items-center justify-between">
            <span className="text-[10px] text-txt-muted">
              Prophet Arena Internal Documentation — confidential
            </span>
            <span className="text-[10px] text-txt-muted">
              Last updated:{" "}
              {new Date().toLocaleDateString("en-US", {
                year: "numeric",
                month: "long",
                day: "numeric",
              })}
            </span>
          </div>
        </article>
      </div>
    </main>
  );
}
