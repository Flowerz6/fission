"""The variant object and its writeup projection.

A variant is a full seed-shaped object (CAPTURE_SPEC §4.5) whose genome is the
concrete carrier text. It is tagged with its archive :class:`~advtest_ext.generate.axes.Cell`
so the same object serves generation, the human-judge archive, and (later) an
automated judge over the same grid. ``render_writeup`` is the human-facing
projection the seeder reproduces from (FISSION_WRITEUP_SPEC, Phase-1 text form).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from advtest_ext.generate.axes import AXES, Cell


def content_id(carrier: str, cell: Cell) -> str:
    """Stable 16-hex id over the genome + its cell (content-hash dedup, CAPTURE_SPEC §4.5)."""
    blob = f"{cell.key}\n{carrier}".encode()
    return hashlib.sha256(blob).hexdigest()[:16]


@dataclass(frozen=True)
class Variant:
    """A generated or human-authored variant: the carrier genome plus lineage and its cell."""

    seed_id: str
    cell: Cell
    carrier: str
    origin: str = "generated"  # "generated" | "human_edited"
    parent_variant_id: str | None = None
    generation: int = 0
    canary: str | None = None
    invariants: tuple[str, ...] = ()
    axes_mutated: tuple[str, ...] = ()
    axis_rationale: dict[str, str] = field(default_factory=dict)
    generator: str = "unknown"

    @property
    def id(self) -> str:
        return content_id(self.carrier, self.cell)


def seed_variant(
    *, seed_id: str, cell: Cell, carrier: str, canary: str | None, invariants: tuple[str, ...]
) -> Variant:
    """The root occupant for a human seed: generation 0, origin human_edited, no parent."""
    return Variant(
        seed_id=seed_id,
        cell=cell,
        carrier=carrier,
        origin="human_edited",
        generation=0,
        canary=canary,
        invariants=invariants,
        generator="human_seed",
    )


def render_writeup(variant: Variant) -> str:
    """Render a variant as a full writeup the seeder can read and reproduce.

    Phase-1 text projection of FISSION_WRITEUP_SPEC: the payload block is the carrier
    verbatim (``locked``); the cell, lineage, mutated axes + rationale are the metadata grid.
    """
    cell_line = " · ".join(f"{axis}={getattr(variant.cell, axis)}" for axis in AXES)
    lines = [
        f"# Variant {variant.id}  (seed {variant.seed_id})",
        f"Cell: {cell_line}",
        f"Origin: {variant.origin} · generation {variant.generation}"
        + (f" · parent {variant.parent_variant_id}" if variant.parent_variant_id else "")
        + f" · generator {variant.generator}",
    ]
    if variant.axes_mutated:
        lines.append("")
        lines.append("Changes from parent:")
        for axis in variant.axes_mutated:
            reason = variant.axis_rationale.get(axis, "(no rationale)")
            lines.append(f"  - {axis}: {reason}")
    if variant.invariants:
        lines.append("")
        lines.append("Invariants held:")
        lines.extend(f"  - {inv}" for inv in variant.invariants)
    if variant.canary:
        lines.append("")
        lines.append(f"Canary: {variant.canary}")
    lines.append("")
    lines.append("Payload — exact, do not modify:")
    lines.append(variant.carrier)
    return "\n".join(lines)


__all__ = ["Variant", "content_id", "render_writeup", "seed_variant"]
