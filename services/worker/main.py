"""Standalone trading worker — polls Kalshi markets, uses LLM for predictions.

Operates directly against the Kalshi API without requiring the Prophet Arena
server.  Uses an LLM (OpenAI/Anthropic) to analyze markets and produce
probability estimates, then feeds them into the existing BettingEngine.

Usage:
    python services/worker/main.py
    python services/worker/main.py --dry-run     # force dry-run regardless of env
    python services/worker/main.py --once         # run one cycle then exit
    python services/worker/main.py -v             # verbose logging

Environment variables:
    DATABASE_URL              — PostgreSQL connection string (required)
    KALSHI_API_KEY_ID         — Kalshi API key ID
    KALSHI_PRIVATE_KEY_B64    — Base64-encoded RSA private key
    KALSHI_BASE_URL           — Kalshi API base URL
    LIVE_BETTING_ENABLED      — Master kill switch (default: false)
    LIVE_BETTING_DRY_RUN      — Dry-run mode (default: true)
    WORKER_POLL_INTERVAL_SEC  — Seconds between cycles (default: 900)
    WORKER_MODELS             — Comma-separated model specs (default: gemini:gemini-3.1-pro-preview)
                                 Providers: openai, anthropic, gemini
                                 Examples: gemini:gemini-3.1-pro-preview, anthropic:claude-sonnet-4-5-20250929
    GOOGLE_API_KEY            — Google AI API key (for gemini provider)
    WORKER_STRATEGY           — Betting strategy: default|rebalancing (default: default)
    WORKER_MAX_MARKETS        — Max NEW markets to fetch per cycle (default: 25)
    WORKER_MAX_ACTIVE_MARKETS — Max total active markets (sticky + new) (default: 40)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv()

logger = logging.getLogger("worker")

# ── Shutdown handling ──────────────────────────────────────────────

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Logging setup ─────────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)


# ── DB helpers ────────────────────────────────────────────────────

def log_heartbeat(db_engine, component: str = "worker", message: str = "alive") -> None:
    """Write a heartbeat row to system_logs."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                level="HEARTBEAT",
                message=message,
                component=component,
                created_at=datetime.now(UTC),
            ))
    except Exception as e:
        logger.warning("Failed to write heartbeat: %s", e)


def log_system_event(db_engine, level: str, message: str, component: str = "worker") -> None:
    """Write a system event to system_logs."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                level=level,
                message=message[:2000],
                component=component,
                created_at=datetime.now(UTC),
            ))
    except Exception:
        pass


def save_price_snapshot(db_engine, market_id: str, ticker: str,
                        yes_ask: float, no_ask: float,
                        volume_24h: float = 0,
                        model_p_yes: float | None = None,
                        model_name: str | None = None) -> None:
    """Record a point-in-time price snapshot for time-series analysis."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import MarketPriceSnapshot

        with get_session(db_engine) as session:
            session.add(MarketPriceSnapshot(
                market_id=market_id,
                ticker=ticker,
                yes_ask=yes_ask,
                no_ask=no_ask,
                volume_24h=volume_24h,
                model_p_yes=model_p_yes,
                model_name=model_name,
                timestamp=datetime.now(UTC),
            ))
    except Exception as e:
        logger.warning("Failed to save price snapshot: %s", e)


def save_market_snapshot(db_engine, market_id: str, title: str, category: str,
                         yes_ask: float, no_ask: float | None = None,
                         expiration=None, ticker: str = "",
                         event_ticker: str = "", volume_24h: float = 0) -> None:
    """Upsert a market snapshot into trading_markets."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        now = datetime.now(UTC)
        with get_session(db_engine) as session:
            existing = session.query(TradingMarket).filter_by(market_id=market_id).first()
            if existing:
                existing.title = title
                existing.category = category
                existing.last_price = yes_ask
                existing.yes_ask = yes_ask
                existing.no_ask = no_ask
                existing.ticker = ticker
                existing.event_ticker = event_ticker
                existing.volume_24h = volume_24h
                existing.expiration = expiration
                existing.updated_at = now
            else:
                session.add(TradingMarket(
                    market_id=market_id,
                    ticker=ticker,
                    event_ticker=event_ticker,
                    title=title,
                    category=category or "unknown",
                    last_price=yes_ask,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    volume_24h=volume_24h,
                    expiration=expiration,
                    updated_at=now,
                ))
    except Exception as e:
        logger.warning("Failed to save market snapshot: %s", e)


def save_model_run(db_engine, model_name: str, market_id: str,
                   decision: str, confidence: float | None,
                   metadata: dict | None = None) -> None:
    """Log a model decision to model_runs."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import ModelRun

        with get_session(db_engine) as session:
            session.add(ModelRun(
                model_name=model_name,
                timestamp=datetime.now(UTC),
                decision=decision,
                confidence=confidence,
                market_id=market_id,
                metadata_json=json.dumps(metadata) if metadata else None,
            ))
    except Exception as e:
        logger.warning("Failed to save model run: %s", e)


