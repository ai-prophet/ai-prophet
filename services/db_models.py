"""Dashboard-specific table models.

Extends the existing betting schema with tables for market tracking,
position aggregation, model run logging, system health, and price snapshots.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ai_prophet_core.betting.db_schema import Base


class TradingMarket(Base):
    """Markets currently being tracked/traded."""

    __tablename__ = "trading_markets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    event_ticker: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=True)
    expiration: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    yes_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    yes_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_bid: Mapped[float | None] = mapped_column(Float, nullable=True)
    no_ask: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("instance_name", "market_id", name="uq_trading_market_instance_market"),
        Index("ix_trading_market_instance_category", "instance_name", "category"),
        Index("ix_trading_market_instance_ticker", "instance_name", "ticker"),
    )


class TradingMarketLifecycle(Base):
    """Latest fetched Kalshi lifecycle state for a tracked market."""

    __tablename__ = "trading_market_lifecycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="unknown")
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("instance_name", "market_id", name="uq_trading_market_lifecycle_instance_market"),
        Index("ix_trading_market_lifecycle_instance_ticker", "instance_name", "ticker"),
        Index("ix_trading_market_lifecycle_instance_status", "instance_name", "status"),
    )


class TradingPosition(Base):
    """Aggregated position view per market."""

    __tablename__ = "trading_positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    contract: Mapped[str] = mapped_column(String(16), nullable=False)  # "yes" or "no"
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    avg_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    max_position: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    realized_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("instance_name", "market_id", name="uq_trading_position_instance_market"),
        Index("ix_trading_position_instance_market", "instance_name", "market_id"),
    )


class ModelRun(Base):
    """Log of model decisions for audit and dashboard display."""

    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)  # BUY_YES, BUY_NO, HOLD, SKIP
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_model_run_instance_model", "instance_name", "model_name"),
        Index("ix_model_run_instance_ts", "instance_name", "timestamp"),
        Index("ix_model_run_instance_market", "instance_name", "market_id"),
    )


class SystemLog(Base):
    """System event/error log for health monitoring."""

    __tablename__ = "system_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    level: Mapped[str] = mapped_column(String(16), nullable=False)  # INFO, WARNING, ERROR, HEARTBEAT, ALERT
    message: Mapped[str] = mapped_column(Text, nullable=False)
    component: Mapped[str] = mapped_column(String(64), nullable=False)  # worker, api, system
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_system_log_instance_level", "instance_name", "level"),
        Index("ix_system_log_instance_component", "instance_name", "component"),
        Index("ix_system_log_instance_created", "instance_name", "created_at"),
    )


class AlertDismissal(Base):
    """Dismissed dashboard alerts keyed by a stable alert fingerprint."""

    __tablename__ = "alert_dismissals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    alert_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("instance_name", "alert_key", name="uq_alert_dismissal_instance_key"),
        Index("ix_alert_dismissal_instance_created", "instance_name", "created_at"),
    )


class MarketPriceSnapshot(Base):
    """Periodic market price snapshots for time-series analysis."""

    __tablename__ = "market_price_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    yes_ask: Mapped[float] = mapped_column(Float, nullable=False)
    no_ask: Mapped[float] = mapped_column(Float, nullable=False)
    volume_24h: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_p_yes: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_price_snap_instance_market_ts", "instance_name", "market_id", "timestamp"),
        Index("ix_price_snap_instance_ticker", "instance_name", "ticker"),
        Index("ix_price_snap_instance_ts", "instance_name", "timestamp"),
    )


class KalshiBalanceSnapshot(Base):
    """Append-only snapshots of live Kalshi balance state."""

    __tablename__ = "kalshi_balance_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    balance: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    portfolio_value: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    updated_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("instance_name", "snapshot_ts", name="uq_kalshi_balance_snapshot_instance_ts"),
        Index("ix_kalshi_balance_snapshot_instance_ts", "instance_name", "snapshot_ts"),
    )


class KalshiPositionSnapshot(Base):
    """Append-only snapshots of live Kalshi positions."""

    __tablename__ = "kalshi_position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    ticker: Mapped[str] = mapped_column(String(255), nullable=False)
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    signed_quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    quantity: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    market_exposure: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fees_paid: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    total_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cost_shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_traded: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_orders_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("instance_name", "ticker", "snapshot_ts", name="uq_kalshi_position_snapshot_instance_ticker_ts"),
        Index("ix_kalshi_position_snapshot_instance_ticker_ts", "instance_name", "ticker", "snapshot_ts"),
        Index("ix_kalshi_position_snapshot_instance_ts", "instance_name", "snapshot_ts"),
    )


class KalshiOrderSnapshot(Base):
    """Deduplicated snapshots of live and historical Kalshi order states."""

    __tablename__ = "kalshi_order_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Haifeng")
    order_id: Mapped[str] = mapped_column(String(128), nullable=False)
    client_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ticker: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    initial_count: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fill_count: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    remaining_count: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_fill_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    fee_paid: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="portfolio")
    created_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_update_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("instance_name", "order_id", "last_update_ts", name="uq_kalshi_order_snapshot_instance_order_update"),
        Index("ix_kalshi_order_snapshot_instance_ticker", "instance_name", "ticker"),
        Index("ix_kalshi_order_snapshot_instance_status", "instance_name", "status"),
        Index("ix_kalshi_order_snapshot_instance_update", "instance_name", "last_update_ts"),
    )
