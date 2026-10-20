"""Best-effort, guide-keyed, resolution-per-row backfill of the archive.

Walks historical traces that carry a ``guide_id`` and reconstructs cell
observations from the attack's genealogy (Option A). It NEVER fabricates a
native-resolution cell from history — each row is tagged with the resolution its
recovered descriptors actually support:

* recovers ``objective_class + carrier`` only            → **coarse**
* recovers those + ≥2 more mutation dims                 → **medium**

Rows with a null ``guide_id`` are skipped (forward-only). A row whose carrier
cannot be recovered at all (baseline ``carrier_placement`` *and* null
``technique_id``) is dropped (``null_carrier``); a row with no ``objective_class``
is dropped (``missing_objective_class`` — ≈0, since ``behavior_id`` is NOT NULL).

Independence: for a tap_crescendo run only leaf nodes are observed (one
root-to-leaf path = one update), scored at the leaf's terminal turn; internal
nodes are ancestry only. Single-step traces are each their own leaf.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from advtest_ext.archive.fingerprint import Archive, TargetProfileKey
from advtest_ext.archive.posterior import Resolution
from advtest_ext.archive.signature import (
    BASELINE,
    KEY_MUTATION_AXES,
    MutationNode,
    Signature,
    build_node_index,
    signature,
)
from advtest_ext.core.orch_trace_meta import run_outcome_for_trace

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from advtest_ext.guides.models import Guide

_CARRIER_AXIS = "carrier_placement"
_OTHER_KEY_AXES = tuple(a for a in KEY_MUTATION_AXES if a != _CARRIER_AXIS)


@dataclass
class BackfillReport:
    """The emitted histogram — assert invariants against it, not a hard-coded split."""

    coarse: int = 0
    medium: int = 0
    forward_only_skipped: int = 0  # null guide_id
    dropped_missing_objective_class: int = 0
    dropped_null_carrier: int = 0
    observed_effects: dict[str, int] = field(default_factory=dict)

    @property
    def dropped_total(self) -> int:
        return self.dropped_missing_objective_class + self.dropped_null_carrier

    def as_dict(self) -> dict[str, Any]:
        return {
            "coarse": self.coarse,
            "medium": self.medium,
            "forward_only_skipped": self.forward_only_skipped,
            "dropped": {
                "missing_objective_class": self.dropped_missing_objective_class,
                "null_carrier": self.dropped_null_carrier,
                "total": self.dropped_total,
            },
            "observed_effects": dict(self.observed_effects),
        }


def _signal_verified(trace: Any) -> bool:
    """A deterministic (non-LLM) signal fired — the SIGNAL-VERIFIED predicate
    (``dashboard/app.py`` / harvest flywheel), read off the trace's signals."""
    sigs = getattr(trace, "signals", None) or []
    return any(
        getattr(s, "fired", False) and getattr(s, "kind", "") != "llm_judge_verdict" for s in sigs
    )


def _judge_confidence(trace: Any) -> float:
    scores = [getattr(js, "score", 0.0) for js in getattr(trace, "judge_scores", None) or []]
    return max(scores) if scores else 0.0


def _tap_node_id(trace: Any) -> str | None:
    meta = getattr(trace, "extra_metadata", None)
    if not isinstance(meta, dict):
        return None
    tc = meta.get("tap_crescendo")
    if not isinstance(tc, dict):
        return None
    node_id = tc.get("node_id")
    return node_id if isinstance(node_id, str) and node_id else None


def _turn(trace: Any) -> int:
    meta = getattr(trace, "extra_metadata", None)
    tc = meta.get("tap_crescendo") if isinstance(meta, dict) else None
    if isinstance(tc, dict):
        turn = tc.get("turn")
        if isinstance(turn, int):
            return turn
    return int(getattr(trace, "depth", 0) or 0)


def _effective_carrier(sig: Signature, terminal_trace: Any) -> str | None:
    """The carrier for backfill: the ancestry-recovered ``carrier_placement``
    axis, else the trace's ``technique_id`` (the contract's single-step carrier
    column), else ``None`` (→ drop as null_carrier)."""
    if sig.carrier_placement != BASELINE:
        return sig.carrier_placement
    technique = getattr(terminal_trace, "technique_id", None)
    return technique if isinstance(technique, str) and technique else None