def update_positions(db_engine, market_prices: dict[str, tuple[float, float]]) -> None:
    """Aggregate betting_orders into trading_positions for the dashboard.

    For each ticker with filled/dry-run orders, computes net position
    (side, quantity, avg_price) and unrealized P&L based on current market price.
    """
    if db_engine is None:
        return
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from db_models import TradingPosition

        now = datetime.now(UTC)

        with get_session(db_engine) as session:
            # Get all filled/dry-run orders grouped by ticker
            orders = (
                session.query(BettingOrder)
                .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
                .order_by(BettingOrder.created_at.asc())
                .all()
            )

            # Aggregate by ticker: track buys, sells, and realized PnL
            positions: dict[str, dict] = {}
            for order in orders:
                ticker = order.ticker
                if ticker not in positions:
                    positions[ticker] = {
                        "yes_shares": 0.0,
                        "total_cost": 0.0,
                        "realized_pnl": 0.0,
                        "realized_trades": 0,
                        "max_position": 0.0,
                    }

                shares = order.filled_shares if order.filled_shares > 0 else float(order.count)
                price = order.fill_price if order.fill_price > 0 else order.price_cents / 100.0
                # Fix corrupted fill_price stored as cents (e.g. 17.0 instead of 0.17)
                if price > 1.0:
                    price = price / 100.0

                # Determine if this is a SELL order (action column may not
                # exist on old rows, so handle gracefully)
                try:
                    action = getattr(order, "action", "BUY") or "BUY"
                except AttributeError:
                    action = "BUY"

                pos = positions[ticker]

                if action.upper() == "SELL":
                    # Compute realized PnL from the sell
                    current_qty = abs(pos["yes_shares"])
                    if current_qty > 0:
                        avg_entry = abs(pos["total_cost"] / pos["yes_shares"]) if pos["yes_shares"] != 0 else 0
                        realized = (price - avg_entry) * shares
                        pos["realized_pnl"] += realized
                        pos["realized_trades"] += 1

                        # Reduce cost basis by avg_entry (not sell price)
                        # to keep avg_price accurate for remaining shares
                        if order.side.lower() == "yes":
                            pos["yes_shares"] -= shares
                            pos["total_cost"] -= avg_entry * shares
                        else:
                            pos["yes_shares"] += shares
                            pos["total_cost"] += avg_entry * shares
                    else:
                        # No position to sell — ignore to prevent negative shares
                        pass
                else:
                    # BUY order
                    if order.side.lower() == "yes":
                        pos["yes_shares"] += shares
                        pos["total_cost"] += shares * price
                    else:  # no
                        pos["yes_shares"] -= shares
                        pos["total_cost"] -= shares * price

                # When position is flat, reset cost basis to prevent
                # residuals from leaking across side flips
                if abs(pos["yes_shares"]) < 0.001:
                    pos["total_cost"] = 0.0
                    pos["yes_shares"] = 0.0

                # Track high-water mark for position size
                current_abs = abs(pos["yes_shares"])
                if current_abs > pos["max_position"]:
                    pos["max_position"] = current_abs

            # Upsert into trading_positions
            for ticker, pos in positions.items():
                net_shares = pos["yes_shares"]
                if abs(net_shares) < 0.001:
                    # No position — remove if exists
                    session.query(TradingPosition).filter_by(
                        market_id=f"kalshi:{ticker}"
                    ).delete()
                    continue

                side = "yes" if net_shares > 0 else "no"
                qty = abs(net_shares)
                avg_price = abs(pos["total_cost"] / net_shares) if net_shares != 0 else 0

                # Unrealized P&L: (current_price - avg_price) * quantity
                market_id = f"kalshi:{ticker}"
                unrealized = 0.0
                if market_id in market_prices:
                    yes_ask, no_ask = market_prices[market_id]
                    current = yes_ask if side == "yes" else no_ask
                    unrealized = (current - avg_price) * qty

                existing = session.query(TradingPosition).filter_by(
                    market_id=market_id
                ).first()
                if existing:
                    existing.contract = side
                    existing.quantity = qty
                    existing.avg_price = round(avg_price, 4)
                    existing.realized_pnl = round(pos["realized_pnl"], 4)
                    existing.unrealized_pnl = round(unrealized, 4)
                    existing.max_position = round(pos["max_position"], 4)
                    existing.realized_trades = pos["realized_trades"]
                    existing.updated_at = now
                else:
                    session.add(TradingPosition(
                        market_id=market_id,
                        contract=side,
                        quantity=qty,
                        avg_price=round(avg_price, 4),
                        realized_pnl=round(pos["realized_pnl"], 4),
                        unrealized_pnl=round(unrealized, 4),
                        max_position=round(pos["max_position"], 4),
                        realized_trades=pos["realized_trades"],
                        updated_at=now,
                    ))

        logger.info("Updated %d positions from order history", len(positions))
    except Exception as e:
        logger.warning("Failed to update positions: %s", e)


# ── Sticky market tracking ────────────────────────────────────────

def get_traded_tickers(db_engine) -> set[str]:
    """Return tickers with orders placed in the last 30 days (still relevant)."""
    if db_engine is None:
        return set()
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from sqlalchemy import distinct

        cutoff = datetime.now(UTC) - timedelta(days=30)
        with get_session(db_engine) as session:
            rows = (
                session.query(distinct(BettingOrder.ticker))
                .filter(BettingOrder.created_at >= cutoff)
                .all()
            )
            return {r[0] for r in rows}
    except Exception as e:
        logger.warning("Failed to query traded tickers: %s", e)
        return set()


def get_tracked_tickers(db_engine) -> set[str]:
    """Return all tickers currently in the trading_markets table."""
    if db_engine is None:
        return set()
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        with get_session(db_engine) as session:
            rows = session.query(TradingMarket.ticker).all()
            return {r[0] for r in rows if r[0]}
    except Exception as e:
        logger.warning("Failed to query tracked tickers: %s", e)
        return set()


