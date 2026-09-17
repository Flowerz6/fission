"""Load :class:`Guide` files from a corpus directory (default ``guides/`` at the
repo root). File-first by design, the DB table lands only when the dashboard
needs to browse guides.

Guides may be authored as YAML (``.yaml``/``.yml``) or JSON. YAML is the
preferred authoring format for new seeds: it is diffable and human-readable, and
the ``Guide`` model validates a YAML-loaded dict exactly as it does JSON. Loading
is the only format-aware layer, everything downstream consumes the in-memory
:class:`Guide`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from advtest_ext.guides.models import Guide

# Author guides in any of these; YAML first.
GUIDE_SUFFIXES: tuple[str, ...] = (".yaml", ".yml", ".json")


def load_guide(path: str | Path) -> Guide:
    """Load one guide from YAML or JSON, dispatched by file suffix.

    Both paths funnel through ``Guide.model_validate`` (via a parsed dict), so a
    YAML and a JSON authoring of the same guide validate identically. ``extra=
    "forbid"`` on the model means a stray key in either format is rejected early."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in {".yaml", ".yml"}:
        data: Any = yaml.safe_load(text)
        return Guide.model_validate(data)
    return Guide.model_validate_json(text)


def resolve_guide_path(directory: str | Path, guide_id: str) -> Path | None:
    """Return the authoritative on-disk file for ``guide_id``, or None.

    Mirrors ``load_guides`` precedence: if a guide id exists as both YAML and
    JSON, the YAML file is the one that wins, so a writer (e.g. the cell
    generator) edits the file that will actually be loaded — never a stale JSON
    twin that load-time dedup would ignore."""
    d = Path(directory)
    for suffix in (".yaml", ".yml", ".json"):
        candidate = d / f"{guide_id}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def load_guides(directory: str | Path) -> list[Guide]:
    """Load every guide in ``directory`` (YAML or JSON), sorted by id.

    If the same guide id exists as both YAML and JSON, YAML wins, so a guide can
    be migrated to YAML by adding the ``.yaml`` file without first deleting the
    ``.json``. Sorted by guide id for a stable order across formats."""
    d = Path(directory)
    by_id: dict[str, Guide] = {}
    # JSON first, then YAML, so a YAML authoring overrides a stale JSON of the
    # same id during a migration.
    for pattern in ("*.json", "*.yml", "*.yaml"):
        for p in sorted(d.glob(pattern)):
            guide = load_guide(p)
            by_id[guide.id] = guide
    return [by_id[k] for k in sorted(by_id)]


__all__ = ["GUIDE_SUFFIXES", "load_guide", "load_guides", "resolve_guide_path"]
