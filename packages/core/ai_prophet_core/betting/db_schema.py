"""Betting module table models.

Normalized tables used by :class:`~ai_prophet_core.betting.engine.BettingEngine`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BettingPrediction(Base):
    """Every incoming probabilistic prediction received by the engine."""

    __tablename__ = "betting_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    tick_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    p_yes: Mapped[float] = mapped_column(Float, nullable=False)
    yes_ask: Mapped[float] = mapped_column(Float, nullable=False)
    no_ask: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_betting_pred_instance_tick_market", "instance_name", "tick_ts", "market_id"),
        Index("ix_betting_pred_instance_source", "instance_name", "source"),
        Index("ix_betting_pred_instance_market", "instance_name", "market_id"),
        UniqueConstraint("instance_name", "source", "tick_ts", "market_id", name="uq_betting_prediction"),
    )


class BettingSignal(Base):
    """Strategy evaluation output for a single prediction."""

    __tablename__ = "betting_signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    prediction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("betting_predictions.id"), nullable=False
    )
    strategy_name: Mapped[str] = mapped_column(String(128), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    shares: Mapped[float] = mapped_column(Float, nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_betting_signal_instance_pred", "instance_name", "prediction_id"),
        Index("ix_betting_signal_instance_strategy", "instance_name", "strategy_name"),
    )


class BettingOrder(Base):
    """Order placed (or simulated) on an exchange."""

    __tablename__ = "betting_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_name: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    signal_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("betting_signals.id"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    ticker: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(8), nullable=False, default="BUY")
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    filled_shares: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fill_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_betting_order_instance_signal", "instance_name", "signal_id"),
        Index("ix_betting_order_instance_status", "instance_name", "status"),
        Index("ix_betting_order_instance_ticker", "instance_name", "ticker"),
        Index("ix_betting_order_instance_created", "instance_name", "created_at"),
    )