def fetch_market_by_ticker(adapter, ticker: str) -> dict | None:
    """Fetch a single market by ticker, then its parent event for clean title/category."""
    base_url = adapter._base_url

    # 1. Fetch market to get pricing + event_ticker
    path = f"/trade-api/v2/markets/{ticker}"
    headers = adapter._sign_request("GET", path)

    try:
        response = adapter._session.get(
            base_url + path,
            headers=headers,
            timeout=adapter._timeout,
        )
        response.raise_for_status()
        mkt = response.json().get("market", {})

        status = mkt.get("status", "")
        if status not in ("open", "active"):
            return None

        yes_ask = mkt.get("yes_ask_dollars")
        no_ask = mkt.get("no_ask_dollars")
        last_price = mkt.get("last_price_dollars")
        if yes_ask is None and last_price is None:
            return None

        # 2. Fetch parent event for clean title + category
        event_ticker = mkt.get("event_ticker", "")
        event_title = ""
        category = ""
        if event_ticker:
            try:
                ev_path = f"/trade-api/v2/events/{event_ticker}"
                ev_headers = adapter._sign_request("GET", ev_path)
                ev_resp = adapter._session.get(
                    base_url + ev_path,
                    headers=ev_headers,
                    timeout=adapter._timeout,
                )
                ev_resp.raise_for_status()
                event = ev_resp.json().get("event", {})
                event_title = event.get("title", "")
                category = event.get("category", "")
            except Exception as e:
                logger.debug("Failed to fetch event %s: %s", event_ticker, e)

        # Build clean title same way as fetch_kalshi_markets
        if not event_title:
            event_title = mkt.get("title", ticker)
        yes_sub = mkt.get("yes_sub_title", "")
        title = f"{event_title}: {yes_sub}" if yes_sub else event_title

        return {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "title": title,
            "subtitle": mkt.get("rules_primary", ""),
            "category": category,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "last_price": last_price,
            "close_time": mkt.get("close_time"),
            "volume_24h": mkt.get("volume_24h_fp", 0),
        }
    except Exception as e:
        logger.warning("Failed to fetch market %s: %s", ticker, e)
        return None


# ── Kalshi market fetcher ─────────────────────────────────────────

