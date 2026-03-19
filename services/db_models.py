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
