"""Live betting table models."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class BetDecisionTable(Base):
    __tablename__ = "bet_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tick_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    p_yes: Mapped[float] = mapped_column(Float, nullable=False)
    yes_ask: Mapped[float] = mapped_column(Float, nullable=False)
    no_ask: Mapped[float] = mapped_column(Float, nullable=False)
    side: Mapped[str | None] = mapped_column(String(8), nullable=True)
    shares: Mapped[float | None] = mapped_column(Float, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_bet_decisions_tick_market", "tick_ts", "market_id"),
        Index("ix_bet_decisions_model", "model_name"),
        UniqueConstraint("model_name", "tick_ts", "market_id", name="uq_bet_decision"),
    )


class KalshiOrderTable(Base):
    __tablename__ = "kalshi_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    tick_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ticker: Mapped[str] = mapped_column(String(255), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    net_shares: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    filled_shares: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    fill_price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    exchange_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("ix_kalshi_orders_tick_market", "tick_ts", "market_id"),
        Index("ix_kalshi_orders_status", "status"),
    )