def fetch_kalshi_markets(adapter, max_markets: int = 10, max_pages: int = 10) -> list[dict]:
    """Fetch active binary markets from Kalshi via the events endpoint.

    Uses /trade-api/v2/events with nested markets.  Paginates through all
    pages, collects candidates closing within 30 days, then returns the top
    ``max_markets`` ranked by volume (with a soft bonus for prices near 50%).
    """
    base_url = adapter._base_url
    path = "/trade-api/v2/events"
    cutoff = datetime.now(UTC) + timedelta(days=30)

    candidates: list[dict] = []
    cursor = ""
    total_events = 0

    for page in range(max_pages):
        headers = adapter._sign_request("GET", path)
        params = {
            "limit": 200,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor

        try:
            response = adapter._session.get(
                base_url + path,
                headers=headers,
                params=params,
                timeout=adapter._timeout,
            )
            response.raise_for_status()
            data = response.json()
            events = data.get("events", [])
            cursor = data.get("cursor", "")
            total_events += len(events)
        except Exception as e:
            logger.error("Failed to fetch Kalshi events (page %d): %s", page + 1, e)
            break

        if not events:
            break

        for event in events:
            event_title = event.get("title", "Unknown")
            category = event.get("category", "")

            for mkt in event.get("markets", []):
                status = mkt.get("status", "")
                if status not in ("open", "active"):
                    continue

                # Only trade markets closing within 30 days
                close_time_str = mkt.get("close_time")
                if close_time_str:
                    try:
                        close_dt = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                        if close_dt > cutoff:
                            continue
                    except (ValueError, AttributeError):
                        pass

                ticker = mkt.get("ticker", "")
                yes_ask = mkt.get("yes_ask_dollars")
                no_ask = mkt.get("no_ask_dollars")
                last_price = mkt.get("last_price_dollars")

                if yes_ask is None and last_price is None:
                    continue

                price = float(yes_ask) if yes_ask is not None else float(last_price)

                yes_sub = mkt.get("yes_sub_title", "")
                market_title = f"{event_title}: {yes_sub}" if yes_sub else event_title

                volume = float(mkt.get("volume_24h_fp", 0) or 0)

                # Apply spread filter early — skip illiquid markets
                _ya = float(yes_ask) if yes_ask is not None else price
                _na = float(no_ask) if no_ask is not None else (1.0 - price)
                if _ya + _na > 1.03:
                    continue

                # Rank by volume with a small soft bonus for prices near 50%
                proximity_bonus = 1.0 - 2.0 * abs(price - 0.5)
                candidates.append({
                    "ticker": ticker,
                    "event_ticker": mkt.get("event_ticker", ""),
                    "title": market_title,
                    "subtitle": mkt.get("rules_primary", ""),
                    "category": category,
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                    "last_price": last_price,
                    "close_time": close_time_str,
                    "volume_24h": volume,
                    "_score": volume + proximity_bonus,
                })

        if not cursor:
            break

        logger.debug("Page %d: %d candidates so far, fetching more...", page + 1, len(candidates))

    # Rank: prefer higher volume, soft bonus for prices near 50%
    candidates.sort(key=lambda m: m["_score"], reverse=True)

    # Deduplicate by event_ticker — keep only the highest-scoring market per event
    seen_events: set[str] = set()
    deduped: list[dict] = []
    for m in candidates:
        ev = m.get("event_ticker", "") or m.get("ticker", "")
        if ev and ev in seen_events:
            continue
        seen_events.add(ev)
        deduped.append(m)

    markets = deduped[:max_markets]

    # Clean up internal scoring field
    for m in markets:
        m.pop("_score", None)

    logger.info(
        "Selected %d markets (from %d candidates, %d events, %d pages)",
        len(markets), len(candidates), total_events, page + 1,
    )
    return markets


# ── LLM prediction ───────────────────────────────────────────────

def create_llm_predictor(model_spec: str):
    """Create a function that uses an LLM to predict market probabilities.

    Args:
        model_spec: Format: "provider:model_name" or "provider:model_name:market"
            The optional ":market" suffix includes market prices in the prompt.
            e.g. "gemini:gemini-3.1-pro-preview:market" → with market data
                 "gemini:gemini-3.1-pro-preview" → without market data

    Returns:
        A callable(market_info) -> dict with keys: p_yes, confidence, reasoning
    """
    parts = model_spec.split(":")
    if len(parts) >= 3:
        provider = parts[0].lower()
        model_name = parts[1]
        include_market = parts[2].lower() in ("market", "mkt", "prices")
    elif len(parts) == 2:
        provider = parts[0].lower()
        model_name = parts[1]
        include_market = False
    else:
        provider, model_name = "openai", parts[0]
        include_market = False

    if provider == "openai":
        return _openai_predictor(model_name, include_market)
    elif provider in ("anthropic", "claude"):
        return _anthropic_predictor(model_name, include_market)
    elif provider in ("gemini", "google"):
        return _gemini_predictor(model_name, include_market)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def _build_prompts(market_info: dict, include_market_prices: bool = False) -> tuple[str, str]:
    """Build system and user prompts matching the ProphetArena AgentPrompts format.

    Args:
        market_info: Market data dict with title, yes_ask, no_ask, etc.
        include_market_prices: If True, include YES/NO ask prices in the prompt.

    Returns:
        (system_prompt, user_prompt)
    """
    title = market_info.get("title", "")

    system = f"""You are an AI assistant specialized in analyzing and predicting real-world events.
You have deep expertise in predicting the outcome of the event: "{title}"

Note that this event occurs in the future. Your goal is to provide well-reasoned predictions.
You will be predicting the probability (as a float value from 0 to 1) of ONLY the following possible outcome:
- {title}

IMPORTANT CONSTRAINTS:
1. You MUST ONLY provide a probability for the exact outcome listed above
2. Ensure your probability is between 0 and 1

Your response MUST be in JSON format with the following structure:
```json
{{
    "rationale": "<short_concise_3_sentence_rationale>",
    "probabilities": {{
        "{title}": <probability_value_from_0_to_1>
    }}
}}
```

In the rationale, provide a short, concise, 3 sentence rationale that explains:
- How you weighed different pieces of information
- Your reasoning for the probability you assigned
- Any key factors or uncertainties you considered"""

    if include_market_prices:
        yes_ask = market_info.get("yes_ask", 0.5)
        no_ask = market_info.get("no_ask", 0.5)
        market_stats = json.dumps({"YES": yes_ask, "NO": no_ask}, indent=2)
        user = f"""CURRENT ONLINE TRADING DATA:
You also have access to the predicted outcome probability from a prediction market:
{market_stats}

Note: Market data can provide insights into the current consensus influenced by traders of various beliefs and private information. However, you should not rely on market data alone.

Please analyze the event and provide your prediction following the specified format."""
    else:
        user = f"""Please analyze the event "{title}" and provide your prediction following the specified format.

Use your knowledge and any available information to form an independent probability estimate."""

    return system, user


def _parse_prediction(content: str) -> dict:
    """Extract prediction JSON from LLM response."""
    # Strip markdown code fences if present
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        result = json.loads(text[start:end])
    else:
        result = json.loads(text)

    # Extract p_yes — ProphetArena format uses "probabilities" dict
    p_yes = 0.5
    probs = result.get("probabilities", {})
    if probs:
        # Get the first (and only) value from the probabilities dict
        p_yes = float(next(iter(probs.values())))
    elif "p_yes" in result:
        p_yes = float(result["p_yes"])

    return {
        "p_yes": p_yes,
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": result.get("rationale", result.get("reasoning", "")),
        "analysis": result.get("analysis", {}),
    }


def _openai_predictor(model_name: str, include_market: bool = False):
    """Return a predictor function using OpenAI."""
    import openai
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def predict(market_info: dict) -> dict:
        system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)
        try:
            response = client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
                max_tokens=800,
                response_format={"type": "json_object"},
            )
            return _parse_prediction(response.choices[0].message.content)
        except Exception as e:
            logger.error("OpenAI prediction failed: %s", e)
            return {"p_yes": 0.5, "confidence": 0.0, "reasoning": f"Error: {e}"}

    return predict


def _anthropic_predictor(model_name: str, include_market: bool = False):
    """Return a predictor function using Anthropic."""
    import anthropic
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    def predict(market_info: dict) -> dict:
        system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)
        try:
            response = client.messages.create(
                model=model_name,
                max_tokens=800,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )
            return _parse_prediction(response.content[0].text)
        except Exception as e:
            logger.error("Anthropic prediction failed: %s", e)
            return {"p_yes": 0.5, "confidence": 0.0, "reasoning": f"Error: {e}"}

    return predict


