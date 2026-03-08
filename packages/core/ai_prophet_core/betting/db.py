"""Local DB helpers for live betting integration."""

from __future__ import annotations

import os
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def get_database_url(override: str | None = None) -> str:
    if override:
        return override
    return os.getenv("DATABASE_URL", "sqlite:///./pa_dev.db")


def create_db_engine(
    database_url: str | None = None,
    echo: bool = False,
    **kwargs,
) -> Engine:
    url = get_database_url(database_url)
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        return create_engine(url, echo=echo, connect_args=connect_args, **kwargs)
    pool_size = kwargs.pop("pool_size", 10)
    max_overflow = kwargs.pop("max_overflow", 20)
    return create_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=600,
        connect_args={"options": "-c statement_timeout=30000 -c lock_timeout=10000"},
        **kwargs,
    )


_session_factories: dict[int, sessionmaker] = {}


@contextmanager
def get_session(engine: Engine):
    key = id(engine)
    if key not in _session_factories:
        _session_factories[key] = sessionmaker(bind=engine, expire_on_commit=False)
    session = _session_factories[key]()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
