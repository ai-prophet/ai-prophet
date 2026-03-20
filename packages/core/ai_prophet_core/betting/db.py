"""Local DB helpers for live betting integration."""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
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


@contextmanager
def get_session(engine: Engine):
    """Yield a DB session with automatic retry on transient connection errors."""
    key = id(engine)
    if key not in _session_factories:
        _session_factories[key] = sessionmaker(bind=engine, expire_on_commit=False)

    last_err = None
    for attempt in range(2):
        session = _session_factories[key]()
        try:
            yield session
            session.commit()
            return
        except Exception as exc:
            session.rollback()
            # Retry once on connection-level errors (Supabase drops, TCP resets)
            from sqlalchemy.exc import OperationalError, DisconnectionError
            if attempt == 0 and isinstance(exc, (OperationalError, DisconnectionError)):
                last_err = exc
                logger.warning("DB connection error (retrying): %s", exc)
                session.close()
                continue
            raise
        finally:
            session.close()

    # Should not reach here, but just in case
    if last_err is not None:
        raise last_err