def _gemini_predictor(model_name: str, include_market: bool = False):
    """Return a predictor function using Gemini REST API.

    Usage: gemini:gemini-2.0-flash, gemini:gemini-3-flash-preview, etc.
    """
    import httpx

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY or GEMINI_API_KEY env var required for Gemini")
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    http_client = httpx.Client(timeout=120.0)

    def predict(market_info: dict) -> dict:
        system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)

        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"responseMimeType": "application/json"},
            "tools": [{"googleSearch": {}}],
        }

        # Gemini 3+ models get thinking config
        if "gemini-3" in model_name:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "high"}

        url = f"{base_url}/models/{model_name}:generateContent?key={api_key}"

        try:
            t0 = time.time()
            response = http_client.post(url, json=body)
            elapsed = time.time() - t0
            response.raise_for_status()
            data = response.json()

            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError(f"Gemini returned no candidates: {data}")

            parts = candidates[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            logger.info("Gemini API call took %.1fs", elapsed)
            return _parse_prediction(text)
        except Exception as e:
            logger.error("Gemini prediction failed (%.1fs): %s", time.time() - t0, e)
            return {"p_yes": 0.5, "confidence": 0.0, "reasoning": f"Error: {e}"}

    return predict


# ── Remote prediction (Cloud Run service) ─────────────────────────

PREDICTOR_SERVICE_URL = os.getenv("PREDICTOR_SERVICE_URL", "").rstrip("/")
PREDICTOR_API_KEY = os.getenv("PREDICTOR_API_KEY", "")


def _remote_predict(model_spec: str, market_info: dict) -> dict:
    """Call the remote predictor service for a single (model, market) pair."""
    import requests

    resp = requests.post(
        f"{PREDICTOR_SERVICE_URL}/predict",
        json={"model_spec": model_spec, "market_info": market_info},
        headers={"X-API-Key": PREDICTOR_API_KEY} if PREDICTOR_API_KEY else {},
        timeout=130,
    )
    resp.raise_for_status()
    return resp.json()


def _remote_predict_with_retry(model_spec: str, market_info: dict,
                                max_retries: int = 2) -> dict:
    """Call remote predictor with retries on failure."""
    for attempt in range(max_retries + 1):
        try:
            return _remote_predict(model_spec, market_info)
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    "  [%s] remote attempt %d failed, retrying in 5s: %s",
                    model_spec, attempt + 1, e,
                )
                time.sleep(5)
            else:
                raise


# ── Betting engine factory ────────────────────────────────────────

def build_betting_engine(strategy_name: str = "default", dry_run_override: bool | None = None):
    """Create BettingEngine reusing the existing core module."""
    from ai_prophet_core.betting import BettingEngine, LiveBettingSettings
    from ai_prophet_core.betting.db import create_db_engine

    settings = LiveBettingSettings.from_env()

    if not settings.enabled:
        logger.warning("Betting engine DISABLED (LIVE_BETTING_ENABLED != true)")
        return None, None

    dry_run = dry_run_override if dry_run_override is not None else settings.dry_run

    db_engine = create_db_engine()

    if strategy_name == "rebalancing":
        from ai_prophet_core.betting import RebalancingStrategy
        strategy = RebalancingStrategy()
    else:
        from ai_prophet_core.betting import DefaultBettingStrategy
        strategy = DefaultBettingStrategy()

    engine = BettingEngine(
        strategy=strategy,
        db_engine=db_engine,
        dry_run=dry_run,
        kalshi_config=settings.kalshi,
        enabled=settings.enabled,
    )
    logger.info(
        "BettingEngine ready: strategy=%s, dry_run=%s",
        engine.strategy.name, dry_run,
    )
    return engine, db_engine


# ── Main trading cycle ────────────────────────────────────────────

