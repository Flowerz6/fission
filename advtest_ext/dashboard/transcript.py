"""Reconstruct multi-turn conversations from a run's traces.

The crescendo / tap_chain attacks persist one trace per turn. This pure
helper groups those per-turn traces back into readable conversations for
the transcript view:

* ``crescendo`` — one conversation; turns ordered by depth.
* ``tap_chain`` — one conversation PER replay round (metadata carries
  ``round`` + the diagnoser's ``reason`` for that round); turns ordered
  by the in-chain turn index.
* anything else — each trace is its own one-turn group, so the view
  degrades gracefully for single-step runs.

Pure over duck-typed trace objects (``.id``, ``.raw_request``,
``.raw_response``, ``.extra_metadata``, ``.depth``, ``.verdict``,
``.judge_scores``, ``.attack_name``), so it's unit-testable without a DB.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TranscriptTurn:
    turn: int
    trace_id: str
    prompt: str
    response: str
    score: float | None
    verdict: str | None
    fired: dict[str, Any]


@dataclass
class ConversationGroup:
    label: str
    kind: str  # "tap_chain" | "crescendo" | "single"
    round: int | None
    reason: str = ""
    turns: list[TranscriptTurn] = field(default_factory=list)


def _meta(trace: Any) -> dict[str, Any]:
    m = getattr(trace, "extra_metadata", None)
    return m if isinstance(m, dict) else {}


def _score_of(trace: Any) -> float | None:
    js = getattr(trace, "judge_scores", None)
    if js:
        try:
            return float(js[0].score)
        except (AttributeError, TypeError, ValueError):
            return None
    return None


def _pick_response(*, intercepted: str, rendered: str) -> str:
    """Choose what to display for a turn's reply.

    The rendered side-panel capture (``raw_response``) is now the reliable,
    complete source — the multi-block DOM read captures the full reply at
    any length. We prefer it and only fall back to the intercepted network
    stream when the rendered capture is empty (or the stream is strictly
    fuller and contains it). Divergent intercepted text is NOT appended —
    the rendered capture is the source of truth.

    * rendered empty            -> intercepted (fallback)
    * intercepted strictly ⊃ r  -> intercepted (stream caught more; contains r)
    * otherwise                 -> rendered
    """
    i, r = intercepted.strip(), rendered.strip()
    if not r:
        return intercepted
    if r in i and i != r:
        return intercepted
    return rendered


def build_transcript_groups(
    traces: list[Any], full_responses: dict[str, str] | None = None
) -> list[ConversationGroup]:
    """Group per-turn traces into conversations, ordered for display."""
    full_responses = full_responses or {}
    groups: dict[tuple[str, int], ConversationGroup] = {}
    order: list[tuple[str, int]] = []

    for t in traces:
        meta = _meta(t)
        tc = meta.get("tap_chain") if isinstance(meta.get("tap_chain"), dict) else None
        cr = meta.get("crescendo") if isinstance(meta.get("crescendo"), dict) else None
        tcr = meta.get("tap_crescendo") if isinstance(meta.get("tap_crescendo"), dict) else None
        depth = int(getattr(t, "depth", 0) or 0)

        if tcr is not None:
            # One conversation per tree node; key by node id so each
            # node's Crescendo renders as its own grouped chat.
            node_id = str(tcr.get("node_id", "node"))
            turn = int(tcr.get("turn", depth))
            tree_depth = int(tcr.get("depth", 0))
            strategy = str(tcr.get("strategy", "") or "")
            key = ("tap_crescendo", hash(node_id) & 0xFFFFFFF)
            label = f"node {node_id} (tree depth {tree_depth})"
            reason = f"strategy: {strategy}" if strategy else ""
            fired = tcr.get("fired") if isinstance(tcr.get("fired"), dict) else {}
            kind, round_ = "tap_crescendo", tree_depth
        elif tc is not None:
            rnd = int(tc.get("round", 0))
            turn = int(tc.get("turn", depth))
            key = ("tap_chain", rnd)
            label = f"tap_chain — replay round {rnd}"
            reason = str(tc.get("reason", "") or "")
            fired = tc.get("fired") if isinstance(tc.get("fired"), dict) else {}
            kind, round_ = "tap_chain", rnd
        elif cr is not None:
            turn = depth or int(cr.get("n_turns", 0))
            key = ("crescendo", 0)
            label = "Crescendo conversation"
            reason, fired = "", {}
            kind, round_ = "crescendo", None
        else:
            turn = depth
            tid = str(getattr(t, "id", ""))
            key = ("single", len(order))  # each single-step trace is its own group
            label = f"{getattr(t, 'attack_name', 'trace')} · {tid[:12]}"
            reason, fired = "", {}
            kind, round_ = "single", None

        grp = groups.get(key)
        if grp is None:
            grp = ConversationGroup(label=label, kind=kind, round=round_, reason=reason)
            groups[key] = grp
            order.append(key)
        elif not grp.reason and reason:
            grp.reason = reason

        tid = str(getattr(t, "id", ""))
        response = _pick_response(
            intercepted=full_responses.get(tid) or "",
            rendered=getattr(t, "raw_response", "") or "",
        )
        grp.turns.append(
            TranscriptTurn(
                turn=turn,
                trace_id=tid,
                prompt=getattr(t, "raw_request", "") or "",
                response=response,
                score=_score_of(t),
                verdict=getattr(t, "verdict", None),
                fired=fired,
            )
        )

    for grp in groups.values():
        grp.turns.sort(key=lambda x: x.turn)

    result = [groups[k] for k in order]
    # tap_crescendo nodes (by tree depth) first, then tap_chain rounds,
    # then crescendo, then single-step. Sort is stable, so same-depth
    # nodes keep their generation order.
    _kind_rank = {"tap_crescendo": 0, "tap_chain": 1, "crescendo": 2, "single": 3}
    result.sort(key=lambda g: (_kind_rank.get(g.kind, 4), g.round if g.round is not None else 0))
    return result


__all__ = ["ConversationGroup", "TranscriptTurn", "build_transcript_groups"]
