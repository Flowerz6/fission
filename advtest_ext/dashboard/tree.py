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

import re
from collections import defaultdict
from types import SimpleNamespace
from typing import Any

from advtest_ext.dashboard.presentation import attack_label, objective_label


def _score_of(trace: Any) -> float:
    js = getattr(trace, "judge_scores", None)
    try:
        return max((float(score.score) for score in js), default=0.0) if js else 0.0
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

    verdict_rank = {"success": 3, "partial": 2, "refused": 1, "error": 0}
    rep = {
        key: max(
            convs[key],
            key=lambda trace: (
                verdict_rank.get(getattr(trace, "verdict", None) or "", 0),
                _score_of(trace),
            ),
        )
        for key in order
    }  # best turn per conversation
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
                # Carry the winning turn's prompt/response so the Cytoscape
                # tree's node-detail panel can show the real transcript inline
                # for collapsed multi-turn conversations (single-step traces
                # already expose these on the ORM row).
                attack_name=getattr(best, "attack_name", ""),
                timestamp=min(
                    (t.timestamp for t in convs[key] if getattr(t, "timestamp", None)), default=None
                ),
                ended_at=max(
                    (t.timestamp for t in convs[key] if getattr(t, "timestamp", None)), default=None
                ),
                turn_count=len(convs[key]),
                raw_request=getattr(best, "raw_request", None),
                raw_response=getattr(best, "raw_response", None),
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


def _trace_phase(trace: object) -> dict[str, Any] | None:
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


# ---------------------------------------------------------------------------
# Cytoscape graph payload
# ---------------------------------------------------------------------------
#
# The live attack tree is rendered client-side with Cytoscape.js (dagre
# layout) instead of Mermaid. Both the initial server render (embedded as
# JSON per cell in ``runs/_tree_live.html``) AND the ``/runs/{id}/_tree.json``
# polling endpoint go through :func:`tree_graph_payload` so the two can never
# drift: the client diffs the polled payload against what it already mounted
# and only ``cy.add()``s the new nodes/edges (no full repaint → no flicker).

# ``Trace.verdict`` → Cytoscape node status (drives colour + shape). ``error``
# maps to the dashed "pruned" style: a crashed / dropped branch that never
# produced a real verdict. A missing verdict falls back to "refused" (matching
# the old Mermaid ``t.verdict or "refused"`` classing).
_VERDICT_STATUS = {
    "success": "success",
    "partial": "partial",
    "refused": "refused",
    "error": "pruned",
}


def _node_status(verdict: str | None) -> str:
    return _VERDICT_STATUS.get(verdict or "", "refused")


def _node_score(trace: Any) -> float | None:
    js = getattr(trace, "judge_scores", None)
    if not js:
        return None
    try:
        return max(float(score.score) for score in js)
    except (AttributeError, TypeError, ValueError):
        return None


def _node_name(trace: Any, index: int) -> str:
    """A short human label for a tree node: the tap_crescendo strategy for
    this conversation when present, else the technique id, else a step
    number. Condensed to a short even label so nodes line up."""
    meta = getattr(trace, "extra_metadata", None)
    name = ""
    if isinstance(meta, dict):
        demo_flow = meta.get("demo_flow")
        if isinstance(demo_flow, dict):
            name = str(demo_flow.get("label") or demo_flow.get("variant") or "").strip()
        tcr = meta.get("tap_crescendo")
        if not name and isinstance(tcr, dict):
            return _strategy_name(str(tcr.get("strategy") or ""), tcr.get("mutation"))
        tap = meta.get("tap")
        if not name and isinstance(tap, dict):
            reasoning = str(tap.get("attacker_reasoning") or "").lower()
            if "operator-provided seed" in reasoning:
                family = "Seed prompt"
            elif "chain-of-thought" in reasoning and any(
                token in reasoning for token in ("yaml", "front-matter", "metadata")
            ):
                family = "Reasoning + YAML"
            elif "chain-of-thought" in reasoning and any(
                token in reasoning for token in ("footnote", "editorial", "annotation")
            ):
                family = "Reasoning footnote"
            elif "chain-of-thought" in reasoning:
                family = "Reasoning hijack"
            elif any(token in reasoning for token in ("tool-call", "tool call", "function-")):
                if "calendar" in reasoning:
                    family = "Calendar API mimic"
                elif "plugin" in reasoning:
                    family = "Plugin response mimic"
                else:
                    family = "Tool-response mimic"
            elif any(token in reasoning for token in ("yaml", "front-matter")):
                family = "YAML metadata embed"
            elif "audit-log" in reasoning:
                family = "Audit-log directive"
            elif any(token in reasoning for token in ("footnote", "editorial", "comment thread")):
                family = "Editorial annotation"
            elif "role confusion" in reasoning:
                family = "Role confusion"
            elif "conditional" in reasoning or "deferred trigger" in reasoning:
                family = "Conditional trigger"
            else:
                family = "TAP variation"
            # No depth/branch suffix: the node's position in the tree shows both.
            name = family
    if not name:
        # technique ids are snake_case slugs; show them as words.
        name = (
            str(getattr(trace, "technique_id", None) or "").replace("_", " ").strip().capitalize()
        )
    if not name:
        name = f"Attempt {index}"
    return _short_label(name, _NAME_LIMIT)


