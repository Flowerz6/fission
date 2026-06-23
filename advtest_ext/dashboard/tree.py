"""Attack-tree assembly + ASCII rendering — shared between the
live ``/runs/{id}/tree`` view and the PDF report.

Originally inlined in :mod:`advtest_ext.dashboard.app`. Extracted
when :mod:`advtest_ext.dashboard.report` started needing the same
forest to embed trees in the PDF: the report module already
deferred-imports back into ``app`` (for ``build_report_context``
re-entry from routes), so a direct ``app → tree → app`` cycle would
have been brittle. Living in a leaf module both sides can import is
cleaner.
"""

from __future__ import annotations

from collections import defaultdict
from types import SimpleNamespace
from typing import Any


def _score_of(trace: Any) -> float:
    js = getattr(trace, "judge_scores", None)
    try:
        return float(js[0].score) if js else 0.0
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _conversation_spec(trace: Any) -> tuple[str, Any, int, Any] | None:
    """If ``trace`` is one turn of a multi-turn attack, return
    ``(conv_key, depth, parent_conv_key)``-ish: a tuple
    ``(kind, key, depth, parent_key)``. Else ``None`` (single-step).

    A "conversation" is one tree node:
      * tap_crescendo — keyed by ``node_id``; parent is the parent
        node; depth is the tree depth (so the tree branches by node).
      * tap_chain     — keyed by replay ``round`` (each replay is a
        fresh conversation); rendered as siblings.
      * crescendo     — the whole run is one conversation / one node.
    """
    meta = getattr(trace, "extra_metadata", None)
    if not isinstance(meta, dict):
        return None
    # Phase prefix: node ids (n0, n1-0-1, …) repeat across phases, so a
    # multi-phase run would otherwise collapse phase-0's n0 and phase-1's
    # n0 into ONE conversation (losing a whole phase). Namespacing the
    # conversation key by phase index keeps phases distinct.
    phase = meta.get("phase")
    ph = int(phase["index"]) if isinstance(phase, dict) and "index" in phase else None
    tcr = meta.get("tap_crescendo")
    if isinstance(tcr, dict):
        parent = tcr.get("parent_id")
        node_key = (ph, tcr.get("node_id", "node"))
        parent_key = (ph, parent) if parent else None
        return (
            "tap_crescendo",
            node_key,
            int(tcr.get("depth", 0)),
            ("tap_crescendo", parent_key) if parent else None,
        )
    tc = meta.get("tap_chain")
    if isinstance(tc, dict):
        rnd = int(tc.get("round", 0))
        return ("tap_chain", rnd, rnd, None)
    cr = meta.get("crescendo")
    if isinstance(cr, dict):
        return ("crescendo", "conv", 0, None)
    return None


def collapse_conversations(traces: list[Any]) -> list[Any]:
    """Collapse the per-turn traces of a multi-turn attack into ONE node
    per conversation, so the attack tree shows conversations — each
    assessed by its best turn (verdict + score) — instead of one node
    per prompt. tap_crescendo's parent edges follow the conversation
    tree, so it branches by node. Non-multi-turn traces pass through
    unchanged. Per-turn detail still lives in the transcript view.

    The synthesized node carries the best turn's ``id`` (so clicking it
    opens that turn) plus the conversation's depth/parent, which is all
    :func:`build_tree_forest` and the tree template read.
    """
    convs: dict[tuple[str, Any], list[Any]] = {}
    info: dict[tuple[str, Any], tuple[int, Any, Any, Any]] = {}
    order: list[tuple[str, Any]] = []
    passthrough: list[Any] = []

    for trace in traces:
        spec = _conversation_spec(trace)
        if spec is None:
            passthrough.append(trace)
            continue
        kind, k, depth, parent_key = spec
        key = (kind, k)
        if key not in convs:
            convs[key] = []
            order.append(key)
            info[key] = (
                depth,
                parent_key,
                getattr(trace, "behavior_id", "?"),
                getattr(trace, "technique_id", None),
            )
        convs[key].append(trace)

    rep = {key: max(convs[key], key=_score_of) for key in order}  # best turn per conversation
    nodes: list[Any] = []
    for key in order:
        depth, parent_key, behavior, technique = info[key]
        best = rep[key]
        parent_id = getattr(rep[parent_key], "id", None) if parent_key in rep else None
        nodes.append(
            SimpleNamespace(
                id=getattr(best, "id", None),
                parent_id=parent_id,
                depth=depth,
                verdict=getattr(best, "verdict", None),
                judge_scores=getattr(best, "judge_scores", []),
                behavior_id=behavior,
                technique_id=technique,
                # Carry the phase tag through the collapse so the per-phase
                # tree grouping still works — without this, the synthesized
                # node has no extra_metadata and every conversation falls
                # into the single unlabelled group (flattening the phases).
                extra_metadata=getattr(best, "extra_metadata", None),
            )
        )
    return nodes + passthrough


