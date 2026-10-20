"""The mutation plan: one-axis variants + planned pairs, realized via sub-step 0.

From a parent leaf with a known :class:`Signature`, emit (a) one-axis variants
over the four semantic axes + carrier, each through :func:`instantiate`, and
(b) a small set of PLANNED PAIRS (two axes moved together) for interaction
estimation — prioritising ``escalation_speed x turn_order``, whose individual
effects are entangled in multi-turn crescendo until paired data exists.

``wording`` mutation is allowed as a within-cell individual, tagged
``paraphrase_only=True`` and NEVER counted as an experiment (same cell).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from itertools import product

from advtest_ext.archive.signature import Signature
from advtest_ext.guides.models import Guide
from advtest_ext.mutate.instantiate import (
    CARRIER_AXIS,
    SEMANTIC_AXES,
    Instantiation,
    PatchStrategist,
    instantiate,
)

# The prioritised interaction pair (entangled in crescendo).
PRIORITY_PAIR: tuple[str, str] = ("escalation_speed", "turn_order")
_WORDING_AXIS = "wording"


@dataclass(frozen=True)
class PlannedVariant:
    """One planned experiment (or a paraphrase individual)."""

    kind: str  # "one_axis" | "pair" | "paraphrase"
    axes: dict[
        str, str
    ]  # dimension -> feature_value (1 for one_axis, 2 for pair, wording for paraphrase)
    paraphrase_only: bool = False


@dataclass
class MutationPlan:
    parent_signature: Signature
    variants: list[PlannedVariant] = field(default_factory=list)

    def experiments(self) -> list[PlannedVariant]:
        """The variants that are actual experiments (exclude paraphrase)."""
        return [v for v in self.variants if not v.paraphrase_only]


def _palette(guide: Guide) -> dict[str, list[str]]:
    """Per-axis feature-value palette from the guide's operators' cell_values."""
    palette: dict[str, list[str]] = {}
    for operator in guide.mutation_operators:
        if operator.cell_values:
            palette.setdefault(operator.dimension, []).extend(operator.cell_values)
    return palette


def plan_mutations(
    parent_guide: Guide,
    parent_signature: Signature,
    *,
    max_per_axis: int = 2,
    max_pair: int = 2,
    include_paraphrase: bool = True,
) -> MutationPlan:
    """Build the one-axis + planned-pair experiment plan for a parent leaf."""
    palette = _palette(parent_guide)
    plan = MutationPlan(parent_signature=parent_signature)

    # (a) one-axis variants over the four semantic axes + carrier.
    for axis in (*SEMANTIC_AXES, CARRIER_AXIS):
        for value in palette.get(axis, [])[:max_per_axis]:
            plan.variants.append(PlannedVariant(kind="one_axis", axes={axis: value}))

    # (b) planned pairs — escalation_speed x turn_order first.
    a, b = PRIORITY_PAIR
    if a in palette and b in palette:
        for va, vb in list(product(palette[a][:max_pair], palette[b][:max_pair])):
            plan.variants.append(PlannedVariant(kind="pair", axes={a: va, b: vb}))

    # wording individuals (not experiments).
    if include_paraphrase:
        for value in palette.get(_WORDING_AXIS, [])[:max_per_axis]:
            plan.variants.append(
                PlannedVariant(kind="paraphrase", axes={_WORDING_AXIS: value}, paraphrase_only=True)
            )
    return plan


@dataclass(frozen=True)
class RealizedVariant:
    """A planned variant turned into a concrete guide via one or more
    instantiations (chained for a pair)."""

    planned: PlannedVariant
    guide: Guide
    signature: Signature
    steps: tuple[Instantiation, ...]
    paraphrase_only: bool

    @property
    def all_clean(self) -> bool:
        """Every axis step was a clean single-axis move (pairs need both clean)."""
        return bool(self.steps) and all(s.instantiation_clean for s in self.steps)


def realize_variant(
    planned: PlannedVariant,
    parent_guide: Guide,
    parent_signature: Signature,
    *,
    strategist: PatchStrategist | None = None,
) -> RealizedVariant:
    """Realize a planned variant. A pair is two chained single-axis instantiations
    (each verified independently). A paraphrase changes only wording — same cell."""
    if planned.paraphrase_only:
        # wording never enters the cell key; the individual stays in the parent cell.
        wording = next(iter(planned.axes.values()), "")
        individual = replace(parent_signature, wording=wording)
        return RealizedVariant(
            planned=planned,
            guide=parent_guide,
            signature=individual,
            steps=(),
            paraphrase_only=True,
        )

    guide = parent_guide
    signature = parent_signature
    steps: list[Instantiation] = []
    for axis, value in planned.axes.items():
        inst = instantiate(guide, signature, axis, value, strategist=strategist)
        guide = inst.guide
        signature = inst.child_signature
        steps.append(inst)
    return RealizedVariant(
        planned=planned, guide=guide, signature=signature, steps=tuple(steps), paraphrase_only=False
    )


__all__ = [
    "PRIORITY_PAIR",
    "MutationPlan",
    "PlannedVariant",
    "RealizedVariant",
    "plan_mutations",
    "realize_variant",
]