# Fixed nodes the runtime always labels with the same strategy sentence.
_FIXED_STRATEGY_NAMES = (
    ("strict byte-for-byte reproduction", "Seed replay, verbatim"),
    ("replay the documented seed conversation verbatim", "Seed replay, verbatim"),
    ("reproduce the documented", "Seed replay, adaptive"),
    ("paired no-carrier", "No-carrier control"),
    ("natural, gradual build-up", "Natural build-up"),
)
# "Pose as a …" / "Frame the session as a …" lead-ins say nothing; the noun
# phrase after them is the name. The phrase ends where the elaboration starts.
_STRATEGY_LEAD_IN = re.compile(
    r"^(?:pose|present yourself|frame (?:yourself|it|this|the \w+))\s+as\s+(?:an?\s+|the\s+)?",
    re.IGNORECASE,
)
_STRATEGY_TAIL = re.compile(
    r"\s*(?:[,;:.(]|\s(?:who|whom|whose|where|which|that|while|when|so|by|between|and then|writing|asking|claiming|contracting|doing|running|trying|needing)\s).*$",
    re.IGNORECASE,
)
_NAME_LIMIT = 44  # two lines of a node card
_DANGLING = frozenset(
    "a an the of for to in on at with from and or as into about over under than".split()
)


def _strategy_name(strategy: str, mutation: Any = None) -> str:
    """A proper, complete name for a tap_crescendo node (never an ellipsised
    sentence): the mutated axis and value, a fixed name for the runtime's own
    nodes, else the persona/framing noun phrase of a free-form strategy."""
    if isinstance(mutation, dict) and mutation.get("dimension"):
        axis = str(mutation["dimension"]).replace("_", " ").strip().capitalize()
        value = str(mutation.get("feature_value") or "").replace("_", " ").replace("->", " → ")
        value = " ".join(value.split())
        # Values are single tokens/arrows that wrap cleanly, so allow a bit more.
        return f"{axis}: {value}" if value and len(axis) + len(value) + 2 <= 52 else axis
    text = " ".join(strategy.replace("*", "").split())
    lowered = text.lower()
    for needle, fixed in _FIXED_STRATEGY_NAMES:
        if needle in lowered:
            return fixed
    stripped = _STRATEGY_LEAD_IN.sub("", text)
    if stripped != text:
        phrase = _STRATEGY_TAIL.sub("", stripped)
        phrase = "".join(ch for ch in phrase if ch not in '"“”').strip()
        if len(phrase) > _NAME_LIMIT:
            phrase = phrase[:_NAME_LIMIT].rsplit(" ", 1)[0].strip(" ,.:;-")
            words = phrase.split()
            while len(words) > 1 and words[-1].lower() in _DANGLING:
                words.pop()  # never end a name on "… certification for the"
            phrase = " ".join(words)
        if len(phrase) >= 4:
            return phrase[0].upper() + phrase[1:]
    return _short_label(text) if text else "Attempt"


