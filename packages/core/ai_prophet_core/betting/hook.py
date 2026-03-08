"""
Live betting hook called after forecasts complete.

Aggregates model decisions and places one Kalshi order per market/tick.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Engine

from .config import KalshiConfig

logger = logging.getLogger(__name__)


class LiveBettingHook:
    """Aggregates forecasts from multiple models and places bets on Kalshi."""

    def __init__(
        self,
        betting_model_names: list[str],
        db_engine: Engine,
        enabled: bool = True,
        dry_run: bool = True,
        kalshi_config: KalshiConfig | None = None,
    ):
        """Create a live-betting hook with explicit runtime settings."""
        self.betting_models = set(betting_model_names)
        self.num_betting_models = len(self.betting_models)
        self.enabled = enabled
        self.dry_run = dry_run
        self._engine = db_engine
        self.kalshi_config = kalshi_config or KalshiConfig.from_env()

        self._lock = threading.Lock()
        self._decisions: dict[tuple[str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
        self._completed_keys: set[tuple[str, str]] = set()
        self._adapter = None

        self._init_tables()

        logger.info(
            "LiveBettingHook initialized: %s models, mode=%s, enabled=%s",
            self.num_betting_models,
            "DRY RUN" if dry_run else "LIVE",
            self.enabled,
        )

    def _init_tables(self) -> None:
        """Create live betting tables if they do not exist."""
        from .db_schema import Base

        Base.metadata.create_all(self._engine, checkfirst=True)

    def _get_adapter(self):
        """Lazy-init Kalshi adapter to avoid import issues at module load."""
        if self._adapter is not None:
            return self._adapter

        from .adapters.kalshi import KalshiAdapter

        self._adapter = KalshiAdapter(
            api_key_id=self.kalshi_config.api_key_id,
            private_key_base64=self.kalshi_config.private_key_base64,
            base_url=self.kalshi_config.base_url,
            dry_run=self.dry_run,
        )
        return self._adapter

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
        """Record one model forecast and place an aggregate order when complete."""
        if not self.enabled:
            return None

        if model_name not in self.betting_models:
            return None

        from .strategy import compute_bet

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

        bet_desc = "SKIP" if bet is None else f"{bet['side'].upper()} {bet['shares']:.4f}"
        logger.info(
            "[LIVE BETTING] %s on %s: p_yes=%.3f, bet=%s (%s/%s models done)",
            model_name,
            market_id,
            p_yes,
            bet_desc,
            count,
            self.num_betting_models,
        )

        if count == self.num_betting_models:
            logger.info(
                "[LIVE BETTING] All %s models done for %s - aggregating bets...",
                self.num_betting_models,
                market_id,
            )
            return self._aggregate_and_place(agg_key, tick_ts, market_id, yes_ask, no_ask, question)

        return None

    def _aggregate_and_place(
        self,
        agg_key: tuple[str, str],
        tick_ts: datetime,
        market_id: str,
        yes_ask: float,
        no_ask: float,
        question: str,
    ) -> dict[str, Any] | None:
        """Aggregate individual bets and place one order on Kalshi."""
        with self._lock:
            decisions_by_model = self._decisions.pop(agg_key, {})
            self._completed_keys.add(agg_key)
        decisions = list(decisions_by_model.values())

        net_shares = 0.0
        model_details = []
        for decision in decisions:
            bet = decision["bet"]
            if bet is None:
                model_details.append(f"  {decision['model_name']}: SKIP")
                continue
            signed = bet["shares"] if bet["side"] == "yes" else -bet["shares"]
            net_shares += signed
            model_details.append(
                f"  {decision['model_name']}: {bet['side'].upper()} {bet['shares']:.4f}"
            )

        logger.info(
            "[LIVE BETTING] Aggregation for %s:\n%s\n  NET: %+0.4f",
            market_id,
            "\n".join(model_details),
            net_shares,
        )

        if abs(net_shares) < 1e-6:
            logger.info("[LIVE BETTING] Net position is zero for %s, no bet", market_id)
            return None

        if net_shares > 0:
            side = "yes"
            price = yes_ask
        else:
            side = "no"
            price = no_ask

        count = max(1, round(abs(net_shares) * 100))
        price_cents = max(1, min(99, round(price * 100)))

        logger.info(
            "[LIVE BETTING] AGGREGATED BET: %s %s contracts @ %sc for %s",
            side.upper(),
            count,
            price_cents,
            market_id,
        )

        kalshi_ticker = market_id[len("kalshi:") :] if market_id.startswith("kalshi:") else market_id
        order_result = self._place_kalshi_order(kalshi_ticker, side, count, price)

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
        from .adapters.base import OrderRequest

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
            "[LIVE BETTING] Order result: %s - filled=%s @ %s (exchange_id=%s)",
            status,
            result.filled_shares,
            result.fill_price,
            result.exchange_order_id,
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
        from .db import get_session
        from .db_schema import BetDecisionTable

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
            with get_session(self._engine) as session:
                session.add(row)
        except Exception as e:
            logger.warning("Failed to persist bet decision: %s", e, exc_info=True)

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
        from .db import get_session
        from .db_schema import KalshiOrderTable

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
            with get_session(self._engine) as session:
                session.add(row)
        except Exception as e:
            logger.warning("Failed to persist Kalshi order: %s", e, exc_info=True)

    def get_decisions_for_market(self, tick_ts: datetime, market_id: str) -> list[dict[str, Any]]:
        """Return all bet decisions for a given tick + market from the DB."""
        from .db import get_session
        from .db_schema import BetDecisionTable

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
                    "model_name": row.model_name,
                    "p_yes": row.p_yes,
                    "side": row.side,
                    "shares": row.shares,
                    "price": row.price,
                    "cost": row.cost,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def get_recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent Kalshi orders from the DB."""
        from .db import get_session
        from .db_schema import KalshiOrderTable

        with get_session(self._engine) as session:
            rows = (
                session.query(KalshiOrderTable)
                .order_by(KalshiOrderTable.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "order_id": row.order_id,
                    "tick_ts": row.tick_ts.isoformat(),
                    "market_id": row.market_id,
                    "ticker": row.ticker,
                    "side": row.side,
                    "count": row.count,
                    "price_cents": row.price_cents,
                    "status": row.status,
                    "filled_shares": row.filled_shares,
                    "fill_price": row.fill_price,
                    "dry_run": row.dry_run,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def close(self) -> None:
        """Clean up resources."""
        if self._adapter:
            try:
                self._adapter.close()
            except Exception:
                pass
