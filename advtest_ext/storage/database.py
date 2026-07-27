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
from typing import TYPE_CHECKING, Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from advtest_ext.storage.models import Base

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine
    from sqlalchemy.sql.schema import Column


_DEFAULT_URL = "sqlite:///./advtest.db"


def _column_default_sql(col: Column[Any]) -> str | None:
    """A SQL literal for a scalar column default (so existing rows get a
    value when the column is back-filled), or None when there's no scalar
    default (the new column is then simply nullable at the DB level)."""
    default = col.default
    if default is None or not getattr(default, "is_scalar", False):
        return None
    val = getattr(default, "arg", None)
    if isinstance(val, bool):
        return "1" if val else "0"
    if isinstance(val, int | float):
        return str(val)
    if isinstance(val, str):
        return "'" + val.replace("'", "''") + "'"
    return None


def _ensure_additive_columns(engine: Engine) -> None:
    """Back-fill columns the model has but an EXISTING table doesn't.

    ``create_all`` creates missing TABLES but never alters existing ones, so a
    dev DB created before a column was added would error on any SELECT of that
    column (``no such column: traces.uncertainty``). This makes *additive*
    schema changes self-healing for dev SQLite / Postgres without Alembic —
    it only ever ``ADD COLUMN``s, never drops or retypes. The new column is
    added nullable-at-the-DB-level with the model's scalar default as a
    back-fill; every insert path supplies the value via the ORM default, so
    dropping the NOT NULL constraint at the DB layer is harmless.
    """
    insp = inspect(engine)
    try:
        tables = set(insp.get_table_names())
    except Exception:  # pragma: no cover - defensive
        return
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            continue  # create_all just made it — nothing to back-fill
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(dialect=engine.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'
            default_sql = _column_default_sql(col)
            if default_sql is not None:
                ddl += f" DEFAULT {default_sql}"
            with engine.begin() as conn:
                conn.execute(text(ddl))


def make_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    """Build a SQLAlchemy engine from ``url`` or ``ADVTEST_DB_URL``.

    For SQLite, sets ``check_same_thread=False`` so the dashboard's
    request handlers (which run in the FastAPI worker thread) can
    share an engine with a sync orchestrator on the main thread.
    Tables are created on first use — no Alembic for v0; the schema
    is small enough that ``Base.metadata.create_all`` is the
    operating contract. Additive column changes on a pre-existing DB are
    back-filled by :func:`_ensure_additive_columns` (create_all alone
    leaves old tables missing new columns)."""
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
    # Self-heal additive schema changes on a pre-existing DB (create_all only
    # makes missing tables, not missing columns) so a dev advtest.db from an
    # earlier version keeps working without a manual migration.
    _ensure_additive_columns(engine)
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