def run_cycle(args) -> None:
    """Run one trading cycle: fetch markets → LLM predict → BettingEngine."""
    strategy_name = os.getenv("WORKER_STRATEGY", "default")
    dry_run_override = True if args.dry_run else None
    max_markets = int(os.getenv("WORKER_MAX_MARKETS", "25"))
    max_active = int(os.getenv("WORKER_MAX_ACTIVE_MARKETS", "40"))
    models_str = os.getenv("WORKER_MODELS", "gemini:gemini-3.1-pro-preview")
    model_specs = [m.strip() for m in models_str.split(",") if m.strip()]

    # Build engine
    betting_engine, db_engine = build_betting_engine(
        strategy_name=strategy_name,
        dry_run_override=dry_run_override,
    )

    if db_engine is not None:
        log_heartbeat(db_engine, message="cycle_start")
        from ai_prophet_core.betting.db_schema import Base as CoreBase
        CoreBase.metadata.create_all(db_engine, checkfirst=True)

    if betting_engine is None:
        logger.error("Betting engine not available, skipping cycle")
        return

    # Get the Kalshi adapter from the engine to reuse auth
    adapter = betting_engine._get_adapter()

    logger.info(
        "Starting cycle: models=%s, strategy=%s, max_markets=%d, max_active=%d",
        model_specs, strategy_name, max_markets, max_active,
    )

    # 1. Gather sticky markets (already tracked in DB)
    tracked_tickers = get_tracked_tickers(db_engine) | get_traded_tickers(db_engine)
    sticky_markets: list[dict] = []

    if tracked_tickers:
        logger.info("Re-fetching %d sticky markets: %s", len(tracked_tickers), tracked_tickers)
        for ticker in tracked_tickers:
            mkt = fetch_market_by_ticker(adapter, ticker)
            if mkt:
                sticky_markets.append(mkt)
            else:
                logger.info("  Sticky market %s no longer active, skipping", ticker)

    # 2. Fetch NEW markets from Kalshi, excluding already-tracked ones
    new_slots = max(0, max_active - len(sticky_markets))
    if new_slots > 0:
        # Fetch more candidates than needed so we have enough after excluding sticky
        all_new = fetch_kalshi_markets(adapter, max_markets=max_markets + len(tracked_tickers))
        # Filter out already-tracked tickers
        new_markets = [m for m in all_new if m["ticker"] not in tracked_tickers]
        new_markets = new_markets[:new_slots]
        logger.info(
            "Fetched %d new markets (%d candidates, %d excluded as already tracked)",
            len(new_markets), len(all_new), len(all_new) - len(new_markets),
        )
    else:
        new_markets = []
        logger.info("At max active markets (%d), not fetching new ones", max_active)

    # 3. Combine: sticky first, then new
    raw_markets = sticky_markets + new_markets
    logger.info("Total markets this cycle: %d sticky + %d new = %d",
                len(sticky_markets), len(new_markets), len(raw_markets))

    if not raw_markets:
        logger.warning("No markets fetched, skipping cycle")
        if db_engine:
            log_system_event(db_engine, "WARNING", "No markets fetched from Kalshi")
        if betting_engine:
            betting_engine.close()
        return

    # Collect all market prices across models for position updates
    all_market_prices: dict[str, tuple[float, float]] = {}
    # Track (market_id, model, edge) for alert checking
    all_edges: list[tuple[str, str, float]] = []

    # ── Phase A: Pre-filter markets (sequential) ────────────────────
    # Validate prices, apply spread filter, save snapshots, skip unchanged.
    # Build a list of markets that need LLM analysis.
    tick_ts = datetime.now(UTC)
    total_results = []

    from ai_prophet_core.betting.config import MAX_SPREAD

    markets_to_analyze: list[dict] = []  # enriched market dicts

    for market in raw_markets:
        if _shutdown_requested:
            logger.info("Shutdown requested, stopping analysis")
            break

        ticker = market.get("ticker", "")
        title = market.get("title", "Unknown")
        subtitle = market.get("subtitle", "")
        category = market.get("category", "")

        yes_ask = market.get("yes_ask")
        no_ask = market.get("no_ask")

        if yes_ask is None or no_ask is None:
            last_price = market.get("last_price")
            if last_price is not None:
                yes_ask = float(last_price)
                no_ask = 1.0 - yes_ask
            else:
                logger.warning("Skipping %s: no pricing data", ticker)
                continue

        yes_ask = float(yes_ask)
        no_ask = float(no_ask)

        if yes_ask + no_ask > MAX_SPREAD:
            logger.debug("Skipping %s: spread %.3f > %.3f", ticker, yes_ask + no_ask, MAX_SPREAD)
            continue

        market_id = f"kalshi:{ticker}"

        # Save market snapshot for dashboard
        if db_engine:
            expiration = None
            exp_str = market.get("close_time")
            if exp_str:
                try:
                    expiration = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass
            save_market_snapshot(
                db_engine, market_id, title, category,
                yes_ask=yes_ask, no_ask=no_ask,
                expiration=expiration, ticker=ticker,
                event_ticker=market.get("event_ticker", ""),
                volume_24h=float(market.get("volume_24h", 0) or 0),
            )

            # Skip if position held and prices unchanged
            try:
                from db_models import TradingPosition as TP, MarketPriceSnapshot as MPS
                from ai_prophet_core.betting.db import get_session as _gs
                with _gs(db_engine) as _sess:
                    _pos = _sess.query(TP).filter_by(market_id=market_id).first()
                    if _pos and _pos.quantity > 0:
                        _last = (
                            _sess.query(MPS)
                            .filter(MPS.market_id == market_id)
                            .order_by(MPS.timestamp.desc())
                            .first()
                        )
                        if (_last
                            and abs(_last.yes_ask - yes_ask) < 1e-6
                            and abs(_last.no_ask - no_ask) < 1e-6):
                            logger.info(
                                "  Skipping %s — market prices unchanged "
                                "(yes=%.2f, no=%.2f)",
                                ticker, yes_ask, no_ask,
                            )
                            all_market_prices[market_id] = (yes_ask, no_ask)
                            continue
            except Exception:
                pass

            save_price_snapshot(
                db_engine, market_id, ticker,
                yes_ask=yes_ask, no_ask=no_ask,
                volume_24h=float(market.get("volume_24h", 0) or 0),
            )

        # Market passed all filters — queue for LLM analysis
        markets_to_analyze.append({
            **market,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "market_id": market_id,
            "market_info": {
                "title": title,
                "subtitle": subtitle,
                "category": category,
                "yes_ask": yes_ask,
                "no_ask": no_ask,
            },
        })

    logger.info("Phase A complete: %d markets to analyze (from %d raw)",
                len(markets_to_analyze), len(raw_markets))

    if not markets_to_analyze:
        logger.info("No markets to analyze, skipping prediction phase")
        if betting_engine:
            betting_engine.close()
        return

    # ── Phase B: Collect predictions (parallel or sequential) ─────
    # predictions[(ticker, model_spec)] = {p_yes, confidence, reasoning, analysis}
    predictions: dict[tuple[str, str], dict] = {}

    if PREDICTOR_SERVICE_URL:
        # ── Remote parallel prediction via Cloud Run service ──────
        logger.info("Using remote predictor: %s (parallel fanout)", PREDICTOR_SERVICE_URL)

        prediction_tasks = [
            (mkt, ms)
            for mkt in markets_to_analyze
            for ms in model_specs
        ]

        logger.info("Fanning out %d prediction tasks (%d markets × %d models) with max_workers=20",
                     len(prediction_tasks), len(markets_to_analyze), len(model_specs))

        t_fan = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_key = {}
            for mkt, ms in prediction_tasks:
                future = executor.submit(
                    _remote_predict_with_retry, ms, mkt["market_info"],
                )
                future_to_key[future] = (mkt["ticker"], ms)

            for future in concurrent.futures.as_completed(future_to_key):
                key = future_to_key[future]
                ticker, ms = key
                try:
                    result = future.result()
                    predictions[key] = result
                    logger.info(
                        "  [%s] %s → p_yes=%.3f",
                        ms.split(":")[-1], ticker, result["p_yes"],
                    )
                except Exception as e:
                    logger.error("  [%s] %s prediction failed: %s", ms, ticker, e)

        logger.info("Phase B complete: %d/%d predictions in %.1fs",
                     len(predictions), len(prediction_tasks), time.time() - t_fan)
    else:
        # ── Local sequential prediction (development fallback) ────
        logger.info("Using local predictors (sequential, no PREDICTOR_SERVICE_URL set)")

        predictors: dict[str, Any] = {}
        for model_spec in model_specs:
            try:
                predictors[model_spec] = create_llm_predictor(model_spec)
            except Exception as e:
                logger.error("Failed to create predictor for %s: %s", model_spec, e)
                if db_engine:
                    log_system_event(db_engine, "ERROR", f"Predictor init failed for {model_spec}: {e}")

        if not predictors:
            logger.error("No predictors available, skipping cycle")
            if betting_engine:
                betting_engine.close()
            return

        for mkt in markets_to_analyze:
            ticker = mkt["ticker"]
            market_info = mkt["market_info"]

            logger.info("Analyzing: %s (yes=%.2f, no=%.2f)",
                        mkt.get("title", "")[:60], mkt["yes_ask"], mkt["no_ask"])

            for mi, (model_spec, predictor) in enumerate(predictors.items()):
                if mi > 0:
                    time.sleep(2)

                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        prediction = predictor(market_info)
                        predictions[(ticker, model_spec)] = prediction
                        logger.info(
                            "  [%s] p_yes=%.3f (confidence=%.2f) | %s",
                            model_spec.split(":")[-1],
                            prediction["p_yes"],
                            prediction.get("confidence", 0.5),
                            prediction.get("reasoning", "")[:60],
                        )
                        break
                    except Exception as e:
                        if attempt < max_retries:
                            logger.warning("  [%s] attempt %d failed, retrying in 5s: %s",
                                           model_spec, attempt + 1, e)
                            time.sleep(5)
                        else:
                            logger.error("  [%s] prediction failed after %d attempts: %s",
                                         model_spec, max_retries + 1, e)

    # ── Phase C: Aggregate & bet (sequential) ─────────────────────
    # BettingEngine is NOT thread-safe — process all markets sequentially.
    for mkt in markets_to_analyze:
        if _shutdown_requested:
            logger.info("Shutdown requested, stopping betting")
            break

        ticker = mkt["ticker"]
        market_id = mkt["market_id"]
        yes_ask = mkt["yes_ask"]
        no_ask = mkt["no_ask"]
        title = mkt.get("title", "Unknown")

        # Gather this market's predictions across all models
        model_predictions: dict[str, dict] = {}
        for ms in model_specs:
            pred = predictions.get((ticker, ms))
            if pred:
                p_yes = pred["p_yes"]
                confidence = pred.get("confidence", 0.5)
                reasoning = pred.get("reasoning", "")

                model_predictions[ms] = {
                    "p_yes": p_yes,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "analysis": pred.get("analysis", {}),
                }

                # Track edge for alert checking
                all_edges.append((market_id, ms, abs(p_yes - yes_ask)))

                # Save individual model run
                decision = "HOLD"
                if p_yes > yes_ask:
                    decision = "BUY_YES"
                elif p_yes < yes_ask:
                    decision = "BUY_NO"

                if db_engine:
                    save_model_run(
                        db_engine, ms, market_id, decision, confidence,
                        metadata={"p_yes": p_yes, "reasoning": reasoning,
                                  "analysis": pred.get("analysis", {}),
                                  "yes_ask": yes_ask, "no_ask": no_ask},
                    )

        if not model_predictions:
            logger.warning("  No model predictions for %s, skipping", ticker)
            all_market_prices[market_id] = (yes_ask, no_ask)
            continue

        # Use the single model's prediction directly (first available)
        model_spec = next(iter(model_predictions))
        mp = model_predictions[model_spec]
        p_yes = mp["p_yes"]
        edge = p_yes - yes_ask

        logger.info(
            "  [%s] edge=%.3f (p_yes=%.3f vs yes_ask=%.3f)",
            model_spec.split(":")[-1], edge, p_yes, yes_ask,
        )

        if db_engine:
            save_price_snapshot(
                db_engine, market_id, ticker,
                yes_ask=yes_ask, no_ask=no_ask,
                volume_24h=float(mkt.get("volume_24h", 0) or 0),
                model_p_yes=round(p_yes, 6),
                model_name=model_spec,
            )

        # Build per-market portfolio snapshot
        portfolio = None
        if db_engine:
            try:
                from ai_prophet_core.betting.db import get_session
                from ai_prophet_core.betting.strategy import PortfolioSnapshot
                from db_models import TradingPosition

                with get_session(db_engine) as session:
                    all_positions = session.query(TradingPosition).all()
                    capital_deployed = Decimal(str(
                        sum(p.avg_price * p.quantity for p in all_positions)
                    ))
                    total_realized = Decimal(str(
                        sum(p.realized_pnl for p in all_positions)
                    ))

                    try:
                        real_balance = adapter.get_balance()
                    except Exception:
                        real_balance = Decimal("0")
                    available_cash = real_balance - capital_deployed + total_realized

                    mkt_pos_shares = Decimal("0")
                    mkt_pos_side = None
                    for p in all_positions:
                        if p.market_id == market_id:
                            mkt_pos_shares = Decimal(str(p.quantity))
                            mkt_pos_side = p.contract
                            break

                    portfolio = PortfolioSnapshot(
                        cash=available_cash,
                        market_position_shares=mkt_pos_shares,
                        market_position_side=mkt_pos_side,
                    )

            except Exception as e:
                logger.debug("Could not load position for portfolio: %s", e)

        # Feed prediction directly into BettingEngine (strategy decides edge threshold)
        result = betting_engine.on_forecast(
            tick_ts=tick_ts,
            market_id=market_id,
            p_yes=p_yes,
            yes_ask=yes_ask,
            no_ask=no_ask,
            source=model_spec,
            portfolio=portfolio,
        )
        if result is not None:
            total_results.append(result)

        all_market_prices[market_id] = (yes_ask, no_ask)

    # Summarize cycle results
    if total_results:
        placed = sum(1 for r in total_results if r.order_placed)
        skipped = sum(1 for r in total_results if r.signal is None)
        logger.info(
            "Cycle results: %d orders placed, %d skipped, %d total across %d markets",
            placed, skipped, len(total_results), len(raw_markets),
        )
        if db_engine:
            log_system_event(
                db_engine, "INFO",
                f"Cycle complete: models={model_specs}, placed={placed}, "
                f"skipped={skipped}, total={len(total_results)}",
            )

    # 3b. Check alert conditions and log to SystemLog
    if db_engine:
        try:
            from ai_prophet_core.betting.db import get_session
            from db_models import TradingPosition

            # Alert if any model showed a large edge (|p_yes - yes_ask| > 0.20)
            for mid, mname, edge in all_edges:
                if edge > 0.20:
                    log_system_event(
                        db_engine, "ALERT",
                        f"Large edge detected on {mid} (model={mname}): "
                        f"edge={edge:.3f}",
                    )

            # Alert if total capital deployed is high
            with get_session(db_engine) as session:
                all_positions = session.query(TradingPosition).all()
                total_capital = sum(p.quantity * p.avg_price for p in all_positions)
                if total_capital > 50.0:  # threshold: $50 deployed
                    log_system_event(
                        db_engine, "ALERT",
                        f"High capital deployment: ${total_capital:.2f} across "
                        f"{len(all_positions)} positions",
                    )
        except Exception as e:
            logger.debug("Alert check failed: %s", e)

    # 4. Update positions from order history
    #    Re-fetch current prices for ALL traded tickers so unrealized PnL
    #    reflects actual market movement, not just this cycle's markets.
    if db_engine:
        traded = get_traded_tickers(db_engine)
        for ticker in traded:
            market_id = f"kalshi:{ticker}"
            if market_id not in all_market_prices:
                mkt = fetch_market_by_ticker(adapter, ticker)
                if mkt and mkt.get("yes_ask") is not None and mkt.get("no_ask") is not None:
                    all_market_prices[market_id] = (float(mkt["yes_ask"]), float(mkt["no_ask"]))

        # Fall back to cached prices in trading_markets table
        if not all_market_prices:
            try:
                from ai_prophet_core.betting.db import get_session
                from db_models import TradingMarket
                with get_session(db_engine) as session:
                    for tm in session.query(TradingMarket).all():
                        if tm.yes_ask is not None and tm.no_ask is not None:
                            all_market_prices[tm.market_id] = (tm.yes_ask, tm.no_ask)
            except Exception as e:
                logger.debug("Failed to load cached market prices: %s", e)

        # Always update positions (even without prices — deployed capital still tracked)
        update_positions(db_engine, all_market_prices)

    # Cleanup
    if betting_engine is not None:
        betting_engine.close()
    if db_engine is not None:
        log_heartbeat(db_engine, message="cycle_end")

    total_placed = sum(1 for r in total_results if r.order_placed)
    logger.info(
        "Cycle complete: %d total results, %d orders placed across %d models",
        len(total_results), total_placed, len(model_specs),
    )


# ── Entry point ───────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi trading worker (standalone)")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    parser.add_argument("--once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    poll_interval = int(os.getenv("WORKER_POLL_INTERVAL_SEC", "900"))

    logger.info("Worker starting (poll_interval=%ds, dry_run=%s)", poll_interval, args.dry_run)
    logger.info("Mode: STANDALONE (direct Kalshi API + LLM predictions)")

    while not _shutdown_requested:
        try:
            run_cycle(args)
        except SystemExit:
            break
        except Exception:
            traceback.print_exc()

        if args.once:
            logger.info("--once flag set, exiting after single cycle.")
            break

        logger.info("Sleeping %ds until next cycle...", poll_interval)
        for _ in range(poll_interval):
            if _shutdown_requested:
                break
            time.sleep(1)

    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
