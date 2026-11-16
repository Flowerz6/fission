"""The MAP-Elites archive: cells, occupants, coverage, and empty-cell targeting.

Phase 1 keeps the **human** in the judge seat: the generator proposes candidates,
a human reproduces and returns a verdict, and only a confirmed ``success`` becomes
a cell's occupant (GENERATION_DESIGN §1). The archive also holds the per-cell
failure memory that feeds Ruby-style memory-augmented mutation, and non-uniform
targeting biased to empty/weak cells (Ferret). One archive per ``target_model``
is the drift matrix (§3). Nothing here scores a candidate — that is the Layer-0.5
seam, deliberately left to the human.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from advtest_ext.generate.axes import AXES, AXIS_VALUES, Cell, grid_size
from advtest_ext.generate.directed import FailedAttempt
from advtest_ext.generate.variant import Variant

_SUCCESS = "success"


@dataclass
class Target:
    """A directed-generation assignment: mutate ``parent`` toward ``target_cell`` (one axis)."""

    parent: Variant
    target_cell: Cell


@dataclass
class Archive:
    """One target-model's archive over the axis grid."""

    target_model: str
    _occupants: dict[str, Variant] = field(default_factory=dict)
    _failures: dict[str, list[FailedAttempt]] = field(default_factory=dict)

    def place(self, variant: Variant, verdict: str) -> bool:
        """Human-judged placement: a ``success`` variant becomes/replaces its cell's occupant.

        Returns True if the archive changed. A non-success verdict never occupies a cell.
        """
        if verdict != _SUCCESS:
            return False
        self._occupants[variant.cell.key] = variant
        return True

    def seed(self, variant: Variant) -> None:
        """Place a human seed as the confirmed occupant of its cell (generation 0)."""
        self._occupants[variant.cell.key] = variant

    def occupant(self, cell: Cell) -> Variant | None:
        return self._occupants.get(cell.key)

    def is_filled(self, cell: Cell) -> bool:
        return cell.key in self._occupants

    def filled_cells(self) -> list[Cell]:
        return [v.cell for v in self._occupants.values()]

    def record_failure(self, cell: Cell, attempt: FailedAttempt) -> None:
        self._failures.setdefault(cell.key, []).append(attempt)

    def memory_at(self, cell: Cell) -> tuple[FailedAttempt, ...]:
        return tuple(self._failures.get(cell.key, ()))

    def coverage(self) -> float:
        """Archive fill-rate over the full grid — the metric, not variant count (§3)."""
        return len(self._occupants) / grid_size()

    def fill_report(self) -> dict[str, object]:
        """Coverage plus per-axis-value fill counts, so empty regions are visible."""
        per_axis: dict[str, dict[str, int]] = {
            axis: dict.fromkeys(AXIS_VALUES[axis], 0) for axis in AXES
        }
        for variant in self._occupants.values():
            for axis in AXES:
                per_axis[axis][getattr(variant.cell, axis)] += 1
        return {
            "target_model": self.target_model,
            "filled": len(self._occupants),
            "grid_size": grid_size(),
            "coverage": round(self.coverage(), 4),
            "per_axis": per_axis,
        }

    def sample_target(self, rng: random.Random) -> Target | None:
        """Pick a (parent, target_cell) for directed generation, biased to empty neighbours.

        A target is a one-axis neighbour of a filled cell (so there is a parent to mutate).
        Empty neighbours are weighted far above filled ones (Ferret low-fitness bias). Returns
        None when nothing is filled yet (a seed is needed first).
        """
        weighted = self._weighted_targets()
        if not weighted:
            return None
        targets, weights = zip(*weighted, strict=True)
        chosen: Target = rng.choices(targets, weights=weights, k=1)[0]
        return chosen

    def _weighted_targets(self) -> list[tuple[Target, float]]:
        weighted: list[tuple[Target, float]] = []
        seen: set[tuple[str, str]] = set()
        for occupant in self._occupants.values():
            for neighbour in occupant.cell.neighbours():
                pair = (occupant.cell.key, neighbour.key)
                if pair in seen:
                    continue
                seen.add(pair)
                weight = 1.0 if self.is_filled(neighbour) else 8.0  # empty cells dominate
                weighted.append((Target(parent=occupant, target_cell=neighbour), weight))
        return weighted


class ArchiveSet:
    """Per-``target_model`` archives — the drift matrix (different models fall to different cells)."""

    def __init__(self) -> None:
        self._by_model: dict[str, Archive] = {}

    def archive(self, target_model: str) -> Archive:
        return self._by_model.setdefault(target_model, Archive(target_model=target_model))

    def drift_matrix(self) -> dict[str, dict[str, object]]:
        """Per-model coverage — which models have which cells filled."""
        return {model: archive.fill_report() for model, archive in self._by_model.items()}


__all__ = ["Archive", "ArchiveSet", "Target"]