def _observe_leaf(
    archive: Archive,
    terminal_trace: Any,
    sig: Signature,
    report: BackfillReport,
) -> None:
    if not sig.objective_class:
        report.dropped_missing_objective_class += 1
        return
    carrier = _effective_carrier(sig, terminal_trace)
    if carrier is None:
        report.dropped_null_carrier += 1
        return
    # Fold a technique_id-recovered carrier into the cell coordinate so the key
    # is consistent with "carrier recovered" (contract: carrier ∈ {carrier_placement,
    # technique_id}). Never native — history only ever yields medium/coarse.
    if carrier != sig.carrier_placement:
        sig = replace(sig, carrier_placement=carrier)
    other_recovered = sum(1 for axis in _OTHER_KEY_AXES if sig.axis_values()[axis] != BASELINE)
    resolution: Resolution = "medium" if other_recovered >= 2 else "coarse"
    outcome = run_outcome_for_trace(terminal_trace)
    result = archive.observe(
        sig,
        outcome=outcome,
        signal_verified=_signal_verified(terminal_trace),
        resolution=resolution,
        judge_confidence=_judge_confidence(terminal_trace),
        trace_id=getattr(terminal_trace, "id", None),
    )
    report.observed_effects[result.effect] = report.observed_effects.get(result.effect, 0) + 1
    # A dropped/observed row still counts toward its resolution histogram — the
    # resolution reflects descriptor recovery, independent of the verdict.
    if resolution == "medium":
        report.medium += 1
    else:
        report.coarse += 1


def _leaf_terminal_traces(traces: list[Any], index: dict[str, MutationNode]) -> dict[str, Any]:
    """Return ``{leaf_node_id: terminal_trace}`` for a run's tap_crescendo nodes.

    A leaf node is one no other node names as parent; its terminal trace is the
    highest-turn trace of that node (the scored end of the conversation).
    """
    node_traces: dict[str, list[Any]] = defaultdict(list)
    for trace in traces:
        node_id = _tap_node_id(trace)
        if node_id is not None:
            node_traces[node_id].append(trace)
    parents = {n.parent_id for n in index.values() if n.parent_id}
    leaves: dict[str, Any] = {}
    for node_id, node_rows in node_traces.items():
        if node_id in parents:
            continue  # internal node — ancestry only
        leaves[node_id] = max(node_rows, key=_turn)
    return leaves


def backfill_from_history(
    session: Session,
    *,
    profile: TargetProfileKey,
    guides_by_id: dict[str, Guide] | None = None,
    behavior_categories: dict[str, str] | None = None,
    run_limit: int = 10_000,
) -> tuple[Archive, BackfillReport]:
    """Reconstruct an :class:`Archive` for ``profile`` from guide-linked history.

    Returns the populated archive and a :class:`BackfillReport` histogram. Call
    :func:`advtest_ext.archive.fingerprint.persist_archive` to write it.
    """
    from advtest_ext.storage.repository import list_runs, list_traces_for_run

    guides_by_id = guides_by_id or {}
    archive = Archive(profile=profile)
    report = BackfillReport()

    for run in list_runs(session, limit=run_limit):
        run_config = run.config if isinstance(run.config, dict) else {}
        traces = list_traces_for_run(session, run.id)
        guide_id = getattr(run, "guide_id", None)
        if not guide_id:
            # Forward-only: no guide to key the reconstruction on.
            report.forward_only_skipped += len(traces)
            continue
        guide = guides_by_id.get(guide_id)
        index = build_node_index(traces)
        if index:
            leaves = _leaf_terminal_traces(traces, index)
            for terminal in leaves.values():
                sig = signature(
                    terminal,
                    node_index=index,
                    run_config=run_config,
                    behavior_categories=behavior_categories,
                    guide=guide,
                )
                _observe_leaf(archive, terminal, sig, report)
        else:
            # Single-step run: each trace is its own leaf.
            for terminal in traces:
                sig = signature(
                    terminal,
                    run_config=run_config,
                    behavior_categories=behavior_categories,
                    guide=guide,
                )
                _observe_leaf(archive, terminal, sig, report)
    return archive, report


__all__ = ["BackfillReport", "backfill_from_history"]
