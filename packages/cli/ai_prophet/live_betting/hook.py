"""
Live Betting Hook — called from the AgentPipeline after forecasts.

After a model produces probability forecasts, this module:
1. Checks if the model is one of our betting models
2. Computes the individual bet decision (edge-based) via compute_bet()
3. Persists the decision to the bet_decisions table
4. When ALL betting models have predicted for a given (tick, market),
   aggregates their bets (summing shares) and places ONE order on Kalshi,
   recording it in the kalshi_orders table.

Thread-safe: participants run in parallel threads within ExperimentRunner.
"""

from __future__ import annotations

import logging
import os
import threading
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Master switch — set to True to enable live betting
LIVE_BETTING_ENABLED = os.getenv("LIVE_BETTING_ENABLED", "false").lower() == "true"

# Dry run by default (even if enabled, won't place real orders unless this is False)
LIVE_BETTING_DRY_RUN = os.getenv("LIVE_BETTING_DRY_RUN", "true").lower() != "false"


class LiveBettingHook:
    """Aggregates forecasts from multiple models and places bets on Kalshi.

    One instance is shared across all participants within an ExperimentRunner.
    Uses a lock for thread-safety since participants run in parallel.

    Bet decisions and Kalshi orders are persisted in local live-betting tables.
    """

    def __init__(
        self,
        betting_model_names: list[str],
        db_engine: Engine,
        dry_run: bool = True,
    ):
        """
        Args:
            betting_model_names: List of model specs (e.g. "google:gemini-3-pro-preview")
                that participate in live betting aggregation.
            db_engine: SQLAlchemy engine connected to the shared database.
            dry_run: If True, simulate orders without hitting Kalshi.
        """
        self.betting_models = set(betting_model_names)
        self.num_betting_models = len(self.betting_models)
        self.dry_run = dry_run
        self._engine = db_engine

        # Thread-safe storage: (tick_ts_iso, market_id) -> model_name -> decision
        self._lock = threading.Lock()
        self._decisions: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
        self._completed_keys: set[tuple[str, str]] = set()

        # Lazy-initialized Kalshi adapter
        self._adapter = None

        # Ensure bet_decisions / kalshi_orders tables exist
        self._init_tables()

        logger.info(
            f"LiveBettingHook initialized: "
            f"{self.num_betting_models} models, "
            f"mode={'DRY RUN' if dry_run else 'LIVE'}, "
            f"enabled={LIVE_BETTING_ENABLED}"
        )

    # ------------------------------------------------------------------
    # Initialization helpers
    # ------------------------------------------------------------------

    def _init_tables(self) -> None:
        """Create only the bet_decisions and kalshi_orders tables if they don't exist."""
        from ai_prophet.live_betting.db_schema import Base

        Base.metadata.create_all(self._engine, checkfirst=True)

    def _get_adapter(self):
        """Lazy-init Kalshi adapter to avoid import issues at module load."""
        if self._adapter is not None:
            return self._adapter

        from ai_prophet.live_betting.adapters.kalshi import KalshiAdapter
        from ai_prophet.live_betting.config import (
            KALSHI_API_KEY_ID,
            KALSHI_BASE_URL,
            KALSHI_PRIVATE_KEY_B64,
        )

        self._adapter = KalshiAdapter(
            api_key_id=KALSHI_API_KEY_ID,
            private_key_b64=KALSHI_PRIVATE_KEY_B64 or "",
            base_url=KALSHI_BASE_URL,
            dry_run=self.dry_run,
        )
        return self._adapter

    # ------------------------------------------------------------------
    # Main entry point (called per-model after each forecast)
    # ------------------------------------------------------------------

    def on_forecast(
        self,
        model_name: str,
        tick_ts: datetime,
        market_id: str,
        p_yes: float,
        yes_ask: float,
        no_ask: float,
        question: str = "",
    ) -> dict[str, Any] | None:
        """Called after a model produces a forecast for a market.

        If this is a betting model, computes the individual bet decision,
        persists it to the DB, and checks if all betting models have reported.
        If so, aggregates and places ONE order on Kalshi.

        Args:
            model_name: ExperimentRunner model spec (e.g. "google:gemini-3-pro-preview")
            tick_ts: Current tick timestamp
            market_id: Internal market ID (e.g. "kalshi:TICKER-XYZ")
            p_yes: Model's predicted probability of YES (0-1)
            yes_ask: Current YES ask price (0-1 scale)
            no_ask: Current NO ask price (0-1 scale)
            question: Market question text (for logging)

        Returns:
            Order result dict if an aggregated bet was placed, None otherwise.
        """
        if not LIVE_BETTING_ENABLED:
            return None

        if model_name not in self.betting_models:
            return None

        from ai_prophet.live_betting.strategy import compute_bet

        # Compute individual bet
        bet = compute_bet(p_yes, yes_ask, no_ask)

        tick_key = tick_ts.isoformat()
        agg_key = (tick_key, market_id)

        decision = {
            "model_name": model_name,
            "p_yes": p_yes,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "bet": bet,
        }

        should_persist = False
        with self._lock:
            if agg_key in self._completed_keys:
                logger.info(
                    "[LIVE BETTING] Ignoring late forecast for completed key %s/%s",
                    tick_key,
                    market_id,
                )
                return None
            is_new_model_decision = model_name not in self._decisions[agg_key]
            self._decisions[agg_key][model_name] = decision
            count = len(self._decisions[agg_key])
            should_persist = is_new_model_decision

        if should_persist:
            self._save_bet_decision(
                model_name=model_name,
                tick_ts=tick_ts,
                market_id=market_id,
                p_yes=p_yes,
                yes_ask=yes_ask,
                no_ask=no_ask,
                bet=bet,
            )
        else:
            logger.info(
                "[LIVE BETTING] Duplicate forecast from %s for %s/%s; overwriting in-memory decision",
                model_name,
                tick_key,
                market_id,
            )

        if bet is None:
            bet_desc = "SKIP"
        else:
            bet_desc = f"{bet['side'].upper()} {bet['shares']:.4f}"
        logger.info(
            f"[LIVE BETTING] {model_name} on {market_id}: "
            f"p_yes={p_yes:.3f}, bet={bet_desc} "
            f"({count}/{self.num_betting_models} models done)"
        )

        # Check if all betting models have reported
        if count == self.num_betting_models:
            logger.info(
                f"[LIVE BETTING] All {self.num_betting_models} models done for "
                f"{market_id} — aggregating bets..."
            )
            return self._aggregate_and_place(agg_key, tick_ts, market_id, yes_ask, no_ask, question)

        return None

    # ------------------------------------------------------------------
    # Aggregation & order placement
    # ------------------------------------------------------------------

    def _aggregate_and_place(
        self,
        agg_key: tuple[str, str],
        tick_ts: datetime,
        market_id: str,
        yes_ask: float,
        no_ask: float,
        question: str,
    ) -> dict[str, Any] | None:
        """Aggregate individual bets and place ONE order on Kalshi."""
        with self._lock:
            decisions_by_model = self._decisions.pop(agg_key, {})
            self._completed_keys.add(agg_key)
        decisions = list(decisions_by_model.values())

        # Sum shares: YES = positive, NO = negative
        net_shares = 0.0
        model_details = []
        for d in decisions:
            bet = d["bet"]
            if bet is None:
                model_details.append(f"  {d['model_name']}: SKIP")
                continue
            signed = bet["shares"] if bet["side"] == "yes" else -bet["shares"]
            net_shares += signed
            model_details.append(
                f"  {d['model_name']}: {bet['side'].upper()} {bet['shares']:.4f}"
            )

        logger.info(
            f"[LIVE BETTING] Aggregation for {market_id}:\n"
            + "\n".join(model_details)
            + f"\n  NET: {net_shares:+.4f}"
        )

        if abs(net_shares) < 1e-6:
            logger.info(f"[LIVE BETTING] Net position is zero for {market_id}, no bet")
            return None

        # Determine side and price
        if net_shares > 0:
            side = "yes"
            price = yes_ask
        else:
            side = "no"
            price = no_ask

        count = max(1, round(abs(net_shares) * 100))
        price_cents = max(1, min(99, round(price * 100)))

        logger.info(
            f"[LIVE BETTING] AGGREGATED BET: {side.upper()} {count} contracts "
            f"@ {price_cents}c for {market_id}"
        )

        # Extract Kalshi ticker from market_id ("kalshi:TICKER" → "TICKER")
        kalshi_ticker = market_id
        if market_id.startswith("kalshi:"):
            kalshi_ticker = market_id[len("kalshi:"):]

        order_result = self._place_kalshi_order(kalshi_ticker, side, count, price)

        # Persist aggregated order to DB
        self._save_kalshi_order(
            order_id=order_result["order_id"],
            tick_ts=tick_ts,
            market_id=market_id,
            ticker=kalshi_ticker,
            side=side,
            count=count,
            price_cents=price_cents,
            net_shares=net_shares,
            status=order_result["status"],
            filled_shares=order_result["filled_shares"],
            fill_price=order_result["fill_price"],
            exchange_order_id=order_result.get("exchange_order_id"),
        )

        return order_result

    def _place_kalshi_order(
        self,
        ticker: str,
        side: str,
        count: int,
        price: float,
    ) -> dict[str, Any]:
        """Place an order on Kalshi via the adapter."""
        from ai_prophet.live_betting.adapters.base import OrderRequest

        adapter = self._get_adapter()
        order_id = str(uuid.uuid4())

        order_req = OrderRequest(
            order_id=order_id,
            intent_id=f"live-agg-{order_id[:8]}",
            market_id=f"kalshi:{ticker}",
            exchange_ticker=ticker,
            action="BUY",
            side=side.upper(),
            shares=Decimal(str(count)),
            limit_price=Decimal(str(price)),
        )

        result = adapter.submit_order(order_req)
        status = result.status.value

        logger.info(
            f"[LIVE BETTING] Order result: {status} — "
            f"filled={result.filled_shares} @ {result.fill_price} "
            f"(exchange_id={result.exchange_order_id})"
        )

        return {
            "order_id": order_id,
            "ticker": ticker,
            "side": side,
            "count": count,
            "price": price,
            "status": status,
            "filled_shares": float(result.filled_shares),
            "fill_price": float(result.fill_price),
            "exchange_order_id": result.exchange_order_id,
        }

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def _save_bet_decision(
        self,
        model_name: str,
        tick_ts: datetime,
        market_id: str,
        p_yes: float,
        yes_ask: float,
        no_ask: float,
        bet: dict[str, Any] | None,
    ) -> None:
        """Insert a row into bet_decisions for this model's individual decision."""
        from ai_prophet.live_betting.db_schema import BetDecisionTable

        now = datetime.now(UTC)
        row = BetDecisionTable(
            model_name=model_name,
            tick_ts=tick_ts,
            market_id=market_id,
            p_yes=p_yes,
            yes_ask=yes_ask,
            no_ask=no_ask,
            side=bet["side"] if bet else None,
            shares=bet["shares"] if bet else None,
            price=bet["price"] if bet else None,
            cost=bet["cost"] if bet else None,
            created_at=now,
        )

        try:
            from ai_prophet.live_betting.db import get_session
            with get_session(self._engine) as session:
                session.add(row)
        except Exception as e:
            logger.warning(f"Failed to persist bet decision: {e}", exc_info=True)

    def _save_kalshi_order(
        self,
        order_id: str,
        tick_ts: datetime,
        market_id: str,
        ticker: str,
        side: str,
        count: int,
        price_cents: int,
        net_shares: float,
        status: str,
        filled_shares: float,
        fill_price: float,
        exchange_order_id: str | None,
    ) -> None:
        """Insert a row into kalshi_orders for the aggregated order."""
        from ai_prophet.live_betting.db_schema import KalshiOrderTable

        now = datetime.now(UTC)
        row = KalshiOrderTable(
            order_id=order_id,
            tick_ts=tick_ts,
            market_id=market_id,
            ticker=ticker,
            side=side,
            count=count,
            price_cents=price_cents,
            net_shares=net_shares,
            status=status,
            filled_shares=filled_shares,
            fill_price=fill_price,
            exchange_order_id=exchange_order_id,
            dry_run=self.dry_run,
            created_at=now,
        )

        try:
            from ai_prophet.live_betting.db import get_session
            with get_session(self._engine) as session:
                session.add(row)
        except Exception as e:
            logger.warning(f"Failed to persist Kalshi order: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Query helpers (useful for debugging / dashboard)
    # ------------------------------------------------------------------

    def get_decisions_for_market(
        self, tick_ts: datetime, market_id: str,
    ) -> list[dict[str, Any]]:
        """Return all bet decisions for a given tick + market from the DB."""
        from ai_prophet.live_betting.db import get_session
        from ai_prophet.live_betting.db_schema import BetDecisionTable

        with get_session(self._engine) as session:
            rows = (
                session.query(BetDecisionTable)
                .filter(
                    BetDecisionTable.tick_ts == tick_ts,
                    BetDecisionTable.market_id == market_id,
                )
                .order_by(BetDecisionTable.created_at)
                .all()
            )
            return [
                {
                    "model_name": r.model_name,
                    "p_yes": r.p_yes,
                    "side": r.side,
                    "shares": r.shares,
                    "price": r.price,
                    "cost": r.cost,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]

    def get_recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent Kalshi orders from the DB."""
        from ai_prophet.live_betting.db import get_session
        from ai_prophet.live_betting.db_schema import KalshiOrderTable

        with get_session(self._engine) as session:
            rows = (
                session.query(KalshiOrderTable)
                .order_by(KalshiOrderTable.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "order_id": r.order_id,
                    "tick_ts": r.tick_ts.isoformat(),
                    "market_id": r.market_id,
                    "ticker": r.ticker,
                    "side": r.side,
                    "count": r.count,
                    "price_cents": r.price_cents,
                    "status": r.status,
                    "filled_shares": r.filled_shares,
                    "fill_price": r.fill_price,
                    "dry_run": r.dry_run,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rows
            ]

    def close(self) -> None:
        """Clean up resources."""
        if self._adapter:
            try:
                self._adapter.close()
            except Exception:
                pass
