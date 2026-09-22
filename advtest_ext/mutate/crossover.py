"""Crossover — compose descriptor values from two successful parents.

Take, say, the carrier from parent A and the framing from parent B (each a value
that succeeded in its own cell) and instantiate them together onto a base guide,
reaching an unvisited cell with NO wording step. The composition is verified to
land in the intended (composed) cell — if the strategist bleeds, the child won't
match and the crossover is reported unverified.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from advtest_ext.mutate.instantiate import Instantiation, PatchStrategist, instantiate

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.archive.fingerprint import Archive
    from advtest_ext.archive.signature import Signature
    from advtest_ext.guides.models import Guide


@dataclass(frozen=True)
class CrossoverResult:
    guide: Guide
    signature: Signature
    steps: tuple[Instantiation, ...]
    intended_axes: dict[str, str]
    verified: bool
    previously_unvisited: bool

    @property
    def all_clean(self) -> bool:
        return bool(self.steps) and all(s.instantiation_clean for s in self.steps)


def crossover(
    base_guide: Guide,
    base_signature: Signature,
    *,
    parent_a: Signature,
    parent_b: Signature,
    take_from_a: Sequence[str] = ("carrier_placement",),
    take_from_b: Sequence[str] = ("framing",),
    strategist: PatchStrategist | None = None,
    archive: Archive | None = None,
) -> CrossoverResult:
    """Compose ``take_from_a`` values from ``parent_a`` and ``take_from_b`` values
    from ``parent_b`` onto ``base_guide`` via chained :func:`instantiate`."""
    intended: dict[str, str] = {}
    for axis in take_from_a:
        intended[axis] = getattr(parent_a, axis)
    for axis in take_from_b:
        intended[axis] = getattr(parent_b, axis)

    guide = base_guide
    signature = base_signature
    steps: list[Instantiation] = []
    for axis, value in intended.items():
        inst = instantiate(guide, signature, axis, value, strategist=strategist)
        guide = inst.guide
        signature = inst.child_signature
        steps.append(inst)

    verified = _lands_in(signature, intended)
    previously_unvisited = archive is None or signature.cell_key() not in archive.cells
    return CrossoverResult(
        guide=guide,
        signature=signature,
        steps=tuple(steps),
        intended_axes=intended,
        verified=verified,
        previously_unvisited=previously_unvisited,
    )


def _lands_in(signature: Signature, intended: Mapping[str, str]) -> bool:
    return all(getattr(signature, axis) == value for axis, value in intended.items())


__all__ = ["CrossoverResult", "crossover"]
