"""Local DB helpers for live betting integration."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import DisconnectionError, OperationalError
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


def get_database_url(override: str | None = None) -> str:
    url = override or os.getenv("DATABASE_URL", "sqlite:///./pa_dev.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def create_db_engine(
    database_url: str | None = None,
    echo: bool = False,
    **kwargs,
) -> Engine:
    url = get_database_url(database_url)
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        return create_engine(url, echo=echo, connect_args=connect_args, **kwargs)
    pool_size = kwargs.pop("pool_size", 5)
    max_overflow = kwargs.pop("max_overflow", 5)
    return create_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,           # 5 min — Supabase drops idle connections aggressively
        pool_timeout=30,            # fail after 30s waiting for a pool slot
        connect_args={
            "connect_timeout": 10,  # TCP connect timeout (seconds)
            "options": "-c statement_timeout=30000 -c lock_timeout=10000",
        },
        **kwargs,
    )


_session_factories: dict[int, sessionmaker] = {}


def _get_factory(engine: Engine) -> sessionmaker:
    key = id(engine)
    if key not in _session_factories:
        _session_factories[key] = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factories[key]


@contextmanager
def get_session(engine: Engine):
    """Yield a DB session. Retries once on transient connection errors.

    On OperationalError / DisconnectionError the failed session is discarded,
    the pool connection is invalidated, and a fresh session is created for a
    single retry — all before yielding, so the @contextmanager contract
    (exactly one yield) is preserved.
    """
    factory = _get_factory(engine)
    session = factory()
    try:
        # Test the connection before yielding — if it's dead, the retry
        # below creates a fresh session before the caller ever sees it.
        session.connection()
    except (OperationalError, DisconnectionError) as exc:
        logger.warning("DB connection stale, retrying with fresh session: %s", exc)
        session.close()
        engine.dispose()  # drop all pooled connections
        session = factory()

    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
