"""Standalone Venice-driven variant generator (Phase 1).

Quality-diversity (MAP-Elites) directed mutation with a **human** judge
(FISSION_GENERATION_DESIGN). The carrier text is the genome: the directed
mutator edits a parent carrier toward a named archive cell, holding invariants
fixed. The human reproduces each candidate and fills the archive; no automated
judge, no generate->score->regenerate loop (that is the Layer-0.5 seam, left
clean over the same archive). Fixtures only until the redaction gate exists —
the model call is injected, so nothing here reaches a live target or Venice.
"""

from __future__ import annotations

from advtest_ext.generate.archive import Archive, ArchiveSet, Target
from advtest_ext.generate.axes import Cell, enumerate_grid, grid_size
from advtest_ext.generate.directed import (
    FailedAttempt,
    build_directed_prompt,
    compose_path,
    generate_candidates,
    parse_carrier,
    prerank,
)
from advtest_ext.generate.variant import Variant, render_writeup, seed_variant

__all__ = [
    "Archive",
    "ArchiveSet",
    "Cell",
    "FailedAttempt",
    "Target",
    "Variant",
    "build_directed_prompt",
    "compose_path",
    "enumerate_grid",
    "generate_candidates",
    "grid_size",
    "parse_carrier",
    "prerank",
    "render_writeup",
    "seed_variant",
]
