"""Descriptor signature — the MAP-Elites cell coordinate of a realized attack.

**The object being fingerprinted is the leaf** — the attack that actually hits
the target. A TAP-Crescendo leaf is the accumulation of every mutation from the
root to it, so its true descriptor vector is the *cumulative path state*, not the
single move applied at the final branch (Option A in the integration contract).

The attack machinery already emits one move per branch at
``trace.extra_metadata['tap_crescendo']['mutation'] = {dimension, feature_value}``
(one of framing / turn_order / persona / carrier_placement / escalation_speed /
wording). We never reconstruct these from raw text — :func:`signature` *reads*
them and walks the attack's genealogy to recover what a leaf actually is.

Cell key (order-independent by construction — two paths reaching the same
cumulative vector share a cell)::

    (objective_class, framing, turn_order, persona, carrier_placement,
     escalation_speed, cross_app_hops_bucket)

``wording`` is the paraphrase axis: it identifies the *individual* within a
cell (together with ``content_hash(guide)``), never the cell. An axis never set
on the path gets an explicit :data:`BASELINE` sentinel — a legitimate, informative
coordinate (thin coverage), not missing data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from advtest_ext.guides.models import Guide, content_hash

BASELINE = "__baseline__"
"""Sentinel for an axis no ancestor on the path ever mutated. A distinct enum
value, never ``None`` — the root anchors it, and a cell carrying sentinels is
real signal (this strategy-region is thinly explored)."""

# The five non-wording mutation dimensions that form the cell key. Order here is
# the canonical cell-key order.
KEY_MUTATION_AXES: tuple[str, ...] = (
    "framing",
    "turn_order",
    "persona",
    "carrier_placement",
    "escalation_speed",
)
_WORDING_AXIS = "wording"
HOPS_BUCKETS: tuple[str, ...] = ("0", "1", "2+")

_MULTITURN_SUFFIX = "_multiturn"
# Map a registered target_name (docs/gmail/... and their *_multiturn twins) to
# the Workspace app whose surface is being crossed. Used only to count
# distinct-app transitions for the cross_app_hops bucket.
_TARGET_APP_ALIASES: dict[str, str] = {
    "google_docs": "docs",
    "docs": "docs",
    "gmail": "gmail",
    "google_sheets": "sheets",
    "sheets": "sheets",
    "google_slides": "slides",
    "slides": "slides",
    "google_calendar": "calendar",
    "calendar": "calendar",
    "google_drive": "drive",
    "drive": "drive",
    "outlook": "outlook",
    "tool_use_agent": "tool_use_agent",
    "agentdojo": "agentdojo",
    "stub": "stub",
}


@dataclass(frozen=True)
class Signature:
    """A leaf's cumulative descriptor vector.

    The first seven fields are the cell key; ``wording`` / ``individual_id`` /
    ``recovered_axes`` describe the individual within the cell and the backfill
    resolution it supports.
    """

    objective_class: str
    framing: str
    turn_order: str
    persona: str
    carrier_placement: str
    escalation_speed: str
    cross_app_hops_bucket: str
    wording: str = BASELINE
    individual_id: str = ""
    recovered_axes: int = 0
    """How many of the five key axes are non-:data:`BASELINE` (drives the
    backfill Coarse/Medium split)."""

    def cell_key(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            self.objective_class,
            self.framing,
            self.turn_order,
            self.persona,
            self.carrier_placement,
            self.escalation_speed,
            self.cross_app_hops_bucket,
        )

    def axis_values(self) -> dict[str, str]:
        """The five key mutation axes as a dict (excludes objective/hops/wording)."""
        return {
            "framing": self.framing,
            "turn_order": self.turn_order,
            "persona": self.persona,
            "carrier_placement": self.carrier_placement,
            "escalation_speed": self.escalation_speed,
        }


# ---------------------------------------------------------------------------
# TAP ancestry index — the tree lives in extra_metadata, NOT the parent_id column
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MutationNode:
    """One TAP tree node: the single move that created it from its parent.

    ``node_id`` / ``parent_id`` are the in-memory TAP ids (``n0``, ``n1-0-1``)
    read from ``extra_metadata['tap_crescendo']``; the persisted
    ``traces.parent_id`` column is NULL for tap_crescendo, so ancestry must be
    walked here. The root (``n0``) carries no mutation → ``dimension is None``.
    """

    node_id: str
    parent_id: str | None
    dimension: str | None
    feature_value: str | None


def _tap_meta(trace: Any) -> dict[str, Any]:
    meta = getattr(trace, "extra_metadata", None)
    if not isinstance(meta, dict):
        return {}
    tc = meta.get("tap_crescendo")
    return tc if isinstance(tc, dict) else {}


def _mutation_of(tc: Mapping[str, Any]) -> tuple[str | None, str | None]:
    mut = tc.get("mutation")
    if not isinstance(mut, dict):
        return None, None
    dim = mut.get("dimension")
    fv = mut.get("feature_value")
    return (dim if isinstance(dim, str) else None, fv if isinstance(fv, str) else None)


def build_node_index(traces: Iterable[Any]) -> dict[str, MutationNode]:
    """Index a run's traces into one :class:`MutationNode` per TAP node id.

    A node emits many turn-traces that all carry the same move; we keep the
    first non-empty mutation seen for a node (they agree). Traces without a
    ``node_id`` (single-step attacks) are ignored — those have no ancestry.
    """
    index: dict[str, MutationNode] = {}
    for trace in traces:
        tc = _tap_meta(trace)
        node_id = tc.get("node_id")
        if not isinstance(node_id, str) or not node_id:
            continue
        dim, fv = _mutation_of(tc)
        existing = index.get(node_id)
        if existing is None:
            parent = tc.get("parent_id")
            index[node_id] = MutationNode(
                node_id=node_id,
                parent_id=parent if isinstance(parent, str) and parent else None,
                dimension=dim,
                feature_value=fv,
            )
        elif existing.dimension is None and dim is not None:
            # Fill in the move from a later turn-trace if the first lacked it.
            index[node_id] = MutationNode(
                node_id=existing.node_id,
                parent_id=existing.parent_id,
                dimension=dim,
                feature_value=fv,
            )
    return index


def _walk_axes(
    leaf_node_id: str, index: Mapping[str, MutationNode]
) -> tuple[dict[str, str], str, int]:
    """Nearest-wins ancestry walk from ``leaf_node_id`` to the root.

    For each key axis, take the ``feature_value`` of the nearest ancestor (leaf
    included) that mutated it — so a later ``framing=urgency`` correctly
    overrides an earlier ``framing=authority`` on the same path. Returns
    ``(axis_values, wording, recovered_count)``.
    """
    axes: dict[str, str] = {axis: BASELINE for axis in KEY_MUTATION_AXES}
    wording = BASELINE
    seen: set[str] = set()
    node_id: str | None = leaf_node_id
    guard = 0
    while node_id is not None and node_id in index and guard < 100_000:
        guard += 1
        node = index[node_id]
        dim = node.dimension
        if dim in KEY_MUTATION_AXES and dim not in seen:
            axes[dim] = node.feature_value or BASELINE
            seen.add(dim)
        elif dim == _WORDING_AXIS and wording == BASELINE:
            wording = node.feature_value or BASELINE
        node_id = node.parent_id
    recovered = sum(1 for axis in KEY_MUTATION_AXES if axes[axis] != BASELINE)
    return axes, wording, recovered


# ---------------------------------------------------------------------------
# objective_class — layered, deterministic, never NULL
# ---------------------------------------------------------------------------


def resolve_objective_class(
    behavior_id: str,
    *,
    behavior_categories: Mapping[str, str] | None = None,
    guide: Guide | None = None,
) -> str:
    """Resolve the objective class for a behaviour/guide id.

    Layered fallback (most trace ``behavior_id``s are guide ids, not entries in
    ``seed_behaviors.yaml``): the catalogue ``BehaviorCategory`` when known, else
    the guide's ``threat_class``, else the ``behavior_id`` itself. ``behavior_id``
    is NOT NULL on every trace, so this is always defined.
    """
    if behavior_categories and behavior_id in behavior_categories:
        return behavior_categories[behavior_id]
    if guide is not None and getattr(guide, "threat_class", None):
        return str(guide.threat_class)
    return behavior_id or (guide.id if guide is not None else "")


# ---------------------------------------------------------------------------
# cross_app_hops — cross-app indirection from runs.config.phases (NOT depth)
# ---------------------------------------------------------------------------


def _target_to_app(target_name: str | None) -> str | None:
    if not isinstance(target_name, str) or not target_name.strip():
        return None
    name = target_name.strip().lower()
    if name.endswith(_MULTITURN_SUFFIX):
        name = name[: -len(_MULTITURN_SUFFIX)]
    return _TARGET_APP_ALIASES.get(name, name or None)


def count_cross_app_transitions(run_config: Mapping[str, Any] | None) -> int:
    """Count distinct-app transitions across a run's ``phases`` (e.g. Gmail→Docs).

    This is cross-app indirection, NOT ``traces.depth`` (turn depth, already
    represented by turn_order / escalation_speed). A run with no ``phases`` or a
    single app has 0 transitions.
    """
    if not isinstance(run_config, Mapping):
        return 0
    phases = run_config.get("phases")
    if not isinstance(phases, list):
        return 0
    transitions = 0
    prev: str | None = None
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        app = _target_to_app(phase.get("target_name"))
        if app is None:
            continue
        if prev is not None and app != prev:
            transitions += 1
        prev = app
    return transitions


def hops_bucket(transitions: int) -> str:
    """Bucket a transition count into ``{"0", "1", "2+"}``."""
    if transitions <= 0:
        return "0"
    if transitions == 1:
        return "1"
    return "2+"


# ---------------------------------------------------------------------------
# signature(trace | guide)
# ---------------------------------------------------------------------------


def signature(
    obj: Any,
    *,
    node_index: Mapping[str, MutationNode] | None = None,
    run_config: Mapping[str, Any] | None = None,
    behavior_categories: Mapping[str, str] | None = None,
    guide: Guide | None = None,
) -> Signature:
    """Deterministic descriptor signature for a leaf trace (or a bare guide).

    * **trace** (has ``behavior_id`` / ``extra_metadata``): reads the leaf's TAP
      node id and walks ``node_index`` (Option A). Without a ``node_index`` (a
      single-step attack, one node) it reads only this trace's own move. Axes
      never set stay :data:`BASELINE`.
    * **guide**: a baseline signature keyed on the guide's objective — all key
      axes :data:`BASELINE`, ``individual_id = content_hash(guide)``.
    """
    if isinstance(obj, Guide):
        return _signature_from_guide(obj, run_config=run_config, behavior_categories=behavior_categories)
    return _signature_from_trace(
        obj,
        node_index=node_index,
        run_config=run_config,
        behavior_categories=behavior_categories,
        guide=guide,
    )


def _signature_from_trace(
    trace: Any,
    *,
    node_index: Mapping[str, MutationNode] | None,
    run_config: Mapping[str, Any] | None,
    behavior_categories: Mapping[str, str] | None,
    guide: Guide | None,
) -> Signature:
    behavior_id = str(getattr(trace, "behavior_id", "") or "")
    objective_class = resolve_objective_class(
        behavior_id, behavior_categories=behavior_categories, guide=guide
    )
    tc = _tap_meta(trace)
    node_id = tc.get("node_id")
    axes: dict[str, str] = {axis: BASELINE for axis in KEY_MUTATION_AXES}
    wording = BASELINE
    recovered = 0
    if node_index is not None and isinstance(node_id, str) and node_id in node_index:
        axes, wording, recovered = _walk_axes(node_id, node_index)
    else:
        # Single node / single-step: read this trace's own emitted move only.
        dim, fv = _mutation_of(tc)
        if dim in KEY_MUTATION_AXES:
            axes[dim] = fv or BASELINE
            recovered = 1 if axes[dim] != BASELINE else 0
        elif dim == _WORDING_AXIS:
            wording = fv or BASELINE
    hops = hops_bucket(count_cross_app_transitions(run_config))
    if guide is not None:
        individual_id = content_hash(guide)
    else:
        individual_id = str(
            getattr(trace, "payload_id", "") or (node_id if isinstance(node_id, str) else "") or getattr(trace, "id", "")
        )
    return Signature(
        objective_class=objective_class,
        framing=axes["framing"],
        turn_order=axes["turn_order"],
        persona=axes["persona"],
        carrier_placement=axes["carrier_placement"],
        escalation_speed=axes["escalation_speed"],
        cross_app_hops_bucket=hops,
        wording=wording,
        individual_id=individual_id,
        recovered_axes=recovered,
    )


def _signature_from_guide(
    guide: Guide,
    *,
    run_config: Mapping[str, Any] | None,
    behavior_categories: Mapping[str, str] | None,
) -> Signature:
    objective_class = resolve_objective_class(
        guide.id, behavior_categories=behavior_categories, guide=guide
    )
    hops = hops_bucket(count_cross_app_transitions(run_config))
    return Signature(
        objective_class=objective_class,
        framing=BASELINE,
        turn_order=BASELINE,
        persona=BASELINE,
        carrier_placement=BASELINE,
        escalation_speed=BASELINE,
        cross_app_hops_bucket=hops,
        wording=BASELINE,
        individual_id=content_hash(guide),
        recovered_axes=0,
    )


def coarse_projection(sig: Signature) -> tuple[str, str | None]:
    """Project a signature down to ``(objective_class, carrier_placement | None)``.

    The backfill coordinate when full descriptors can't be recovered; a
    :data:`BASELINE` carrier maps to ``None``.
    """
    carrier = sig.carrier_placement
    return (sig.objective_class, None if carrier == BASELINE else carrier)


__all__ = [
    "BASELINE",
    "HOPS_BUCKETS",
    "KEY_MUTATION_AXES",
    "MutationNode",
    "Signature",
    "build_node_index",
    "coarse_projection",
    "count_cross_app_transitions",
    "hops_bucket",
    "resolve_objective_class",
    "signature",
]
