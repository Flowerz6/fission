"""The archive axis grid (QD / MAP-Elites diversity dimensions).

A variant is placed on a cell of ``harm x surface x technique x carrier_type``
(GENERATION_DESIGN §1, CAPTURE_SPEC §4.5). The grid is the behaviour space the
generator illuminates; empty cells are the worklist. Values bind to the repo's
closed vocabularies so a cell is always a real, reproducible combination.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import product

from advtest_ext.delivery.harm_taxonomy import PILOT_HARM_TYPES

# Content axis = one content ``_MutationDimension``; structural axes = surface + carrier_type.
# (The ``flow``/``topology`` structural axes are human-authored metadata, not binned here — the
# generator mutates content + carrier_type/surface, CAPTURE_SPEC §4.5.)
HARMS: tuple[str, ...] = tuple(PILOT_HARM_TYPES)
SURFACES: tuple[str, ...] = ("workspace_studio", "gemini_sidepanel")
TECHNIQUES: tuple[str, ...] = (
    "pretext_scenario",
    "persona",
    "wording",
    "format_mimicry",
    "escalation_trajectory",
    "carrier_placement",
)
CARRIER_TYPES: tuple[str, ...] = ("doc", "email", "sheet", "calendar_invite", "chat_message")

AXES: tuple[str, ...] = ("harm", "surface", "technique", "carrier_type")
AXIS_VALUES: dict[str, tuple[str, ...]] = {
    "harm": HARMS,
    "surface": SURFACES,
    "technique": TECHNIQUES,
    "carrier_type": CARRIER_TYPES,
}


class CellError(ValueError):
    """A cell axis value is not in the closed vocabulary for that axis."""


@dataclass(frozen=True)
class Cell:
    """One archive cell: a concrete value on every diversity axis."""

    harm: str
    surface: str
    technique: str
    carrier_type: str

    def __post_init__(self) -> None:
        for axis in AXES:
            value = getattr(self, axis)
            if value not in AXIS_VALUES[axis]:
                raise CellError(f"{axis}={value!r} not in {AXIS_VALUES[axis]}")

    @property
    def key(self) -> str:
        return "|".join(getattr(self, axis) for axis in AXES)

    @property
    def values(self) -> dict[str, str]:
        return {axis: getattr(self, axis) for axis in AXES}

    def differing_axes(self, other: Cell) -> tuple[str, ...]:
        """The axes whose value differs between this cell and ``other``."""
        return tuple(axis for axis in AXES if getattr(self, axis) != getattr(other, axis))

    def with_axis(self, axis: str, value: str) -> Cell:
        """A copy of this cell with one axis set to ``value`` (the one-axis move)."""
        if axis not in AXES:
            raise CellError(f"unknown axis {axis!r}")
        return Cell(**{**self.values, axis: value})

    def neighbours(self) -> Iterator[Cell]:
        """Every cell one axis-value away (the one-axis-per-step move set)."""
        for axis in AXES:
            for value in AXIS_VALUES[axis]:
                if value != getattr(self, axis):
                    yield self.with_axis(axis, value)


def enumerate_grid() -> Iterator[Cell]:
    """Every cell in the full axis grid."""
    for harm, surface, technique, carrier_type in product(
        HARMS, SURFACES, TECHNIQUES, CARRIER_TYPES
    ):
        yield Cell(harm=harm, surface=surface, technique=technique, carrier_type=carrier_type)


def grid_size() -> int:
    return len(HARMS) * len(SURFACES) * len(TECHNIQUES) * len(CARRIER_TYPES)


__all__ = [
    "AXES",
    "AXIS_VALUES",
    "CARRIER_TYPES",
    "HARMS",
    "SURFACES",
    "TECHNIQUES",
    "Cell",
    "CellError",
    "enumerate_grid",
    "grid_size",
]
