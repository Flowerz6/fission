"""Corpus-format invariants for guides authored as YAML or JSON.

The corpus is mid-migration to YAML (the trackable seed authoring format). Both
formats load through one chokepoint (``corpus.load_guide``), and a guide id may
transiently exist as both while migrating. These tests enforce the safety
properties that make coexistence non-hazardous:

1. Every guide file in ``guides/`` loads and validates.
2. No guide id exists as two files whose *content* differs — otherwise load-time
   dedup (YAML wins) would silently ignore edits to the losing twin.
3. Every guide round-trips through YAML with an identical content hash, so the
   converter (``scripts/seed_capture_to_yaml.py``) is provably lossless.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml

from advtest_ext.guides.completeness import check_guide
from advtest_ext.guides.corpus import load_guide, load_guides
from advtest_ext.guides.models import Guide, content_hash

_GUIDES_DIR = Path(__file__).resolve().parents[1] / "guides"


def _all_guide_files() -> list[Path]:
    files: list[Path] = []
    for pattern in ("*.json", "*.yml", "*.yaml"):
        files.extend(sorted(_GUIDES_DIR.glob(pattern)))
    return files


def test_every_guide_file_loads() -> None:
    files = _all_guide_files()
    assert files, "no guide files found in guides/"
    for path in files:
        guide = load_guide(path)
        assert guide.id, f"{path.name} loaded a guide with no id"


def test_no_diverging_dual_format_twins() -> None:
    """If an id is authored as both YAML and JSON, their content must match."""
    by_id: dict[str, list[tuple[Path, str]]] = defaultdict(list)
    for path in _all_guide_files():
        guide = load_guide(path)
        by_id[guide.id].append((path, content_hash(guide)))
    for guide_id, entries in by_id.items():
        hashes = {h for _, h in entries}
        assert len(hashes) == 1, (
            f"guide id {guide_id!r} has diverging formats: "
            + ", ".join(f"{p.name}={h}" for p, h in entries)
            + " — one would be silently ignored at load time"
        )


def test_every_guide_round_trips_through_yaml() -> None:
    """model_dump -> YAML -> reload preserves attack content (converter is lossless)."""
    for path in _all_guide_files():
        guide = load_guide(path)
        text = yaml.safe_dump(
            guide.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
            default_flow_style=False,
        )
        reloaded = Guide.model_validate(yaml.safe_load(text))
        assert content_hash(reloaded) == content_hash(guide), (
            f"{path.name} does not round-trip through YAML losslessly"
        )


def test_no_guide_has_completeness_errors() -> None:
    """Every shipped guide must have everything the system needs to run and verify it.

    Error-level completeness issues (no deterministic signal, no seed chain, unresolved
    setup operation, missing provenance) mean the system cannot run or confirm the guide;
    the corpus must have none. Warnings (e.g. no default adapter for a track-only surface)
    are allowed."""
    offenders: list[str] = []
    for guide in load_guides(_GUIDES_DIR):
        errors = [i for i in check_guide(guide) if i.level == "error"]
        if errors:
            offenders.append(f"{guide.id}: " + "; ".join(f"[{e.code}] {e.message}" for e in errors))
    assert not offenders, "guides with completeness errors:\n" + "\n".join(offenders)
