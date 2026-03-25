"""
Betting engine — the main entry point for the betting module.

Takes probabilistic predictions as input, runs them through a pluggable
:class:`~ai_prophet_core.betting.strategy.BettingStrategy`, places orders
via the exchange adapter, and logs everything to the database.

Usage::

    from ai_prophet_core.betting import BettingEngine

    engine = BettingEngine(dry_run=True)
    results = engine.process_forecasts(
        tick_ts=tick_ts,
        forecasts={"kalshi:TICKER": 0.72},
        market_prices={"kalshi:TICKER": (0.55, 0.45)},
        source="my-model",
    )
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.engine import Engine

from .config import MAX_MARKETS_PER_TICK, MAX_ORDER_COST, KalshiConfig
from .strategy import BetSignal, BettingStrategy, DefaultBettingStrategy, PortfolioSnapshot, RebalancingStrategy

logger = logging.getLogger(__name__)

_position_replay_cache: tuple | None = None


def _get_position_replay():
    """Lazy-import position_replay helpers, inserting sys.path only once."""
    global _position_replay_cache
    if _position_replay_cache is not None:
        return _position_replay_cache
    import sys, os
    _services = os.path.join(os.path.dirname(__file__), "../../../../services")
    if _services not in sys.path:
        sys.path.insert(0, _services)
    from position_replay import replay_orders_by_ticker, summarize_replayed_positions
    _position_replay_cache = (replay_orders_by_ticker, summarize_replayed_positions)
    return _position_replay_cache


@dataclass
class BetResult:
    """Outcome of a single market bet attempt."""

    market_id: str
    signal: BetSignal | None
    order_placed: bool
    order_id: str | None = None
    status: str | None = None
    filled_shares: float = 0.0
    fill_price: float = 0.0
    fee_paid: float = 0.0
    exchange_order_id: str | None = None
    error: str | None = None


class BettingEngine:
    """Evaluate predictions, place bets, log to DB.

    This is the single integration point for the betting module.
    The :class:`ExperimentRunner` (or any caller) feeds forecasts
    produced by the trading pipeline into :meth:`process_forecasts`,
    and the engine handles strategy evaluation, order placement, and
    database logging.

    Args:
        strategy: A :class:`BettingStrategy` instance.  Defaults to
            :class:`DefaultBettingStrategy`.
        db_engine: SQLAlchemy engine for persistence.  ``None`` disables
            DB logging (useful in tests / notebooks).
        dry_run: When ``True`` the exchange adapter simulates fills.
        kalshi_config: Explicit Kalshi credentials.  Defaults to env vars.
        enabled: Master kill-switch.
    """

    def __init__(
        self,
        strategy: BettingStrategy | None = None,
        db_engine: Engine | None = None,
        dry_run: bool = True,
        kalshi_config: KalshiConfig | None = None,
        enabled: bool = True,
        max_markets_per_tick: int = MAX_MARKETS_PER_TICK,
        instance_name: str = "Haifeng",
        starting_cash: float = 10000.0,
    ) -> None:
        self.strategy = strategy or DefaultBettingStrategy()
        self.dry_run = dry_run
        self.starting_cash = starting_cash
        self.enabled = enabled
        self.max_markets_per_tick = max_markets_per_tick
        self.instance_name = instance_name
        self._engine = db_engine
        self._kalshi_config = kalshi_config or KalshiConfig.from_env()
        self._adapter = None

        if self._engine is not None:
            self._init_tables()

        logger.info(
            "BettingEngine initialized: strategy=%s, mode=%s, enabled=%s, db=%s",
            self.strategy.name,
            "DRY RUN" if dry_run else "LIVE",
            self.enabled,
            "yes" if self._engine else "none",
        )

    # ── public API ────────────────────────────────────────────────────

    def process_forecasts(
        self,
        tick_ts: datetime,
        forecasts: dict[str, float],
        market_prices: dict[str, tuple[float, float]],
        source: str = "",
        portfolio: PortfolioSnapshot | None = None,  # noqa: ARG002 — kept for API compat; live DB state used instead
    ) -> list[BetResult]:
        """Run the full predict → evaluate → place → log cycle.

        Args:
            tick_ts: Timestamp of the current tick.
            forecasts: ``{market_id: p_yes}`` predictions.
            market_prices: ``{market_id: (yes_ask, no_ask)}`` live quotes.
            source: Identifier for the prediction source (model name, etc.).
            portfolio: Ignored. Portfolio is refreshed from live DB state
                before each market's strategy evaluation.

        Returns:
            A :class:`BetResult` for every market in *forecasts*.
        """
        if not self.enabled:
            return []

        # Use caller's portfolio as fallback when no DB is available
        self.strategy._portfolio = portfolio

        results: list[BetResult] = []
        # Collect evaluated signals before placing orders (for cap enforcement)
        pending_orders: list[tuple[str, float, float, float, BetSignal, int | None]] = []

        for market_id, p_yes in forecasts.items():
            prices = market_prices.get(market_id)
            if prices is None:
                logger.warning(
                    "[BETTING] No prices for %s, skipping", market_id,
                )
                results.append(BetResult(market_id=market_id, signal=None, order_placed=False))
                continue

            yes_ask, no_ask = prices

            # 1. Persist the prediction
            prediction_id = self._save_prediction(
                tick_ts=tick_ts,
                market_id=market_id,
                source=source,
                p_yes=p_yes,
                yes_ask=yes_ask,
                no_ask=no_ask,
            )

            # 2. Refresh portfolio from live DB state (not the stale snapshot
            #    from the caller) so the strategy always sees the authoritative
            #    position for THIS market — prevents stale-delta over-buying.
            #    Falls back to caller's portfolio when no DB is configured.
            if self._engine is not None:
                ticker = market_id[len("kalshi:"):] if market_id.startswith("kalshi:") else market_id
                live_side, live_qty, live_cash = self._live_ledger_state(ticker)
                self.strategy._portfolio = PortfolioSnapshot(
                    cash=live_cash,
                    market_position_shares=Decimal(str(live_qty)),
                    market_position_side=live_side,
                )

            # 3. Evaluate strategy
            signal = self.strategy.evaluate(
                market_id=market_id,
                p_yes=p_yes,
                yes_ask=yes_ask,
                no_ask=no_ask,
            )

            if signal is None:
                logger.info(
                    "[BETTING] %s on %s: p_yes=%.3f → SKIP",
                    source, market_id, p_yes,
                )
                results.append(BetResult(market_id=market_id, signal=None, order_placed=False))
                continue

            # 4. Persist signal
            signal_id = self._save_signal(prediction_id, signal)

            logger.info(
                "[BETTING] %s on %s: p_yes=%.3f → %s %.4f @ %.3f",
                source, market_id, p_yes,
                signal.side.upper(), signal.shares, signal.price,
            )

            pending_orders.append((market_id, p_yes, yes_ask, no_ask, signal, signal_id))

        # 5. Cap to max_markets_per_tick, keeping highest-edge signals
        if len(pending_orders) > self.max_markets_per_tick:
            logger.warning(
                "[BETTING] %d signals exceed max_markets_per_tick=%d, "
                "keeping top %d by edge",
                len(pending_orders), self.max_markets_per_tick,
                self.max_markets_per_tick,
            )
            pending_orders.sort(
                key=lambda t: abs(t[1] - t[2]),  # abs(p_yes - yes_ask)
                reverse=True,
            )
            dropped = pending_orders[self.max_markets_per_tick:]
            pending_orders = pending_orders[:self.max_markets_per_tick]
            for mid, _, _, _, sig, _ in dropped:
                results.append(BetResult(
                    market_id=mid, signal=sig, order_placed=False,
                    error="Dropped: exceeded max_markets_per_tick",
                ))

        # 6. Place orders
        for market_id, _p_yes, yes_ask, no_ask, signal, signal_id in pending_orders:
            result = self._place_and_log_order(
                tick_ts=tick_ts,
                market_id=market_id,
                signal=signal,
                signal_id=signal_id,
                yes_ask=yes_ask,
                no_ask=no_ask,
            )
            results.append(result)

        return results

    def on_forecast(
        self,
        tick_ts: datetime,
        market_id: str,
        p_yes: float,
        yes_ask: float,
        no_ask: float,
        question: str = "",
        source: str = "",
        portfolio: PortfolioSnapshot | None = None,
    ) -> BetResult | None:
        """Convenience method for single-market callback use.

        Matches the signature expected by the pipeline's ``on_forecast``
        callback so it can be wired directly::

            pipeline_config["on_forecast"] = engine.on_forecast
        """
        if not self.enabled:
            return None

        results = self.process_forecasts(
            tick_ts=tick_ts,
            forecasts={market_id: p_yes},
            market_prices={market_id: (yes_ask, no_ask)},
            source=source,
            portfolio=portfolio,
        )
        return results[0] if results else None

    def close(self) -> None:
        """Release resources."""
        if self._adapter:
            try:
                self._adapter.close()
            except Exception:
                pass

    # ── internals ─────────────────────────────────────────────────────

    def _init_tables(self) -> None:
        from .db_schema import Base

        if self._engine is not None:
            Base.metadata.create_all(self._engine, checkfirst=True)

    def _get_adapter(self):
        if self._adapter is not None:
            return self._adapter

        from .adapters.kalshi import KalshiAdapter

        self._adapter = KalshiAdapter(
            api_key_id=self._kalshi_config.api_key_id,
            private_key_base64=self._kalshi_config.private_key_base64,
            base_url=self._kalshi_config.base_url,
            dry_run=self.dry_run,
        )
        return self._adapter

    def _live_ledger_state(self, ticker: str) -> tuple[str | None, int, Decimal]:
        """Query the live order ledger for ground-truth position and cash.

        Returns (side, qty, available_cash) by replaying ALL instance orders
        from the DB.  Called immediately before every order placement so
        nothing is ever stale.

        For DRY_RUN mode: uses starting_cash as the fixed baseline (no API
        call needed — DRY_RUN orders never affect the real Kalshi balance).
        For LIVE mode: fetches real balance from the adapter (Kalshi already
        deducts for real orders, so we use it directly without subtraction).

        IMPORTANT: Only counts FILLED orders (and DRY_RUN in dry-run mode) for positions.
        PENDING orders are NOT included in position calculation to avoid discrepancies
        with partially filled or unfilled orders. Pending orders are cancelled before
        placing new orders.
        """
        if self._engine is None:
            return None, 0, Decimal(str(self.starting_cash))
        try:
            from .db import get_session
            from .db_schema import BettingOrder
            replay_orders_by_ticker, summarize_replayed_positions = _get_position_replay()

            with get_session(self._engine) as session:
                # Only include FILLED orders (and DRY_RUN in dry-run mode) for position calculation
                # PENDING orders are not included as they may not fill or may only partially fill
                status_filter = ["FILLED", "DRY_RUN"] if self.dry_run else ["FILLED"]
                orders = (
                    session.query(BettingOrder)
                    .filter(BettingOrder.instance_name == self.instance_name)
                    .filter(BettingOrder.status.in_(status_filter))
                    .order_by(BettingOrder.created_at.asc(), BettingOrder.id.asc())
                    .all()
                )

            positions = replay_orders_by_ticker(orders)
            capital_deployed, total_realized, _ = summarize_replayed_positions(positions)

            if self.dry_run:
                # DRY_RUN: fixed virtual budget — no API call needed
                base = Decimal(str(self.starting_cash))
                cash = base - Decimal(str(capital_deployed)) + Decimal(str(total_realized))
            else:
                # LIVE: real balance from Kalshi already accounts for real orders
                try:
                    cash = self._get_adapter().get_balance()
                except Exception:
                    cash = Decimal("0")

            pos = positions.get(ticker)
            if pos is None:
                return None, 0, cash
            side, qty, _ = pos.current_position()
            return side, max(0, round(qty)), cash
        except Exception as e:
            logger.warning("[BETTING] _live_ledger_state query failed for %s: %s", ticker, e)
            return None, 0, Decimal("0")

    def _place_and_log_order(
        self,
        tick_ts: datetime,
        market_id: str,
        signal: BetSignal,
        signal_id: int | None,
        yes_ask: float = 0.0,
        no_ask: float = 0.0,
    ) -> BetResult:
        """Convert a signal into an exchange order, persist the result.

        Implements NET position management: if the strategy wants to buy
        one side but we already hold the opposite side, we SELL existing
        contracts first.  We only buy the new side when the desired
        quantity exceeds the existing opposite position.
        """
        from .adapters.base import OrderRequest

        ticker = market_id[len("kalshi:"):] if market_id.startswith("kalshi:") else market_id

        adapter = self._get_adapter()

        count = max(1, round(abs(signal.shares) * 100))
        price_cents = max(1, min(99, round(signal.price * 100)))

        # --- Cancel any pending orders for this ticker before rebalancing ---
        # This prevents double-ordering from partially filled or unfilled orders
        if self._engine is not None and not self.dry_run:
            try:
                import sys, os
                _services = os.path.join(os.path.dirname(__file__), "../../../../services")
                if _services not in sys.path:
                    sys.path.insert(0, _services)
                from order_management import cancel_partially_filled_orders

                cancelled = cancel_partially_filled_orders(
                    self._engine, adapter, self.instance_name, ticker
                )
                if cancelled > 0:
                    logger.info(
                        "[BETTING] Cancelled %d pending order(s) for %s before placing new order",
                        cancelled, ticker
                    )
            except Exception as e:
                logger.warning("[BETTING] Failed to cancel pending orders for %s: %s", ticker, e)

        # --- Live ledger state: single DB query for ground-truth position + cash ---
        # Both NET management and the cash check use this so neither is ever stale,
        # even when multiple markets are processed in the same cycle.
        live_side, live_qty, live_cash = self._live_ledger_state(ticker)
        action = "BUY"
        effective_side = signal.side.upper()
        sell_price = signal.price  # fallback; overwritten if a SELL is needed

        if live_side and live_qty > 0:
            held_side = live_side.lower()
            want_side = signal.side.lower()

            if held_side != want_side:
                held_count = live_qty
                # Use the correct price for the side being sold
                sell_price = yes_ask if held_side == "yes" else no_ask
                sell_price_cents = max(1, min(99, round(sell_price * 100)))

                # SAFETY CHECK: Verify we actually have shares to sell
                # The position replay might report incorrect quantities
                if held_count <= 0:
                    logger.warning(
                        "[BETTING] OVERSELL PREVENTED for %s: tried to sell %d %s but have 0 shares",
                        ticker, count, held_side.upper()
                    )
                    # Skip the sell, just buy the wanted side
                    action = "BUY"
                    effective_side = want_side.upper()
                elif count <= held_count:
                    # Just sell some of the existing opposite position
                    action = "SELL"
                    effective_side = held_side.upper()
                    price_cents = sell_price_cents
                    logger.info(
                        "[BETTING] NET: selling %d %s instead of buying %d %s on %s",
                        count, held_side.upper(), count, want_side.upper(), ticker,
                    )
                else:
                    # Sell all existing, then buy remainder on new side
                    sell_order_id = str(uuid.uuid4())
                    sell_req = OrderRequest(
                        order_id=sell_order_id,
                        intent_id=f"net-sell-{sell_order_id[:8]}",
                        market_id=market_id,
                        exchange_ticker=ticker,
                        action="SELL",
                        side=held_side.upper(),
                        shares=Decimal(str(held_count)),
                        limit_price=Decimal(str(sell_price)),
                    )
                    sell_status = "FILLED"
                    try:
                        sell_result = adapter.submit_order(sell_req)
                        sell_status = sell_result.status.value
                        logger.info(
                            "[BETTING] NET: sold %d %s on %s → %s",
                            held_count, held_side.upper(), ticker, sell_status,
                        )
                        self._save_order(
                            signal_id=None,  # NET sells are not driven by a signal for this market
                            order_id=sell_order_id,
                            ticker=ticker,
                            side=held_side,
                            count=held_count,
                            price_cents=sell_price_cents,
                            status=sell_status,
                            filled_shares=float(sell_result.filled_shares),
                            fill_price=float(sell_result.fill_price),
                            fee_paid=float(sell_result.fee),
                            exchange_order_id=sell_result.exchange_order_id,
                            action="SELL",
                        )
                    except Exception as e:
                        logger.error("[BETTING] NET sell failed: %s", e)
                        sell_status = "ERROR"

                    count = count - held_count
                    if count <= 0:
                        return BetResult(
                            market_id=market_id,
                            signal=signal,
                            order_placed=sell_status != "ERROR",
                            order_id=sell_order_id,
                            status=sell_status,
                        )
                    # Continue to buy remaining on new side
                    action = "BUY"
                    effective_side = want_side.upper()
                    # Refresh cash after the NET sell — proceeds are now persisted
                    # to DB and must be available for the subsequent BUY.
                    _, _, live_cash = self._live_ledger_state(ticker)

        # --- Cash constraint: use live cash so multi-market cycles don't overspend ---
        if action == "BUY":
            if live_cash <= 0:
                logger.warning(
                    "[BETTING] Insufficient cash: live balance is $%.2f, skipping BUY %s",
                    float(live_cash), ticker,
                )
                return BetResult(
                    market_id=market_id,
                    signal=signal,
                    order_placed=False,
                    error=f"Insufficient cash: live balance is ${float(live_cash):.2f}",
                )
            order_cost = Decimal(str(count)) * Decimal(str(signal.price))
            if order_cost > Decimal(str(MAX_ORDER_COST)):
                logger.warning(
                    "[BETTING] Max order cost exceeded: need $%.2f which is above $%.2f, skipping %s",
                    float(order_cost), MAX_ORDER_COST, ticker,
                )
                return BetResult(
                    market_id=market_id,
                    signal=signal,
                    order_placed=False,
                    error=f"SKIP: order cost ${float(order_cost):.2f} exceeds max ${MAX_ORDER_COST:.2f}",
                )
            if order_cost > live_cash:
                max_shares = int(live_cash / Decimal(str(signal.price)))
                if max_shares <= 0:
                    logger.warning(
                        "[BETTING] Insufficient cash: need $%.2f but only $%.2f available, skipping %s",
                        float(order_cost), float(live_cash), ticker,
                    )
                    return BetResult(
                        market_id=market_id,
                        signal=signal,
                        order_placed=False,
                        error=f"Insufficient cash: need ${float(order_cost):.2f}, have ${float(live_cash):.2f}",
                    )
                logger.info(
                    "[BETTING] Cash cap: reducing %s from %d to %d shares (cash=$%.2f)",
                    ticker, count, max_shares, float(live_cash),
                )
                count = max_shares

        order_id = str(uuid.uuid4())

        order_req = OrderRequest(
            order_id=order_id,
            intent_id=f"bet-{order_id[:8]}",
            market_id=market_id,
            exchange_ticker=ticker,
            action=action,
            side=effective_side,
            shares=Decimal(str(count)),
            limit_price=Decimal(str(sell_price if action == "SELL" else signal.price)),
        )

        try:
            order_result = adapter.submit_order(order_req)

            # Poll if order is resting/pending (live mode only)
            from .adapters.base import OrderStatus
            if (
                order_result.status == OrderStatus.PENDING
                and order_result.exchange_order_id
                and not self.dry_run
            ):
                order_result = self._poll_order_status(
                    adapter, order_result,
                )

            status = order_result.status.value
            filled_shares = float(order_result.filled_shares)
            fill_price = float(order_result.fill_price)
            raw_fee = getattr(order_result, "fee", 0)
            try:
                fee_paid = float(raw_fee or 0)
            except (TypeError, ValueError):
                fee_paid = 0.0
            exchange_oid = order_result.exchange_order_id
            error = order_result.rejection_reason
        except Exception as e:
            logger.error("[BETTING] Order submission failed: %s", e, exc_info=True)
            status = "ERROR"
            filled_shares = 0.0
            fill_price = 0.0
            fee_paid = 0.0
            exchange_oid = None
            error = str(e)

        logger.info(
            "[BETTING] Order %s: %s %s %s×%s @ %sc → %s (filled=%s @ %s, fee=%s)",
            order_id[:8], action, effective_side, count, ticker,
            price_cents, status, filled_shares, fill_price, fee_paid,
        )

        self._save_order(
            signal_id=signal_id,
            order_id=order_id,
            ticker=ticker,
            side=effective_side.lower(),
            count=count,
            price_cents=price_cents,
            status=status,
            filled_shares=filled_shares,
            fill_price=fill_price,
            fee_paid=fee_paid,
            exchange_order_id=exchange_oid,
            action=action,
        )

        return BetResult(
            market_id=market_id,
            signal=signal,
            order_placed=True,
            order_id=order_id,
            status=status,
            filled_shares=filled_shares,
            fill_price=fill_price,
            fee_paid=fee_paid,
            exchange_order_id=exchange_oid,
            error=error,
        )

    def _poll_order_status(
        self,
        adapter,
        initial_result,
        max_polls: int = 5,
        interval_sec: float = 2.0,
    ):
        """Poll exchange for order fill status after a PENDING submission."""
        from .adapters.base import OrderStatus

        exchange_oid = initial_result.exchange_order_id
        for attempt in range(max_polls):
            time.sleep(interval_sec)
            polled = adapter.get_order(exchange_oid)
            if polled is None:
                break
            if polled.status in (
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
            ):
                # Preserve original order/intent IDs
                polled.order_id = initial_result.order_id
                polled.intent_id = initial_result.intent_id
                return polled
            logger.debug(
                "[BETTING] Poll %d/%d for %s: still %s",
                attempt + 1, max_polls, exchange_oid, polled.status.value,
            )

        logger.info(
            "[BETTING] Order %s still pending after %d polls",
            exchange_oid, max_polls,
        )
        return initial_result

    # ── DB persistence ────────────────────────────────────────────────

    def _save_prediction(
        self,
        tick_ts: datetime,
        market_id: str,
        source: str,
        p_yes: float,
        yes_ask: float,
        no_ask: float,
    ) -> int | None:
        if self._engine is None:
            return None

        from .db import get_session
        from .db_schema import BettingPrediction

        now = datetime.now(UTC)
        row = BettingPrediction(
            instance_name=self.instance_name,
            tick_ts=tick_ts,
            market_id=market_id,
            source=source,
            p_yes=p_yes,
            yes_ask=yes_ask,
            no_ask=no_ask,
            created_at=now,
        )
        try:
            with get_session(self._engine) as session:
                session.add(row)
                session.flush()
                return row.id
        except Exception as e:
            logger.warning("Failed to persist prediction: %s", e, exc_info=True)
            return None

    def _save_signal(
        self,
        prediction_id: int | None,
        signal: BetSignal,
    ) -> int | None:
        if self._engine is None or prediction_id is None:
            return None

        from .db import get_session
        from .db_schema import BettingSignal

        now = datetime.now(UTC)
        metadata_json = json.dumps(signal.metadata) if signal.metadata else None
        row = BettingSignal(
            instance_name=self.instance_name,
            prediction_id=prediction_id,
            strategy_name=self.strategy.name,
            side=signal.side,
            shares=signal.shares,
            price=signal.price,
            cost=signal.cost,
            metadata_json=metadata_json,
            created_at=now,
        )
        try:
            with get_session(self._engine) as session:
                session.add(row)
                session.flush()
                return row.id
        except Exception as e:
            logger.warning("Failed to persist signal: %s", e, exc_info=True)
            return None

    def _save_order(
        self,
        signal_id: int | None,
        order_id: str,
        ticker: str,
        side: str,
        count: int,
        price_cents: int,
        status: str,
        filled_shares: float,
        fill_price: float,
        fee_paid: float,
        exchange_order_id: str | None,
        action: str = "BUY",
    ) -> None:
        if self._engine is None:
            return

        from .db import get_session
        from .db_schema import BettingOrder

        now = datetime.now(UTC)
        row = BettingOrder(
            instance_name=self.instance_name,
            signal_id=signal_id,
            order_id=order_id,
            ticker=ticker,
            side=side,
            action=action,
            count=count,
            price_cents=price_cents,
            status=status,
            filled_shares=filled_shares,
            fill_price=fill_price,
            fee_paid=fee_paid,
            exchange_order_id=exchange_order_id,
            dry_run=self.dry_run,
            created_at=now,
        )
        try:
            with get_session(self._engine) as session:
                session.add(row)
        except Exception as e:
            logger.warning("Failed to persist order: %s", e, exc_info=True)

    # ── query helpers ─────────────────────────────────────────────────

    def get_recent_predictions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent predictions from the DB."""
        if self._engine is None:
            return []

        from .db import get_session
        from .db_schema import BettingPrediction

        with get_session(self._engine) as session:
            rows = (
                session.query(BettingPrediction)
                .filter(BettingPrediction.instance_name == self.instance_name)
                .order_by(BettingPrediction.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "tick_ts": row.tick_ts.isoformat(),
                    "market_id": row.market_id,
                    "source": row.source,
                    "p_yes": row.p_yes,
                    "yes_ask": row.yes_ask,
                    "no_ask": row.no_ask,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def get_recent_orders(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent betting orders from the DB."""
        if self._engine is None:
            return []

        from .db import get_session
        from .db_schema import BettingOrder

        with get_session(self._engine) as session:
            rows = (
                session.query(BettingOrder)
                .order_by(BettingOrder.created_at.desc())
                .limit(limit)
                .all()
            )
            return [
                {
                    "id": row.id,
                    "order_id": row.order_id,
                    "ticker": row.ticker,
                    "side": row.side,
                    "count": row.count,
                    "price_cents": row.price_cents,
                    "status": row.status,
                    "filled_shares": row.filled_shares,
                    "fill_price": row.fill_price,
                    "fee_paid": row.fee_paid,
                    "dry_run": row.dry_run,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]
