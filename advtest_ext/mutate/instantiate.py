"""Deterministic single-axis instantiation with verification (the Part-D flag).

``instantiate(parent_guide, dimension, feature_value)`` produces a variant that
moves exactly ONE descriptor axis to a requested value, holding the others fixed.
Two mechanisms cooperate:

* **Isolation (hard).** The patch may only touch the operator's ``mutable_fields``
  — the same invariant ``GuideMutationMaterializer.apply`` enforces. A patch that
  reaches another operator's fields is *structural bleed* and is caught reliably.
* **Verification via Part A ``signature()`` (soft for semantics).** We append a
  synthetic node carrying the strategist's CLAIMED axis moves onto the parent's
  ancestry and re-derive the child :class:`Signature`. ``instantiation_clean`` is
  True iff exactly one cell-key axis changed, to the requested value.

**The carrier axis** goes through the deterministic ``_tip_patch`` path (literal
value → patch); its clean-ness is trustworthy. **The semantic axes**
(framing/persona/turn_order/escalation_speed) go through an injected
:data:`PatchStrategist`; its ``claimed_realized_axes`` is a *self-report*, not
ground truth — a strategist that bleeds semantically will tend to mis-report it,
so ``instantiation_clean=True`` is necessary but NOT sufficient for a clean
experiment (see :mod:`advtest_ext.mutate.effects`, which widens intervals for a
self-report-only basis).

> Real-impl note: the ``PatchStrategist`` prompt must be "realize exactly this
> feature_value on this axis, hold all else fixed" — do NOT reuse
> ``tap_crescendo._propose_strategy``'s "MATERIALLY different" prompt; its
> diversity objective is the opposite of single-axis control and would guarantee
> a high bleed rate. Reuse ``cell_generator``'s completion callable, not its (or
> crescendo's) prompt intent.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from advtest_ext.archive.signature import (
    BASELINE,
    KEY_MUTATION_AXES,
    Signature,
    build_node_index,
    signature,
)
from advtest_ext.guides.models import Guide, GuideMutationOperator, content_hash

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.document.target import DocumentTarget

CARRIER_AXIS = "carrier_placement"
SEMANTIC_AXES: tuple[str, ...] = ("framing", "persona", "turn_order", "escalation_speed")
STRUCTURAL_BLEED = "__structural__"


@dataclass(frozen=True)
class AxisPatch:
    """A strategist's proposal for one axis.

    ``artifact_patch`` maps ``mutable_fields`` → new content. ``claimed_realized_axes``
    is the strategist's OWN CLAIM of which axes it moved and to what value — a
    self-report, never verified. ``seed``/``prompt`` make an LLM path reproducible.
    """

    artifact_patch: dict[str, Any]
    claimed_realized_axes: dict[str, str]
    seed: int | None = None
    prompt: str | None = None


class PatchStrategist(Protocol):
    """Maps ``(guide, operator, feature_value)`` → :class:`AxisPatch`.

    The real implementation reuses ``cell_generator``'s completion callable with a
    single-axis "hold all else fixed" prompt (NOT the crescendo diversity prompt).
    Tests inject scripted strategists.
    """

    def __call__(self, guide: Guide, operator: GuideMutationOperator, feature_value: str) -> AxisPatch: ...


@dataclass(frozen=True)
class Instantiation:
    """The result of a single-axis instantiation."""

    guide: Guide
    dimension: str
    feature_value: str
    parent_signature: Signature
    child_signature: Signature
    artifact_patch: dict[str, Any]
    claimed_realized_axes: dict[str, str]
    instantiation_clean: bool
    bleed: tuple[str, ...]
    clean_basis: str  # "deterministic" (carrier/TIP) | "self_report" (semantic)
    seed: int | None
    prompt: str | None

    @property
    def eligible_for_effects(self) -> bool:
        """Only clean instantiations count toward single-axis effect estimates."""
        return self.instantiation_clean


def _operator_for(guide: Guide, dimension: str) -> GuideMutationOperator:
    for operator in guide.mutation_operators:
        if operator.dimension == dimension:
            return operator
    raise ValueError(f"guide {guide.id!r} declares no mutation operator for axis {dimension!r}")


class _NoTarget:
    """A do-nothing stand-in so we can reuse the materializer's deterministic
    ``_tip_patch`` without staging (it never touches ``target``)."""


def _carrier_patch(parent_guide: Guide, operator: GuideMutationOperator, feature_value: str) -> AxisPatch:
    """Deterministic carrier patch via the existing ``_tip_patch`` path."""
    from advtest_ext.guides.mutation_runtime import GuideMutationMaterializer

    scheme = feature_value.split(":", 1)[1] if feature_value.startswith("tip:") else feature_value
    materializer = GuideMutationMaterializer(guide=parent_guide, target=_no_target())
    # Reuse the materializer's deterministic TIP patch (single source of truth).
    patch = materializer._tip_patch(operator, f"tip:{scheme}")
    return AxisPatch(artifact_patch=patch, claimed_realized_axes={CARRIER_AXIS: feature_value}, seed=0)


def _no_target() -> DocumentTarget:
    from typing import cast

    return cast("DocumentTarget", _NoTarget())


class _SynthTrace:
    """A minimal trace carrying one tap_crescendo mutation node (for signature())."""

    def __init__(self, node_id: str, parent_id: str | None, dimension: str | None, feature_value: str | None) -> None:
        self.behavior_id = "__synthetic__"
        self.payload_id = node_id
        self.id = node_id
        mutation = {"dimension": dimension, "feature_value": feature_value} if dimension else {}
        self.extra_metadata = {"tap_crescendo": {"node_id": node_id, "parent_id": parent_id, "turn": 1, "mutation": mutation}}


def _child_signature(parent: Signature, claimed_realized_axes: Mapping[str, str], child_guide: Guide) -> Signature:
    """Re-derive the child Signature by running Part A ``signature()`` over the
    parent's ancestry with the claimed axis moves appended as new nodes."""
    nodes: list[tuple[str, str | None, str | None, str | None]] = [("n0", None, None, None)]
    prev = "n0"
    counter = 1
    # Reconstruct the parent's non-baseline axes as ancestry nodes.
    for axis in KEY_MUTATION_AXES:
        value = getattr(parent, axis)
        if value != BASELINE:
            node_id = f"np{counter}"
            nodes.append((node_id, prev, axis, value))
            prev = node_id
            counter += 1
    # Append the claimed child moves (newest = nearest wins).
    for axis, value in claimed_realized_axes.items():
        node_id = f"nc{counter}"
        nodes.append((node_id, prev, axis, value))
        prev = node_id
        counter += 1
    traces = [_SynthTrace(n, p, d, f) for (n, p, d, f) in nodes]
    index = build_node_index(traces)
    leaf_trace = traces[-1]
    raw = signature(leaf_trace, node_index=index)
    return Signature(
        objective_class=parent.objective_class,
        framing=raw.framing,
        turn_order=raw.turn_order,
        persona=raw.persona,
        carrier_placement=raw.carrier_placement,
        escalation_speed=raw.escalation_speed,
        cross_app_hops_bucket=parent.cross_app_hops_bucket,  # instantiation never changes hops
        wording=BASELINE,
        individual_id=content_hash(child_guide),
        recovered_axes=raw.recovered_axes,
    )


