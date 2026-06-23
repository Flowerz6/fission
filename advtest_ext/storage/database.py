"""Engine + session factory wiring.

Default URL is ``sqlite:///./advtest.db`` (a project-local file).
Postgres is supported via ``ADVTEST_DB_URL`` of the shape
``postgresql+psycopg://user:pw@host/dbname``; the schema is pure
SQLAlchemy 2.0 with portable column types so no migration code path
is needed for the dialect switch.

This is a thin module by design: callers (``Repository``,
dashboard, tests) import :func:`make_engine` / :func:`session_scope`
and treat the rest as opaque.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from advtest_ext.storage.models import Base

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine


_DEFAULT_URL = "sqlite:///./advtest.db"


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine from ``url`` or ``ADVTEST_DB_URL``.

    For SQLite, sets ``check_same_thread=False`` so the dashboard's
    request handlers (which run in the FastAPI worker thread) can
    share an engine with a sync orchestrator on the main thread.
    Tables are created on first use — no Alembic for v0; the schema
    is small enough that ``Base.metadata.create_all`` is the
    operating contract."""
    resolved = url or os.environ.get("ADVTEST_DB_URL") or _DEFAULT_URL
    connect_args: dict[str, object] = {}
    extra: dict[str, object] = {}
    if resolved.startswith("sqlite"):
        connect_args["check_same_thread"] = False
        # In-memory SQLite databases are scoped to a single connection,
        # so the default QueuePool's separate connections each see a
        # fresh empty database — including the dashboard's request
        # handlers, which couldn't find the tables the test seeded.
        # StaticPool reuses one connection across the engine, which is
        # the canonical pattern for in-memory test fixtures and is
        # safe here because we already wave check_same_thread.
        if ":memory:" in resolved:
            extra["poolclass"] = StaticPool
    engine = create_engine(resolved, echo=echo, future=True, connect_args=connect_args, **extra)
    Base.metadata.create_all(engine)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Commit-on-success / rollback-on-exception session.

    Boilerplate-free pattern for callers that just want a unit-of-
    work; the dashboard's request handlers use FastAPI's dependency
    system instead and don't go through this."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["make_engine", "make_session_factory", "session_scope"]