# Match a quoted framing (the distinctive bit of a strategy) as the label.
# Quote chars via \u escapes so no ambiguous literals live in the source.
_QUOTES = "'\"‘’“”"  # noqa: RUF001 (straight + smart quotes)
_QUOTED_PHRASE = re.compile("[" + _QUOTES + "]([^" + _QUOTES + "]{3,28})[" + _QUOTES + "]")


def _short_label(name: str, limit: int = 28) -> str:
    """Condense a strategy sentence into a short, even node label. Long strategy
    prose otherwise wraps to 3-4 lines and overflows the fixed-size
    round-rectangle, so nodes render at different heights and look ragged.

    Order of preference: a quoted framing (the distinctive bit, e.g. 'live
    incident postmortem'), then the leading clause when brief, then a clean
    word-boundary truncation."""
    name = " ".join(name.split())  # collapse newlines / runs of whitespace
    quoted = _QUOTED_PHRASE.search(name)
    if quoted:
        return quoted.group(1).strip()
    for sep in (" — ", " - ", ": ", "; ", ", ", ". "):
        head = name.split(sep, 1)[0].strip()
        if 4 <= len(head) <= limit:
            return head
    if len(name) <= limit:
        return name
    cut = name[:limit].rsplit(" ", 1)[0].strip(" ,.:;-")
    return (cut or name[:limit].strip()) + "…"


def _clip(text: Any, limit: int = 1200) -> str:
    s = "" if text is None else str(text)
    return s if len(s) <= limit else s[:limit] + "…"


def _attempt_description(trace: Any, index: int) -> str:
    meta = getattr(trace, "extra_metadata", None) or {}
    strategy = (meta.get("tap_crescendo") or {}).get("strategy")
    demo = meta.get("demo_flow") or {}
    description = strategy or demo.get("label") or demo.get("variant")
    if not description and isinstance(meta.get("tap"), dict):
        # Indirect TAP sends the same carrier prompt on every branch; the
        # injection family is what tells the nodes apart.
        description = _node_name(trace, index)
    if not description:
        description = getattr(trace, "raw_request", None) or _node_name(trace, index)
    return " ".join(str(description).split())


