"""Load :class:`Guide` JSON from a corpus directory (default ``guides/`` at the
repo root). File-first by design — the DB table lands only when the dashboard
needs to browse guides."""

from __future__ import annotations

from pathlib import Path

from advtest_ext.guides.models import Guide


def load_guide(path: str | Path) -> Guide:
    return Guide.model_validate_json(Path(path).read_text(encoding="utf-8"))


def load_guides(directory: str | Path) -> list[Guide]:
    """Load every ``*.json`` guide in ``directory``, sorted by filename."""
    return [load_guide(p) for p in sorted(Path(directory).glob("*.json"))]


__all__ = ["load_guide", "load_guides"]