def _apply_patch(parent_guide: Guide, artifact_patch: Mapping[str, Any]) -> Guide:
    """Produce the variant guide by applying the patch (reusing the materializer's
    allow-listed dotted-path setter)."""
    from advtest_ext.guides.mutation_runtime import _set_guide_path

    variant = parent_guide.model_copy(deep=True)
    for path, value in artifact_patch.items():
        _set_guide_path(variant, path, value)
    return variant


def instantiate(
    parent_guide: Guide,
    parent_signature: Signature,
    dimension: str,
    feature_value: str,
    *,
    strategist: PatchStrategist | None = None,
    seed: int | None = None,
) -> Instantiation:
    """Instantiate a variant that moves ``dimension`` to ``feature_value``.

    ``carrier_placement`` uses the deterministic TIP path; the semantic axes use
    the injected ``strategist``. Verification certifies exactly-one-axis via Part A
    ``signature()``.
    """
    operator = _operator_for(parent_guide, dimension)
    if dimension == CARRIER_AXIS:
        axis_patch = _carrier_patch(parent_guide, operator, feature_value)
        clean_basis = "deterministic"
    else:
        if strategist is None:
            raise ValueError(f"a PatchStrategist is required to instantiate semantic axis {dimension!r}")
        axis_patch = strategist(parent_guide, operator, feature_value)
        clean_basis = "self_report"

    allowed = set(operator.mutable_fields)
    structural_bleed = bool(set(axis_patch.artifact_patch) - allowed)

    child_guide = _apply_patch(parent_guide, axis_patch.artifact_patch)
    child_signature = _child_signature(parent_signature, axis_patch.claimed_realized_axes, child_guide)

    changed = [a for a in KEY_MUTATION_AXES if getattr(child_signature, a) != getattr(parent_signature, a)]
    bleed_axes = tuple(a for a in changed if a != dimension)
    bleed = bleed_axes + ((STRUCTURAL_BLEED,) if structural_bleed else ())
    clean = (
        not structural_bleed
        and changed == [dimension]
        and getattr(child_signature, dimension) == feature_value
    )
    return Instantiation(
        guide=child_guide,
        dimension=dimension,
        feature_value=feature_value,
        parent_signature=parent_signature,
        child_signature=child_signature,
        artifact_patch=dict(axis_patch.artifact_patch),
        claimed_realized_axes=dict(axis_patch.claimed_realized_axes),
        instantiation_clean=clean,
        bleed=bleed,
        clean_basis=clean_basis,
        seed=axis_patch.seed if seed is None else seed,
        prompt=axis_patch.prompt,
    )


__all__ = [
    "CARRIER_AXIS",
    "SEMANTIC_AXES",
    "STRUCTURAL_BLEED",
    "AxisPatch",
    "Instantiation",
    "PatchStrategist",
    "instantiate",
]