def _cell_nodes_edges(
    cell: dict[str, object], node_prefix: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Turn one cell's ``lines`` into Cytoscape ``nodes`` + ``edges``.

    Node id is ``{node_prefix}_t{trace_id}`` (hyphens stripped) so ids stay
    valid + unique across phases/cells — same scheme the old Mermaid view used.

    A synthetic **playbook root** node (id ``{node_prefix}_playbook``) is
    prepended and parents every opening variant, so the tree has ONE top node —
    the playbook — that fans out into the attacker's approaches, instead of
    several unconnected sibling roots."""
    lines = cell.get("lines", [])
    assert isinstance(lines, list)
    ids_in_cell = {getattr(ln["trace"], "id", None) for ln in lines}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    root_ids: list[str] = []  # parentless nodes → children of the playbook root
    for i, line in enumerate(lines, start=1):
        t = line["trace"]
        tid = str(getattr(t, "id", "") or "").replace("-", "")
        nid = f"{node_prefix}_t{tid}"
        score = _node_score(t)
        attempted = _attempt_description(t, i)
        method = attack_label(str(getattr(t, "attack_name", "") or ""))
        started = getattr(t, "timestamp", None)
        ended = getattr(t, "ended_at", None)
        time_label = started.strftime("%H:%M:%S") if started else "Time not recorded"
        if started and ended and ended != started:
            time_label += "–" + ended.strftime("%H:%M:%S")  # noqa: RUF001 (en dash range)
        turn_count = getattr(t, "turn_count", 1)
        name = _node_name(t, i)
        label = f"{name}\n{time_label}\n{method}"
        if turn_count > 1:
            label += f" · {turn_count} turns"
        nodes.append(
            {
                "data": {
                    "id": nid,
                    "label": label,
                    "name": name,
                    "attempted": _clip(attempted, 3000),
                    "method": method,
                    "time": time_label,
                    "turn_count": turn_count,
                    "status": _node_status(getattr(t, "verdict", None)),
                    "score": score,
                    "depth": getattr(t, "depth", None),
                    "trace_id": getattr(t, "id", None),
                    "u": _clip(getattr(t, "raw_request", None)),
                    "a": _clip(getattr(t, "raw_response", None)),
                    "email": _clip(
                        ((getattr(t, "extra_metadata", None) or {}).get("demo_flow") or {}).get(
                            "visible_email"
                        )
                    ),
                    "injection": _clip(
                        ((getattr(t, "extra_metadata", None) or {}).get("demo_flow") or {}).get(
                            "hidden_injection"
                        )
                    ),
                }
            }
        )
        parent_id = getattr(t, "parent_id", None)
        if parent_id and parent_id in ids_in_cell:
            pid = f"{node_prefix}_t{str(parent_id).replace('-', '')}"
            edges.append({"data": {"id": f"{pid}__{nid}", "source": pid, "target": nid}})
        else:
            root_ids.append(nid)

    # Prepend the playbook root — one top node the opening variants branch from.
    if nodes:
        pb_id = f"{node_prefix}_playbook"
        behavior = str(cell.get("behavior_id") or "playbook")
        nodes.insert(
            0,
            {
                "data": {
                    "id": pb_id,
                    "label": _short_label(objective_label(behavior), 60),
                    "status": "root",
                    "score": None,
                    "depth": -1,
                    "trace_id": None,
                    "u": "Guide",
                    "a": f"Guide: {behavior}. The opening moves branch from here.",
                }
            },
        )
        for rid in root_ids:
            edges.append({"data": {"id": f"{pb_id}__{rid}", "source": pb_id, "target": rid}})
    return nodes, edges


def tree_graph_payload(
    phases: list[dict[str, object]], *, finished: bool, tree_hash: str
) -> dict[str, Any]:
    """Serialise the per-phase forest into the Cytoscape graph payload the
    client mounts and incrementally updates.

    Shape::

        {
          "finished": bool,
          "hash": str,
          "phases": [
            {"index", "label", "n_success", "n_traces",
             "cells": [
               {"key", "cell_index", "behavior_id", "technique_id",
                "n_success", "n_traces",
                "nodes": [{"data": {...}}], "edges": [{"data": {...}}]},
             ]},
          ],
        }

    ``key`` (``p{phase}|behavior|technique``) is stable across polls even as
    a cell grows, so the client can match a polled cell to the panel it
    already mounted. ``cell_index`` is the 0-based position across all phases
    — the navigator's ``data-cell`` handle.
    """
    out_phases: list[dict[str, Any]] = []
    cell_index = 0
    for phase in phases:
        p_idx = phase.get("index", 0)
        cells_out: list[dict[str, Any]] = []
        forest = phase.get("forest", [])
        assert isinstance(forest, list)
        for pos, cell in enumerate(forest, start=1):
            node_prefix = f"p{p_idx}c{pos}"
            nodes, edges = _cell_nodes_edges(cell, node_prefix)
            behavior_id = cell.get("behavior_id")
            technique_id = cell.get("technique_id")
            cells_out.append(
                {
                    "key": f"p{p_idx}|{behavior_id}|{technique_id}",
                    "cell_index": cell_index,
                    "behavior_id": behavior_id,
                    "technique_id": technique_id,
                    "n_success": cell.get("n_success", 0),
                    "n_traces": cell.get("n_traces", 0),
                    "nodes": nodes,
                    "edges": edges,
                }
            )
            cell_index += 1
        out_phases.append(
            {
                "index": p_idx,
                "label": phase.get("label", ""),
                "n_success": phase.get("n_success", 0),
                "n_traces": phase.get("n_traces", 0),
                "cells": cells_out,
            }
        )
    return {"finished": finished, "hash": tree_hash, "phases": out_phases}


__all__ = [
    "ascii_tree_lines",
    "build_phase_forests",
    "build_tree_forest",
    "collapse_conversations",
    "tree_graph_payload",
]
