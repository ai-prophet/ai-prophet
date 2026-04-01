"""Local DB helpers for live betting integration."""

from __future__ import annotations

import logging
import os
import random
import time
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.exc import DisconnectionError, OperationalError, TimeoutError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger(__name__)


def get_database_url(override: str | None = None) -> str:
    url = override or os.getenv("DATABASE_URL", "sqlite:///./pa_dev.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url


def _pg_connect_args() -> dict:
    """libpq connection args tuned for Supabase pgBouncer."""
    return {
        "connect_timeout": 30,
        "options": "-c statement_timeout=60000 -c lock_timeout=20000",
        # TCP keepalives — detect dead connections in ~45s instead of OS default (~2h).
        # Supabase pgBouncer may silently drop idle connections; keepalives ensure
        # we discover that promptly rather than blocking on a dead socket.
        "keepalives": 1,
        "keepalives_idle": 15,     # start probing after 15s idle
        "keepalives_interval": 5,  # probe every 5s
        "keepalives_count": 6,     # give up after 6 failed probes (~45s)
    }


def create_db_engine(
    database_url: str | None = None,
    echo: bool = False,
    **kwargs,
) -> Engine:
    url = get_database_url(database_url)
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        return create_engine(url, echo=echo, connect_args=connect_args, **kwargs)
    use_null_pool = kwargs.pop("poolclass", None) is NullPool or \
        os.getenv("DB_NULL_POOL", "").lower() in ("1", "true")
    connect_args = _pg_connect_args()
    if use_null_pool:
        logger.warning("Using NullPool - this will be very slow! Consider using a small pool instead.")
        return create_engine(url, echo=echo, poolclass=NullPool, connect_args=connect_args, **kwargs)

    pool_size = kwargs.pop("pool_size", int(os.getenv("DB_POOL_SIZE", "2")))
    max_overflow = kwargs.pop("max_overflow", int(os.getenv("DB_MAX_OVERFLOW", "2")))
    eng = create_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        # Supabase pgBouncer drops idle connections after ~60-120s.
        # Recycle at 120s so the pool never hands out a silently-dead conn.
        pool_recycle=120,
        pool_timeout=30,
        connect_args=connect_args,
        **kwargs,
    )

    # Pessimistic disconnect handling: if a connection errors out mid-use,
    # invalidate it so the pool replaces it on the next checkout.
    @event.listens_for(eng, "handle_error")
    def _handle_error(context):
        if context.connection is not None and context.is_disconnect:
            logger.warning("Disconnect detected, invalidating pooled connection")

    return eng


_session_factories: dict[int, sessionmaker] = {}


def _get_factory(engine: Engine) -> sessionmaker:
    key = id(engine)
    if key not in _session_factories:
        _session_factories[key] = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factories[key]


def _session_retry_settings() -> tuple[int, float]:
    retries = max(0, int(os.getenv("DB_SESSION_ACQUIRE_RETRIES", "3")))
    backoff_sec = max(0.0, float(os.getenv("DB_SESSION_ACQUIRE_BACKOFF_SEC", "1.0")))
    return retries, backoff_sec


@contextmanager
def get_session(engine: Engine):
    """Yield a DB session. Retries on transient connection/pool pressure errors.

    On OperationalError / DisconnectionError / TimeoutError the failed session
    is discarded and retried before yielding, so the @contextmanager contract
    (exactly one yield) is preserved.

    Uses exponential backoff with jitter to avoid thundering-herd retries when
    multiple workers hit Supabase pooler limits simultaneously.
    """
    factory = _get_factory(engine)
    retries, backoff_sec = _session_retry_settings()
    max_attempts = retries + 1
    session = None

    for attempt in range(1, max_attempts + 1):
        session = factory()
        try:
            session.connection()
            break
        except (OperationalError, DisconnectionError, TimeoutError) as exc:
            session.close()
            if isinstance(exc, (OperationalError, DisconnectionError)):
                engine.dispose()

            if attempt >= max_attempts:
                raise

            # Exponential backoff: 1s, 2s, 4s, … capped at 15s, plus ±25% jitter
            wait_sec = min(backoff_sec * (2 ** (attempt - 1)), 15.0)
            wait_sec *= 0.75 + random.random() * 0.5  # jitter
            logger.warning(
                "DB session acquisition attempt %d/%d failed, retrying in %.1fs: %s",
                attempt,
                max_attempts,
                wait_sec,
                exc,
            )
            time.sleep(wait_sec)

    try:
        assert session is not None
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
