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
from .db import get_session  # Re-exported for tests and legacy patch points.
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
    from position_replay import (
        load_replayable_orders,
        replay_orders_by_ticker,
        summarize_replayed_positions,
    )
    _position_replay_cache = (
        load_replayable_orders,
        replay_orders_by_ticker,
        summarize_replayed_positions,
    )
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

        IMPORTANT: Position replay counts only executed quantity.
        Untouched PENDING orders do not affect holdings, but partially filled
        pending orders do count for their filled_shares. Pending orders are
        cancelled before placing new orders.
        """
        if self._engine is None:
            return None, 0, Decimal(str(self.starting_cash))
        try:
            from .db import get_session
            from .db_schema import BettingOrder
            (
                load_replayable_orders,
                replay_orders_by_ticker,
                summarize_replayed_positions,
            ) = _get_position_replay()

            with get_session(self._engine) as session:
                # Replay only executed quantity. This still excludes untouched
                # pending orders, but it includes the filled portion of partially
                # filled orders so live position checks don't drift mid-fill.
                orders = load_replayable_orders(session, BettingOrder, self.instance_name)

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

            # Enhancement: In LIVE mode, ALWAYS use Kalshi as the source of truth
            if not self.dry_run:
                kalshi_position = self._verify_position_with_kalshi(ticker)
                if kalshi_position is not None:
                    # Use Kalshi's position as ground truth
                    kalshi_side = "yes" if kalshi_position >= 0 else "no"
                    kalshi_qty = abs(kalshi_position)

                    # Auto-correct DB if there's a mismatch
                    pos = positions.get(ticker)
                    if pos is not None:
                        side, qty, _ = pos.current_position()
                        if side != kalshi_side or abs(qty - kalshi_qty) > 0.001:
                            logger.error(
                                "[BETTING] CRITICAL: Position mismatch for %s: DB=%s:%d, Kalshi=%s:%d - AUTO-CORRECTING",
                                ticker, side, qty, kalshi_side, kalshi_qty
                            )
                            # Immediately sync this position to DB
                            self._force_sync_position(ticker, kalshi_side, kalshi_qty)

                    # ALWAYS use Kalshi's position, it's the only truth
                    return kalshi_side if kalshi_qty > 0 else None, max(0, round(kalshi_qty)), cash

            pos = positions.get(ticker)
            if pos is None:
                return None, 0, cash
            side, qty, _ = pos.current_position()
            return side, max(0, round(qty)), cash
        except Exception as e:
            logger.warning("[BETTING] _live_ledger_state query failed for %s: %s", ticker, e)
            return None, 0, Decimal("0")

    def _verify_position_with_kalshi(self, ticker: str) -> float | None:
        """Quick poll to verify exact position from Kalshi before trading.

        Returns signed quantity: positive for YES, negative for NO, or None if error.
        """
        try:
            positions = self._get_adapter().get_positions()
            for pos in positions:
                if pos.get("ticker") == ticker:
                    return float(pos.get("position_fp", 0) or 0)
            return 0.0  # No position found means zero position
        except Exception as e:
            logger.warning("[BETTING] Failed to verify position with Kalshi for %s: %s", ticker, e)
            return None

    def _force_sync_position(self, ticker: str, kalshi_side: str, kalshi_qty: float) -> None:
        """Force immediate position sync when mismatch detected.

        This ensures our DB matches Kalshi's truth immediately, not at next sync.
        """
        if self._engine is None:
            return

        try:
            import sys, os
            _services = os.path.join(os.path.dirname(__file__), "../../../../services")
            if _services not in sys.path:
                sys.path.insert(0, _services)
            from kalshi_state import record_kalshi_state, sync_trading_positions_from_snapshots
            from ai_prophet_core.betting.db import get_session

            with get_session(self._engine) as session:
                # Record current Kalshi state
                record_kalshi_state(session, self._get_adapter(), self.instance_name)
                # Immediately sync positions from the snapshots
                updated = sync_trading_positions_from_snapshots(session, self.instance_name)

                if updated > 0:
                    logger.info(
                        "[BETTING] Force-synced %d position(s) for %s to match Kalshi truth",
                        updated, ticker
                    )

                    # Also log this critical event
                    from db_models import SystemLog
                    session.add(SystemLog(
                        instance_name=self.instance_name,
                        level="ERROR",
                        message=f"AUTO-CORRECTED position for {ticker}: now {kalshi_side}:{kalshi_qty}",
                        component="position_sync",
                        created_at=datetime.now(UTC),
                    ))
                    session.commit()

        except Exception as e:
            logger.error("[BETTING] Failed to force sync position for %s: %s", ticker, e)

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
        signal_metadata = signal.metadata or {}

        def _metadata_count(key: str) -> int | None:
            raw = signal_metadata.get(key)
            if raw is None:
                return None
            try:
                return max(0, round(abs(float(raw)) * 100))
            except (TypeError, ValueError):
                return None

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
                intended_sell_count = _metadata_count("sell_portion")
                intended_buy_count = _metadata_count("buy_portion")
                if intended_sell_count is None:
                    intended_sell_count = held_count
                intended_sell_count = min(held_count, intended_sell_count)
                if intended_buy_count is None:
                    intended_buy_count = count
                # When selling, use the opposite side's ask as an approximation of our bid
                # (Since we don't have bid prices, this is the best we can do)
                # If selling YES, use (1 - no_ask) as the YES bid
                # If selling NO, use (1 - yes_ask) as the NO bid
                if held_side == "yes":
                    sell_price = max(0.01, 1.0 - no_ask)  # YES bid ≈ 1 - NO ask
                else:
                    sell_price = max(0.01, 1.0 - yes_ask)  # NO bid ≈ 1 - YES ask
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
                elif intended_sell_count <= 0:
                    action = "BUY"
                    effective_side = want_side.upper()
                    count = intended_buy_count
                else:
                    # Rebalancing signals can contain separate sell-down and
                    # buy-on-new-side portions. Respect those exact portions
                    # rather than force-flipping the full opposite position.
                    from .adapters.base import OrderStatus

                    sell_order_id = str(uuid.uuid4())
                    sell_req = OrderRequest(
                        order_id=sell_order_id,
                        intent_id=f"net-sell-{sell_order_id[:8]}",
                        market_id=market_id,
                        exchange_ticker=ticker,
                        action="SELL",
                        side=held_side.upper(),
                        shares=Decimal(str(intended_sell_count)),
                        limit_price=Decimal(str(sell_price)),
                    )
                    sell_status = "FILLED"
                    sell_filled_shares = 0.0
                    sell_fill_price = sell_price
                    sell_exchange_oid = None
                    try:
                        sell_result = adapter.submit_order(sell_req)
                        if (
                            sell_result.status == OrderStatus.PENDING
                            and sell_result.exchange_order_id
                            and not self.dry_run
                        ):
                            sell_result = self._poll_order_status(
                                adapter,
                                sell_result,
                                fallback_request=sell_req,
                            )
                        sell_status = sell_result.status.value
                        sell_filled_shares = float(sell_result.filled_shares)
                        sell_fill_price = float(sell_result.fill_price)
                        sell_exchange_oid = sell_result.exchange_order_id
                        raw_sell_fee = getattr(sell_result, "fee", 0)
                        try:
                            sell_fee_paid = float(raw_sell_fee or 0)
                        except (TypeError, ValueError):
                            sell_fee_paid = 0.0
                        # Enhanced logging for position flips
                        logger.info(
                            "[BETTING] POSITION FLIP STEP 1/2: SELL %d %s on %s @ $%.2f → %s",
                            intended_sell_count, held_side.upper(), ticker, sell_price, sell_status,
                        )
                        self._save_order(
                            signal_id=None,  # NET sells are not driven by a signal for this market
                            order_id=sell_order_id,
                            ticker=ticker,
                            side=held_side,
                            count=intended_sell_count,
                            price_cents=sell_price_cents,
                            status=sell_status,
                            filled_shares=sell_filled_shares,
                            fill_price=sell_fill_price,
                            fee_paid=sell_fee_paid,
                            exchange_order_id=sell_exchange_oid,
                            action="SELL",
                        )
                    except Exception as e:
                        logger.error("[BETTING] NET sell failed: %s", e)
                        sell_status = "ERROR"

                    # If sell failed, don't continue with the buy
                    if sell_status == "ERROR":
                        return BetResult(
                            market_id=market_id,
                            signal=signal,
                            order_placed=False,
                            order_id=sell_order_id,
                            status=sell_status,
                        )

                    if sell_status not in {"FILLED", "DRY_RUN"}:
                        self._save_deferred_flip(
                            signal_id=signal_id,
                            market_id=market_id,
                            ticker=ticker,
                            sell_order_id=sell_order_id,
                            buy_side=want_side.upper(),
                            buy_count=intended_buy_count,
                            buy_price_cents=max(1, min(99, round(signal.price * 100))),
                        )
                        logger.info(
                            "[BETTING] POSITION FLIP PAUSED: SELL %s is %s (filled=%s/%s); will not BUY %s until sell resolves",
                            ticker,
                            sell_status,
                            sell_filled_shares,
                            intended_sell_count,
                            want_side.upper(),
                        )
                        return BetResult(
                            market_id=market_id,
                            signal=signal,
                            order_placed=sell_status not in {"CANCELLED", "REJECTED"},
                            order_id=sell_order_id,
                            status=sell_status,
                            filled_shares=sell_filled_shares,
                            fill_price=sell_fill_price,
                            fee_paid=sell_fee_paid,
                            exchange_order_id=sell_exchange_oid,
                        )

                    if intended_buy_count <= 0:
                        return BetResult(
                            market_id=market_id,
                            signal=signal,
                            order_placed=True,
                            order_id=sell_order_id,
                            status=sell_status,
                        )

                    # Continue with only the intended buy portion on the new side.
                    logger.info(
                        "[BETTING] POSITION FLIP STEP 2/2: Will BUY %d %s to complete flip",
                        intended_buy_count, want_side.upper(),
                    )
                    action = "BUY"
                    effective_side = want_side.upper()
                    count = intended_buy_count
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
                    adapter,
                    order_result,
                    fallback_request=order_req,
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

        # Check if this was part of a position flip
        is_flip_completion = (
            action == "BUY" and
            signal.side.lower() != (live_side or "").lower() and
            live_side is not None
        )

        if is_flip_completion:
            logger.info(
                "[BETTING] POSITION FLIP COMPLETE: Order %s: %s %s %s×%s @ %sc → %s (filled=%s @ %s, fee=%s)",
                order_id[:8], action, effective_side, count, ticker,
                price_cents, status, filled_shares, fill_price, fee_paid,
            )
        else:
            logger.info(
                "[BETTING] Order %s: %s %s %s×%s @ %sc → %s (filled=%s @ %s, fee=%s)",
                order_id[:8], action, effective_side, count, ticker,
                price_cents, status, filled_shares, fill_price, fee_paid,
            )

        # Enhancement: Verify balance after fills in LIVE mode
        if not self.dry_run and status == "FILLED" and action == "BUY":
            self._verify_balance_after_fill(
                ticker=ticker,
                order_id=order_id,
                expected_cost=filled_shares * fill_price + fee_paid,
                pre_order_balance=float(live_cash)
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

    def _verify_balance_after_fill(
        self,
        ticker: str,
        order_id: str,
        expected_cost: float,
        pre_order_balance: float
    ) -> None:
        """Verify balance changed correctly after a filled order.

        Balance discrepancies should NOT exist - if they do, it's a CRITICAL error.
        Only called for FILLED BUY orders in LIVE mode.
        """
        try:
            # Poll balance multiple times to ensure Kalshi has updated
            max_retries = 3
            new_balance = None

            for retry in range(max_retries):
                time.sleep(1.0 if retry == 0 else 2.0)  # Wait 1s first, then 2s between retries
                new_balance = float(self._get_adapter().get_balance())

                # Expected balance = pre-order balance - order cost
                expected_balance = pre_order_balance - expected_cost
                discrepancy = abs(new_balance - expected_balance)

                if discrepancy <= 0.01:
                    # Balance is correct!
                    logger.info(
                        "[BETTING] Balance VERIFIED after order %s: $%.2f → $%.2f (cost=$%.2f)",
                        order_id[:8], pre_order_balance, new_balance, expected_cost
                    )
                    return

                if retry < max_retries - 1:
                    logger.debug(
                        "[BETTING] Balance check %d/%d: Expected $%.2f, got $%.2f - retrying...",
                        retry + 1, max_retries, expected_balance, new_balance
                    )

            # CRITICAL ERROR - Balance mismatch after all retries
            logger.error(
                "[BETTING] CRITICAL BALANCE ERROR after order %s for %s: "
                "Expected $%.2f (%.2f - %.2f), got $%.2f (DISCREPANCY=$%.2f). "
                "This should NOT happen with proper sync!",
                order_id[:8], ticker,
                expected_balance, pre_order_balance, expected_cost,
                new_balance, abs(new_balance - expected_balance)
            )

            # Force immediate full reconciliation
            self._force_full_reconciliation(
                ticker=ticker,
                order_id=order_id,
                expected_balance=expected_balance,
                actual_balance=new_balance,
                discrepancy=abs(new_balance - expected_balance)
            )

        except Exception as e:
            logger.error(
                "[BETTING] CRITICAL: Failed to verify balance after order %s: %s. "
                "Manual intervention may be required!",
                order_id[:8], e
            )

    def _force_full_reconciliation(
        self,
        ticker: str,
        order_id: str,
        expected_balance: float,
        actual_balance: float,
        discrepancy: float
    ) -> None:
        """Force a full reconciliation when balance discrepancy detected.

        This is a CRITICAL operation - balance mismatches should never happen.
        """
        if self._engine is None:
            return

        try:
            import sys, os
            _services = os.path.join(os.path.dirname(__file__), "../../../../services")
            if _services not in sys.path:
                sys.path.insert(0, _services)
            from kalshi_state import record_kalshi_state
            from order_management import reconcile_positions_with_kalshi
            from db_models import SystemLog
            from ai_prophet_core.betting.db import get_session

            logger.error("[BETTING] FORCING FULL RECONCILIATION due to balance discrepancy")

            # 1. Record complete Kalshi state
            with get_session(self._engine) as session:
                record_kalshi_state(session, self._get_adapter(), self.instance_name)

            # 2. Full position reconciliation
            drifts = reconcile_positions_with_kalshi(
                self._engine,
                self._get_adapter(),
                self.instance_name,
                tolerance_contracts=0,  # Zero tolerance during critical reconciliation
                sync_pending_orders=True
            )

            # 3. Log critical event
            with get_session(self._engine) as session:
                session.add(SystemLog(
                    instance_name=self.instance_name,
                    level="CRITICAL",
                    message=(
                        f"BALANCE DISCREPANCY DETECTED: Expected ${expected_balance:.2f}, "
                        f"got ${actual_balance:.2f} (diff=${discrepancy:.2f}) after order {order_id[:8]} "
                        f"for {ticker}. Full reconciliation performed. Found {len(drifts)} position drifts."
                    ),
                    component="balance_reconciliation",
                    created_at=datetime.now(UTC),
                ))
                session.commit()

            # 4. Stop trading if discrepancy is too large (> $10)
            if discrepancy > 10.0:
                logger.critical(
                    "[BETTING] EMERGENCY: Balance discrepancy > $10. "
                    "DISABLING TRADING. Manual intervention required!"
                )
                self.enabled = False  # Disable engine

                with get_session(self._engine) as session:
                    session.add(SystemLog(
                        instance_name=self.instance_name,
                        level="EMERGENCY",
                        message=f"TRADING DISABLED due to ${discrepancy:.2f} balance discrepancy",
                        component="emergency_stop",
                        created_at=datetime.now(UTC),
                    ))
                    session.commit()

        except Exception as e:
            logger.critical(
                "[BETTING] CRITICAL: Failed to perform reconciliation: %s. "
                "MANUAL INTERVENTION REQUIRED!",
                e
            )

    def _poll_order_status(
        self,
        adapter,
        initial_result,
        *,
        fallback_request=None,
        max_polls: int = 10,  # Increased from 5
        interval_sec: float = 1.0,  # Start with 1 second
    ):
        """Poll exchange for order fill status after a PENDING submission.

        Uses exponential backoff: 1s, 1s, 2s, 3s, 5s, 8s, 13s, 20s, 30s, 45s
        Total time: ~130 seconds for all 10 polls
        """
        from .adapters.base import OrderStatus

        exchange_oid = initial_result.exchange_order_id

        # Enhanced polling with exponential backoff
        poll_intervals = [1, 1, 2, 3, 5, 8, 13, 20, 30, 45]  # Fibonacci-like sequence

        for attempt in range(min(max_polls, len(poll_intervals))):
            # Use exponential backoff for polling intervals
            sleep_time = poll_intervals[attempt] if attempt < len(poll_intervals) else interval_sec
            time.sleep(sleep_time)

            try:
                polled = adapter.get_order(exchange_oid, fallback_request=fallback_request)
            except Exception as e:
                logger.warning(
                    "[BETTING] Poll %d/%d failed for %s: %s",
                    attempt + 1, max_polls, exchange_oid, e
                )
                continue

            if polled is None:
                logger.debug(
                    "[BETTING] Poll %d/%d for %s returned None (order may not exist)",
                    attempt + 1, max_polls, exchange_oid
                )
                continue

            # Check for terminal states
            if polled.status in (
                OrderStatus.FILLED,
                OrderStatus.CANCELLED,
                OrderStatus.REJECTED,
            ):
                # Preserve original order/intent IDs
                polled.order_id = initial_result.order_id
                polled.intent_id = initial_result.intent_id

                # Log successful resolution
                if polled.status == OrderStatus.FILLED:
                    logger.info(
                        "[BETTING] Order %s FILLED after %d polls (%.1f seconds)",
                        exchange_oid, attempt + 1, sum(poll_intervals[:attempt+1])
                    )
                else:
                    logger.info(
                        "[BETTING] Order %s %s after %d polls",
                        exchange_oid, polled.status.value, attempt + 1
                    )
                return polled

            # Check for partial fills
            if polled.filled_shares and float(polled.filled_shares) > 0:
                logger.info(
                    "[BETTING] Poll %d/%d for %s: PARTIAL FILL %d shares, status=%s",
                    attempt + 1, max_polls, exchange_oid,
                    int(polled.filled_shares), polled.status.value
                )
                # Update the initial result with partial fill info
                initial_result.filled_shares = polled.filled_shares
                initial_result.fill_price = polled.fill_price
                initial_result.fee = polled.fee
            else:
                logger.debug(
                    "[BETTING] Poll %d/%d for %s: still %s (next poll in %ds)",
                    attempt + 1, max_polls, exchange_oid, polled.status.value,
                    poll_intervals[attempt + 1] if attempt + 1 < len(poll_intervals) else interval_sec
                )

        # Final status check and warning
        total_time = sum(poll_intervals[:max_polls])
        logger.warning(
            "[BETTING] Order %s still PENDING after %d polls (%.1f seconds total). "
            "Will continue monitoring in background sync.",
            exchange_oid, max_polls, total_time
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

    def _save_deferred_flip(
        self,
        *,
        signal_id: int | None,
        market_id: str,
        ticker: str,
        sell_order_id: str,
        buy_side: str,
        buy_count: int,
        buy_price_cents: int,
    ) -> None:
        if self._engine is None or signal_id is None or buy_count <= 0:
            return

        from .db import get_session
        from .db_schema import BettingDeferredFlip

        now = datetime.now(UTC)
        try:
            with get_session(self._engine) as session:
                row = (
                    session.query(BettingDeferredFlip)
                    .filter(
                        BettingDeferredFlip.instance_name == self.instance_name,
                        BettingDeferredFlip.signal_id == signal_id,
                    )
                    .one_or_none()
                )
                if row is None:
                    row = BettingDeferredFlip(
                        instance_name=self.instance_name,
                        signal_id=signal_id,
                        market_id=market_id,
                        ticker=ticker,
                        sell_order_id=sell_order_id,
                        buy_side=buy_side,
                        buy_count=buy_count,
                        buy_price_cents=buy_price_cents,
                        status="WAITING_SELL",
                        buy_order_id=None,
                        last_error=None,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                else:
                    row.market_id = market_id
                    row.ticker = ticker
                    row.sell_order_id = sell_order_id
                    row.buy_side = buy_side
                    row.buy_count = buy_count
                    row.buy_price_cents = buy_price_cents
                    row.status = "WAITING_SELL"
                    row.buy_order_id = None
                    row.last_error = None
                    row.updated_at = now
        except Exception as e:
            logger.warning("Failed to persist deferred flip: %s", e, exc_info=True)

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
