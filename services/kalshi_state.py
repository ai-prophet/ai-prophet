"""Shared helpers for recording and reading Kalshi-backed account truth."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import func

from db_models import (
    KalshiBalanceSnapshot,
    KalshiOrderSnapshot,
    KalshiPositionSnapshot,
    TradingMarket,
    TradingPosition,
)
from position_replay import InventoryPosition, replay_orders_by_ticker


def _to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_ts(value: Any, fallback: datetime | None = None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return fallback
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=UTC)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return fallback
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(cleaned)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return fallback
    return fallback


def _snapshot_order_key(order_id: str, last_update_ts: datetime) -> tuple[str, datetime]:
    normalized = _parse_ts(last_update_ts, fallback=last_update_ts) or last_update_ts
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=UTC)
    return order_id, normalized.astimezone(UTC)


def _normalize_order_status(status: str | None) -> str:
    raw = (status or "").strip().lower()
    if raw in {"resting", "pending"}:
        return "PENDING"
    if raw in {"executed", "filled"}:
        return "FILLED"
    if raw in {"canceled", "cancelled"}:
        return "CANCELLED"
    if raw in {"rejected", "rejection"}:
        return "REJECTED"
    return raw.upper() if raw else "UNKNOWN"


def _order_limit_price(order: dict[str, Any]) -> float | None:
    side = (order.get("side") or "").strip().lower()
    if side == "yes":
        price = _to_float(order.get("yes_price_dollars"), default=-1)
        return price if price >= 0 else None
    if side == "no":
        price = _to_float(order.get("no_price_dollars"), default=-1)
        return price if price >= 0 else None
    return None


def _order_avg_fill_price(order: dict[str, Any]) -> float | None:
    fill_count = _to_float(order.get("fill_count_fp"))
    if fill_count <= 0:
        return None

    total_fill_cost = _to_float(order.get("taker_fill_cost_dollars")) + _to_float(order.get("maker_fill_cost_dollars"))
    if total_fill_cost > 0:
        return total_fill_cost / fill_count

    limit_price = _order_limit_price(order)
    return limit_price if limit_price is not None and limit_price > 0 else None


def _order_fee_paid(order: dict[str, Any]) -> float:
    return _to_float(order.get("taker_fees_dollars")) + _to_float(order.get("maker_fees_dollars"))


def _last_traded_unit_value(contract: str, last_price: float | None) -> float | None:
    if last_price is None:
        return None
    if contract.lower() == "yes":
        return last_price
    return 1.0 - last_price


def record_kalshi_state(session, adapter, instance_name: str, *, snapshot_ts: datetime | None = None) -> dict[str, int]:
    """Persist append-only Kalshi balance/position/order snapshots for one sync cycle."""
    snapshot_ts = snapshot_ts or datetime.now(UTC)
    results = {"balances": 0, "positions": 0, "orders": 0}

    balance_data = adapter.get_balance_details()
    session.add(
        KalshiBalanceSnapshot(
            instance_name=instance_name,
            balance=_to_float(balance_data.get("balance")) / 100.0,
            portfolio_value=_to_float(balance_data.get("portfolio_value")) / 100.0,
            updated_ts=_parse_ts(balance_data.get("updated_ts")),
            snapshot_ts=snapshot_ts,
            raw_json=json.dumps(balance_data),
        )
    )
    results["balances"] += 1

    positions = adapter.get_positions()
    for raw_pos in positions:
        ticker = raw_pos.get("ticker")
        if not ticker:
            continue
        signed_quantity = _to_float(raw_pos.get("position_fp"))
        session.add(
            KalshiPositionSnapshot(
                instance_name=instance_name,
                ticker=ticker,
                market_id=f"kalshi:{ticker}",
                side="yes" if signed_quantity >= 0 else "no",
                signed_quantity=signed_quantity,
                quantity=abs(signed_quantity),
                market_exposure=_to_float(raw_pos.get("market_exposure_dollars")),
                realized_pnl=_to_float(raw_pos.get("realized_pnl_dollars")),
                fees_paid=_to_float(raw_pos.get("fees_paid_dollars")),
                total_cost=_to_float(raw_pos.get("total_cost_dollars"), default=0.0) or None,
                total_cost_shares=_to_float(raw_pos.get("total_cost_shares_fp"), default=0.0) or None,
                total_traded=_to_float(raw_pos.get("total_traded_dollars"), default=0.0) or None,
                resting_orders_count=int(round(_to_float(raw_pos.get("resting_orders_count")))),
                snapshot_ts=snapshot_ts,
                raw_json=json.dumps(raw_pos),
            )
        )
        results["positions"] += 1

    active_tickers = {
        pos.get("ticker")
        for pos in positions
        if pos.get("ticker") and abs(_to_float(pos.get("position_fp"))) > 1e-9
    }

    order_payloads: list[tuple[str, dict[str, Any]]] = []
    for status in ("resting", "executed", "canceled"):
        for order in adapter.get_orders(status=status):
            if order.get("order_id"):
                order_payloads.append(("portfolio", order))
                if order.get("ticker"):
                    active_tickers.add(order["ticker"])

    for ticker in sorted(t for t in active_tickers if t):
        for order in adapter.get_historical_orders(ticker=ticker):
            if order.get("order_id"):
                order_payloads.append(("historical", order))

    candidate_keys = {
        _snapshot_order_key(
            raw_order.get("order_id"),
            _parse_ts(
                raw_order.get("last_update_time"),
                fallback=_parse_ts(raw_order.get("created_time"), fallback=snapshot_ts),
            ) or snapshot_ts,
        )
        for _, raw_order in order_payloads
        if raw_order.get("order_id")
    }
    order_ids = sorted({order_id for order_id, _ in candidate_keys if order_id})
    existing_keys: set[tuple[str, datetime]] = set()
    if order_ids:
        existing_rows = (
            session.query(KalshiOrderSnapshot.order_id, KalshiOrderSnapshot.last_update_ts)
            .filter(
                KalshiOrderSnapshot.instance_name == instance_name,
                KalshiOrderSnapshot.order_id.in_(order_ids),
            )
            .all()
        )
        existing_keys = {
            _snapshot_order_key(order_id, last_update_ts)
            for order_id, last_update_ts in existing_rows
        }

    seen: set[tuple[str, datetime]] = set()
    for source, raw_order in order_payloads:
        order_id = raw_order.get("order_id")
        if not order_id:
            continue
        last_update_ts = _parse_ts(
            raw_order.get("last_update_time"),
            fallback=_parse_ts(raw_order.get("created_time"), fallback=snapshot_ts),
        ) or snapshot_ts
        dedupe_key = _snapshot_order_key(order_id, last_update_ts)
        if dedupe_key in seen:
            continue
        if dedupe_key in existing_keys:
            continue
        seen.add(dedupe_key)

        session.add(
            KalshiOrderSnapshot(
                instance_name=instance_name,
                order_id=order_id,
                client_order_id=raw_order.get("client_order_id"),
                ticker=raw_order.get("ticker") or "",
                action=((raw_order.get("action") or "").upper() or "BUY"),
                side=((raw_order.get("side") or "").upper() or "YES"),
                status=_normalize_order_status(raw_order.get("status")),
                initial_count=_to_float(raw_order.get("initial_count_fp")),
                fill_count=_to_float(raw_order.get("fill_count_fp")),
                remaining_count=_to_float(raw_order.get("remaining_count_fp")),
                limit_price=_order_limit_price(raw_order),
                avg_fill_price=_order_avg_fill_price(raw_order),
                fee_paid=_order_fee_paid(raw_order),
                source=source,
                created_ts=_parse_ts(raw_order.get("created_time")),
                last_update_ts=last_update_ts,
                captured_at=snapshot_ts,
                raw_json=json.dumps(raw_order),
            )
        )
        results["orders"] += 1

    session.commit()
    return results


def get_latest_balance_snapshot(session, instance_name: str) -> KalshiBalanceSnapshot | None:
    return (
        session.query(KalshiBalanceSnapshot)
        .filter(KalshiBalanceSnapshot.instance_name == instance_name)
        .order_by(KalshiBalanceSnapshot.snapshot_ts.desc(), KalshiBalanceSnapshot.id.desc())
        .first()
    )


def get_latest_position_snapshots(session, instance_name: str) -> dict[str, KalshiPositionSnapshot]:
    subquery = (
        session.query(
            KalshiPositionSnapshot.ticker.label("ticker"),
            func.max(KalshiPositionSnapshot.snapshot_ts).label("max_snapshot_ts"),
        )
        .filter(KalshiPositionSnapshot.instance_name == instance_name)
        .group_by(KalshiPositionSnapshot.ticker)
        .subquery()
    )

    rows = (
        session.query(KalshiPositionSnapshot)
        .join(
            subquery,
            (KalshiPositionSnapshot.ticker == subquery.c.ticker)
            & (KalshiPositionSnapshot.snapshot_ts == subquery.c.max_snapshot_ts),
        )
        .filter(KalshiPositionSnapshot.instance_name == instance_name)
        .all()
    )
    return {row.ticker: row for row in rows}


def get_latest_order_snapshots(
    session,
    instance_name: str,
    *,
    tickers: Iterable[str] | None = None,
) -> list[KalshiOrderSnapshot]:
    ticker_list = sorted({ticker for ticker in (tickers or []) if ticker})
    query = session.query(KalshiOrderSnapshot).filter(KalshiOrderSnapshot.instance_name == instance_name)
    if tickers is not None:
        if not ticker_list:
            return []
        query = query.filter(KalshiOrderSnapshot.ticker.in_(ticker_list))

    rows = query.order_by(
        KalshiOrderSnapshot.last_update_ts.desc(),
        KalshiOrderSnapshot.captured_at.desc(),
        KalshiOrderSnapshot.id.desc(),
    ).all()

    latest: dict[str, KalshiOrderSnapshot] = {}
    for row in rows:
        if row.order_id not in latest:
            latest[row.order_id] = row
    return list(latest.values())


def build_latest_order_activity_by_ticker(
    session,
    instance_name: str,
    *,
    tickers: Iterable[str] | None = None,
    min_created_ts: datetime | None = None,
) -> tuple[dict[str, str], dict[str, int]]:
    latest_order_snaps = get_latest_order_snapshots(session, instance_name, tickers=tickers)
    latest_order_time_by_ticker: dict[str, str] = {}
    order_count_by_ticker: dict[str, int] = {}

    for snap in latest_order_snaps:
        ts = snap.created_ts or snap.last_update_ts or snap.captured_at
        if ts is None:
            continue
        if min_created_ts is not None and ts < min_created_ts:
            continue
        order_count_by_ticker[snap.ticker] = order_count_by_ticker.get(snap.ticker, 0) + 1
        ts_iso = ts.isoformat()
        current = latest_order_time_by_ticker.get(snap.ticker)
        if current is None or ts_iso > current:
            latest_order_time_by_ticker[snap.ticker] = ts_iso

    return latest_order_time_by_ticker, order_count_by_ticker


@dataclass
class KalshiPositionView:
    market_id: str
    ticker: str
    contract: str
    quantity: float
    avg_price: float
    realized_pnl: float
    fees_paid: float
    total_cost: float
    market_exposure: float
    resting_orders_count: int
    updated_at: datetime


@dataclass
class KalshiPortfolioSummary:
    cash_balance: float
    cash_pnl: float
    open_value: float
    cash_spent: float
    net_pnl: float
    starting_total: float
    total_fees: float
    open_positions: int
    active_markets: int
    return_pct: float


def build_position_views(session, instance_name: str) -> list[KalshiPositionView]:
    latest_positions = get_latest_position_snapshots(session, instance_name)
    if not latest_positions:
        return []

    latest_orders = get_latest_order_snapshots(session, instance_name, tickers=latest_positions.keys())
    replay_rows = sorted(
        (
            _ReplayableKalshiOrder(
                ticker=row.ticker,
                action=row.action,
                side=row.side,
                filled_shares=row.fill_count,
                fill_price=row.avg_fill_price or row.limit_price or 0.0,
                fee_paid=row.fee_paid,
                created_at=row.created_ts or row.last_update_ts or row.captured_at,
            )
            for row in latest_orders
            if row.fill_count > 0
        ),
        key=lambda row: row.created_at,
    )
    replayed_positions = replay_orders_by_ticker(replay_rows)

    views: list[KalshiPositionView] = []
    for ticker, snap in sorted(latest_positions.items()):
        qty = abs(snap.signed_quantity)
        if qty <= 1e-9:
            continue

        contract = "yes" if snap.signed_quantity >= 0 else "no"
        avg_price = None
        total_cost = snap.total_cost or 0.0
        if snap.total_cost is not None and snap.total_cost_shares and snap.total_cost_shares > 0:
            avg_price = snap.total_cost / snap.total_cost_shares
        else:
            replayed = replayed_positions.get(ticker)
            if replayed:
                replay_side, replay_qty, replay_avg = replayed.current_position()
                if replay_side == contract and abs(replay_qty - qty) <= 0.001 and replay_avg > 0:
                    avg_price = replay_avg
                    total_cost = replay_avg * qty

        if avg_price is None:
            avg_price = 0.5
            if total_cost > 0 and qty > 0:
                avg_price = total_cost / qty

        views.append(
            KalshiPositionView(
                market_id=snap.market_id,
                ticker=ticker,
                contract=contract,
                quantity=qty,
                avg_price=avg_price,
                realized_pnl=snap.realized_pnl,
                fees_paid=snap.fees_paid,
                total_cost=total_cost if total_cost > 0 else avg_price * qty,
                market_exposure=snap.market_exposure,
                resting_orders_count=snap.resting_orders_count,
                updated_at=snap.snapshot_ts,
            )
        )

    return views


def build_portfolio_summary(
    session,
    instance_name: str,
    *,
    tickers: Iterable[str] | None = None,
    starting_total: float | None = None,
    prefer_synced_portfolio_value: bool = False,
) -> KalshiPortfolioSummary:
    visible_tickers = {ticker for ticker in (tickers or []) if ticker}
    position_views = build_position_views(session, instance_name)
    if visible_tickers:
        position_views = [view for view in position_views if view.ticker in visible_tickers]
    pending_by_ticker = build_pending_orders_by_ticker(
        session,
        instance_name,
        tickers=visible_tickers if visible_tickers else None,
    )
    latest_balance = get_latest_balance_snapshot(session, instance_name)

    market_map = {
        row.market_id: row
        for row in (
            session.query(TradingMarket)
            .filter(
                TradingMarket.instance_name == instance_name,
                TradingMarket.market_id.in_([view.market_id for view in position_views]),
            )
            .all()
        )
    } if position_views else {}

    cash_balance = float(latest_balance.balance) if latest_balance else 0.0
    cash_pnl = sum(float(view.realized_pnl or 0.0) for view in position_views)
    open_value = 0.0
    for view in position_views:
        market = market_map.get(view.market_id)
        unit_value = _last_traded_unit_value(view.contract, market.last_price if market else None)
        if unit_value is None:
            open_value += float(view.market_exposure or 0.0)
        else:
            open_value += unit_value * float(view.quantity or 0.0)
    if prefer_synced_portfolio_value and latest_balance is not None and latest_balance.portfolio_value is not None:
        open_value = float(latest_balance.portfolio_value or 0.0)
    cash_spent = sum(float(view.total_cost or 0.0) for view in position_views)
    total_fees = sum(float(view.fees_paid or 0.0) for view in position_views)
    baseline_total = float(starting_total or 0.0)
    if baseline_total > 1e-9:
        # When we have an explicit display baseline, the headline P&L should
        # reconcile to current account equity rather than a partial cost-basis
        # decomposition that may include a different history window.
        net_pnl = (cash_balance + open_value) - baseline_total
    else:
        net_pnl = open_value - cash_spent + cash_pnl
    open_positions = len(position_views)
    active_markets = len({
        *[view.ticker for view in position_views if view.quantity > 1e-9],
        *[ticker for ticker, orders in pending_by_ticker.items() if orders],
    })
    return_pct = (net_pnl / baseline_total) if baseline_total > 1e-9 else 0.0
    if not math.isfinite(return_pct):
        return_pct = 0.0

    return KalshiPortfolioSummary(
        cash_balance=cash_balance,
        cash_pnl=cash_pnl,
        open_value=open_value,
        cash_spent=cash_spent,
        net_pnl=net_pnl,
        starting_total=baseline_total,
        total_fees=total_fees,
        open_positions=open_positions,
        active_markets=active_markets,
        return_pct=return_pct,
    )


def build_pending_orders_by_ticker(
    session,
    instance_name: str,
    *,
    tickers: Iterable[str] | None = None,
    min_created_ts: datetime | None = None,
) -> dict[str, list[dict[str, Any]]]:
    latest_orders = get_latest_order_snapshots(session, instance_name, tickers=tickers)
    pending_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for order in latest_orders:
        if order.status != "PENDING" or order.remaining_count <= 0:
            continue
        created_at = order.created_ts or order.last_update_ts or order.captured_at
        if created_at is None:
            continue
        if min_created_ts is not None and created_at < min_created_ts:
            continue
        pending_by_ticker.setdefault(order.ticker, []).append(
            {
                "order_id": order.client_order_id or order.order_id,
                "exchange_order_id": order.order_id,
                "action": order.action,
                "side": order.side,
                "count": order.initial_count,
                "filled_shares": order.fill_count,
                "price_cents": int(round((order.limit_price or 0.0) * 100)),
                "created_at": created_at.isoformat(),
            }
        )
    for orders in pending_by_ticker.values():
        orders.sort(key=lambda order: order["created_at"], reverse=True)
    return pending_by_ticker


def sync_betting_orders_from_snapshots(session, betting_order_model, instance_name: str) -> int:
    """Bring local betting_orders into line with the latest Kalshi order states."""
    latest_orders = get_latest_order_snapshots(session, instance_name)
    by_exchange_id = {row.order_id: row for row in latest_orders}
    by_client_id = {row.client_order_id: row for row in latest_orders if row.client_order_id}

    updated = 0
    local_orders = (
        session.query(betting_order_model)
        .filter(betting_order_model.instance_name == instance_name)
        .all()
    )
    for row in local_orders:
        snap = None
        if getattr(row, "exchange_order_id", None):
            snap = by_exchange_id.get(row.exchange_order_id)
        if snap is None:
            snap = by_client_id.get(row.order_id)
        if snap is None:
            continue

        changed = False
        if (row.exchange_order_id or "") != snap.order_id:
            row.exchange_order_id = snap.order_id
            changed = True
        if row.status != snap.status:
            row.status = snap.status
            changed = True
        if abs(float(row.filled_shares or 0) - float(snap.fill_count or 0)) > 1e-6:
            row.filled_shares = float(snap.fill_count or 0)
            changed = True
        new_fill_price = float(snap.avg_fill_price or 0.0)
        if abs(float(row.fill_price or 0) - new_fill_price) > 1e-6:
            row.fill_price = new_fill_price
            changed = True
        if abs(float(row.fee_paid or 0) - float(snap.fee_paid or 0)) > 1e-6:
            row.fee_paid = float(snap.fee_paid or 0)
            changed = True
        if changed:
            updated += 1

    if updated > 0:
        session.commit()
    return updated


def sync_trading_positions_from_snapshots(session, instance_name: str) -> int:
    """Replace trading_positions with the latest Kalshi-backed position view."""
    latest_views = build_position_views(session, instance_name)
    desired_by_ticker = {view.ticker: view for view in latest_views}
    existing_rows = (
        session.query(TradingPosition)
        .filter(TradingPosition.instance_name == instance_name)
        .all()
    )
    existing_by_ticker = {
        (row.market_id.split("kalshi:", 1)[1] if row.market_id.startswith("kalshi:") else row.market_id): row
        for row in existing_rows
    }

    updated = 0
    target_tickers = set(existing_by_ticker.keys()) | set(desired_by_ticker.keys())
    for ticker in sorted(target_tickers):
        existing = existing_by_ticker.get(ticker)
        desired = desired_by_ticker.get(ticker)
        if desired is None:
            if existing is not None:
                session.delete(existing)
                updated += 1
            continue

        unrealized_pnl = max(0.0, desired.market_exposure - desired.total_cost)
        if existing is None:
            session.add(
                TradingPosition(
                    instance_name=instance_name,
                    market_id=desired.market_id,
                    contract=desired.contract,
                    quantity=desired.quantity,
                    avg_price=desired.avg_price,
                    realized_pnl=desired.realized_pnl,
                    unrealized_pnl=unrealized_pnl,
                    max_position=desired.quantity,
                    realized_trades=0,
                    updated_at=desired.updated_at,
                )
            )
            updated += 1
            continue

        current_tuple = (
            existing.contract,
            float(existing.quantity),
            float(existing.avg_price),
            float(existing.realized_pnl),
            float(existing.unrealized_pnl),
        )
        desired_tuple = (
            desired.contract,
            float(desired.quantity),
            float(desired.avg_price),
            float(desired.realized_pnl),
            unrealized_pnl,
        )
        if current_tuple != desired_tuple or existing.updated_at != desired.updated_at:
            existing.contract = desired.contract
            existing.quantity = desired.quantity
            existing.avg_price = desired.avg_price
            existing.realized_pnl = desired.realized_pnl
            existing.unrealized_pnl = unrealized_pnl
            existing.max_position = max(float(existing.max_position or 0), desired.quantity)
            existing.updated_at = desired.updated_at
            updated += 1

    if updated > 0:
        session.commit()
    return updated


@dataclass
class _ReplayableKalshiOrder:
    ticker: str
    action: str
    side: str
    filled_shares: float
    fill_price: float
    fee_paid: float
    created_at: datetime
