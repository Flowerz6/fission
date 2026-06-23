"""Dashboard / reporting layer.

FastAPI + HTMX + Jinja2 — heatmaps over hiding-technique x
payload-family composition (FOUNDATIONS.md "open problems" #2),
plus per-run PDF reports via WeasyPrint (optional ``pdf`` extra).

The :func:`create_app` factory takes a SQLAlchemy engine or a DB URL
and returns a fully-wired FastAPI application; tests use an
in-memory SQLite engine, the production CLI / uvicorn entrypoint
points at a file-backed engine.
"""

from advtest_ext.dashboard.app import create_app

__citations__: list[str] = []  # original infrastructure work

__all__ = ["create_app"]