def ascii_tree_lines(roots: list[dict[str, object]]) -> list[dict[str, object]]:
    """Walk ``roots`` in pre-order and emit one rendering record per
    node with the box-drawing prefix that makes the printed output
    look like ``tree(1)``::

        ├── child_a
        │   └── grandchild
        └── child_b

    Each record carries ``prefix`` (leading "│   " / "    " columns),
    ``connector`` ("├── " / "└── " / "" for the sole-root case),
    and ``trace`` (the underlying Trace row).

    Multi-root cells (typical TAP shape where depth-1 branches'
    parent — the synthetic ``tap-root`` sentinel — is never persisted)
    render as multiple top-level sibling lines.
    """
    out: list[dict[str, object]] = []

    def _walk(
        node: dict[str, object],
        prefix: str,
        connector: str,
        is_last: bool,
    ) -> None:
        out.append({"prefix": prefix, "connector": connector, "trace": node["trace"]})
        children = node["children"]
        assert isinstance(children, list)
        next_prefix = prefix + ("    " if is_last else "│   ")
        for i, child in enumerate(children):
            child_is_last = i == len(children) - 1
            _walk(
                child,
                next_prefix,
                "└── " if child_is_last else "├── ",
                child_is_last,
            )

    for i, root in enumerate(roots):
        root_is_last = i == len(roots) - 1
        _walk(
            root,
            prefix="",
            connector=("└── " if root_is_last else "├── ") if len(roots) > 1 else "",
            is_last=root_is_last,
        )
    return out


def build_tree_forest(traces: list[object]) -> list[dict[str, object]]:
    """Group ``traces`` into one tree per ``(behavior_id, technique_id)``
    cell, parent→children edges from ``Trace.parent_id``.

    Returns a list of cells, each shaped::

        {
            "behavior_id": ...,
            "technique_id": ...,
            "n_traces": int,
            "n_success": int,
            "roots": [TreeNode, ...],
            "lines": [{"prefix": str, "connector": str, "trace": Trace}, ...],
        }

    A trace counts as a root within its cell if its ``parent_id`` is
    ``None`` OR points outside the cell (e.g. the synthetic TAP root
    that doesn't exist as a row in the DB).

    For non-tree attacks (PAIR linear chains, single-step), each
    trace becomes its own root with no children — the same template
    renders both shapes without branching.
    """
    by_cell: dict[tuple[str, str], list[object]] = defaultdict(list)
    for trace in traces:
        key = (
            getattr(trace, "behavior_id", "?"),
            getattr(trace, "technique_id", None) or "(none)",
        )
        by_cell[key].append(trace)

    forest: list[dict[str, object]] = []
    for (behavior_id, technique_id), cell_traces in sorted(by_cell.items()):
        ids_in_cell = {getattr(t, "id", None) for t in cell_traces}
        children_by_parent: dict[str | None, list[object]] = defaultdict(list)
        for trace in cell_traces:
            parent = getattr(trace, "parent_id", None)
            key_parent = parent if parent in ids_in_cell else None
            children_by_parent[key_parent].append(trace)

        def _as_node(
            trace: object,
            _children: dict[str | None, list[object]] = children_by_parent,
        ) -> dict[str, object]:
            tid = getattr(trace, "id", None)
            return {
                "trace": trace,
                "children": [_as_node(c) for c in _children.get(tid, [])],
            }

        roots = [_as_node(t) for t in children_by_parent[None]]
        forest.append(
            {
                "behavior_id": behavior_id,
                "technique_id": technique_id,
                "n_traces": len(cell_traces),
                "n_success": sum(
                    1 for t in cell_traces if getattr(t, "verdict", None) == "success"
                ),
                "roots": roots,
                "lines": ascii_tree_lines(roots),
            }
        )
    return forest


def _trace_phase(trace: object) -> dict[str, object] | None:
    """Read a trace's phase tag from ``extra_metadata['phase']``.
    Returns ``None`` for single-phase runs (no tag)."""
    meta = getattr(trace, "extra_metadata", None)
    if isinstance(meta, dict):
        ph = meta.get("phase")
        if isinstance(ph, dict):
            return ph
    return None


def build_phase_forests(traces: list[object]) -> list[dict[str, object]]:
    """Partition traces by phase, then build one tree forest per phase.

    Returns an ordered list of phase groups, each shaped::

        {
            "index": int,          # phase order (0-based)
            "label": str,          # human-readable phase name
            "total": int,          # total phases in the run
            "n_traces": int,
            "n_success": int,
            "forest": [cell, ...], # same shape as build_tree_forest
        }

    For a single-phase (non-phased) run — no ``phase`` metadata on any
    trace — returns a single group with ``index=0`` and an empty label,
    so callers can render it as one unlabelled tree section. This keeps
    the template uniform across phased and non-phased runs.
    """
    by_phase: dict[int, list[object]] = defaultdict(list)
    labels: dict[int, str] = {}
    total = 1
    any_phase = False
    for trace in traces:
        ph = _trace_phase(trace)
        if ph is None:
            by_phase[0].append(trace)
            continue
        any_phase = True
        idx = int(ph.get("index", 0))
        by_phase[idx].append(trace)
        labels[idx] = str(ph.get("label", f"Phase {idx + 1}"))
        total = max(total, int(ph.get("total", 1)))

    groups: list[dict[str, object]] = []
    for idx in sorted(by_phase):
        phase_traces = by_phase[idx]
        groups.append(
            {
                "index": idx,
                "label": labels.get(idx, "") if any_phase else "",
                "total": total,
                "n_traces": len(phase_traces),
                "n_success": sum(
                    1 for t in phase_traces if getattr(t, "verdict", None) == "success"
                ),
                "forest": build_tree_forest(phase_traces),
            }
        )
    return groups


__all__ = [
    "ascii_tree_lines",
    "build_phase_forests",
    "build_tree_forest",
    "collapse_conversations",
]
