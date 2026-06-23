"""Tests for collapse_conversations — one tree node per conversation.

Verifies that per-turn traces of a multi-turn attack collapse into a
single node per conversation (assessed by the best turn), tap_crescendo
branches by node, and non-multi-turn traces pass through untouched.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from advtest_ext.dashboard.tree import build_tree_forest, collapse_conversations


def _trace(
    tid: str,
    *,
    meta: dict[str, Any] | None = None,
    score: float | None = None,
    verdict: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=tid,
        extra_metadata=meta or {},
        behavior_id="quillfeather_v1_persona",
        technique_id=None,
        depth=0,
        parent_id=None,
        verdict=verdict,
        judge_scores=[SimpleNamespace(score=score)] if score is not None else [],
    )


def _tcr(node_id: str, parent: str | None, depth: int, turn: int) -> dict[str, Any]:
    return {
        "tap_crescendo": {"node_id": node_id, "parent_id": parent, "depth": depth, "turn": turn}
    }


def test_crescendo_collapses_to_one_node() -> None:
    traces = [
        _trace("t1", meta={"crescendo": {"n_turns": 1}}, score=0.30, verdict="partial"),
        _trace("t2", meta={"crescendo": {"n_turns": 2}}, score=0.30, verdict="partial"),
        _trace("t3", meta={"crescendo": {"n_turns": 3}}, score=0.90, verdict="success"),
    ]
    nodes = collapse_conversations(traces)
    assert len(nodes) == 1  # 3 turns -> 1 conversation node
    n = nodes[0]
    # Assessed by the best turn (the one that landed).
    assert n.verdict == "success"
    assert n.judge_scores[0].score == 0.90
    assert n.id == "t3"
    assert n.depth == 0


def test_tap_crescendo_branches_by_node() -> None:
    traces = [
        # root node n0 (2 turns)
        _trace("a1", meta=_tcr("n0", None, 0, 1), score=0.30, verdict="partial"),
        _trace("a2", meta=_tcr("n0", None, 0, 2), score=0.30, verdict="partial"),
        # child node n1 (parent n0), 1 turn, lands it
        _trace("b1", meta=_tcr("n1-0-1", "n0", 1, 1), score=0.90, verdict="success"),
        # child node n2 (parent n0), 1 turn, weak
        _trace("c1", meta=_tcr("n1-0-2", "n0", 1, 1), score=0.20, verdict="refused"),
    ]
    nodes = collapse_conversations(traces)
    assert len(nodes) == 3  # 3 conversations, not 4 turns
    by_id = {n.id: n for n in nodes}
    # root rep = best of n0's turns (a1/a2, both 0.30) — id is one of them
    root = next(n for n in nodes if n.depth == 0)
    child_success = by_id["b1"]
    child_weak = by_id["c1"]
    # Children point at the root node's representative trace id.
    assert child_success.parent_id == root.id
    assert child_weak.parent_id == root.id
    assert child_success.verdict == "success"
    assert child_weak.depth == 1

    # And the forest links them: root has 2 children.
    forest = build_tree_forest(nodes)
    assert len(forest) == 1
    cell = forest[0]
    assert cell["n_traces"] == 3  # conversations, not turns
    assert cell["n_success"] == 1
    assert len(cell["roots"]) == 1
    assert len(cell["roots"][0]["children"]) == 2


def test_non_multi_turn_passes_through() -> None:
    plain = [
        _trace("x", meta={}, score=0.5, verdict="partial"),
        _trace("y", meta={"tap": {"depth": 1}}, score=0.5, verdict="partial"),
    ]
    nodes = collapse_conversations(plain)
    assert len(nodes) == 2  # untouched
    assert {n.id for n in nodes} == {"x", "y"}
