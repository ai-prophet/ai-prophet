"""Local DB helpers for live betting integration."""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import DisconnectionError, OperationalError, TimeoutError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

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
    use_null_pool = kwargs.pop("poolclass", None) is NullPool or \
        os.getenv("DB_NULL_POOL", "").lower() in ("1", "true")
    connect_args = {
        "connect_timeout": 30,  # Increased from 10 to 30 seconds
        "options": "-c statement_timeout=60000 -c lock_timeout=20000",  # Increased timeouts
    }
    if use_null_pool:
        # NullPool: no persistent connections — connect/disconnect per request.
        # IMPORTANT: This is extremely slow (40+ seconds per request) with Cloud Run + Supabase
        # because each request creates a new TCP connection + SSL handshake + pgBouncer auth.
        # Only use this for local development or if you have persistent connection issues.
        logger.warning("Using NullPool - this will be very slow! Consider using a small pool instead.")
        return create_engine(url, echo=echo, poolclass=NullPool, connect_args=connect_args, **kwargs)

    # Use a reasonable pool size for concurrent operations
    # Default to 5 connections with 10 overflow for better concurrency
    pool_size = kwargs.pop("pool_size", int(os.getenv("DB_POOL_SIZE", "5")))
    max_overflow = kwargs.pop("max_overflow", int(os.getenv("DB_MAX_OVERFLOW", "10")))
    return create_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,           # 5 min — increase from 1 min for better connection reuse
        pool_timeout=30,            # increased from 10 to 30 seconds for busy periods
        connect_args=connect_args,
        **kwargs,
    )


_session_factories: dict[int, sessionmaker] = {}


def _get_factory(engine: Engine) -> sessionmaker:
    key = id(engine)
    if key not in _session_factories:
        _session_factories[key] = sessionmaker(bind=engine, expire_on_commit=False)
    return _session_factories[key]


def _session_retry_settings() -> tuple[int, float]:
    retries = max(0, int(os.getenv("DB_SESSION_ACQUIRE_RETRIES", "2")))
    backoff_sec = max(0.0, float(os.getenv("DB_SESSION_ACQUIRE_BACKOFF_SEC", "1.5")))
    return retries, backoff_sec


@contextmanager
def get_session(engine: Engine):
    """Yield a DB session. Retries on transient connection/pool pressure errors.

    On OperationalError / DisconnectionError / TimeoutError the failed session
    is discarded and retried before yielding, so the @contextmanager contract
    (exactly one yield) is preserved.
    """
    factory = _get_factory(engine)
    retries, backoff_sec = _session_retry_settings()
    max_attempts = retries + 1
    session = None

    for attempt in range(1, max_attempts + 1):
        session = factory()
        try:
            # Test the connection before yielding so pool exhaustion or stale
            # connections are retried before the caller touches the session.
            session.connection()
            break
        except (OperationalError, DisconnectionError, TimeoutError) as exc:
            session.close()
            if isinstance(exc, (OperationalError, DisconnectionError)):
                engine.dispose()  # drop pooled stale connections before retry

            if attempt >= max_attempts:
                raise

            wait_sec = backoff_sec * attempt
            logger.warning(
                "DB session acquisition attempt %d/%d failed, retrying in %.1fs: %s",
                attempt,
                max_attempts,
                wait_sec,
                exc,
            )
            if wait_sec > 0:
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
