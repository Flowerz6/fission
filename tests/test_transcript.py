"""Unit tests for the dashboard multi-turn transcript grouping.

Pure over duck-typed trace objects — no DB, no app. Verifies that
per-turn traces regroup into the right conversations, ordered for
display, with the full intercepted response preferred over raw_response.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from advtest_ext.dashboard.transcript import build_transcript_groups


def _js(score: float) -> list[Any]:
    return [SimpleNamespace(score=score)]


def _trace(
    tid: str,
    *,
    meta: dict[str, Any] | None = None,
    depth: int = 0,
    request: str = "",
    response: str = "",
    verdict: str | None = None,
    score: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=tid,
        extra_metadata=meta or {},
        depth=depth,
        raw_request=request,
        raw_response=response,
        verdict=verdict,
        judge_scores=_js(score) if score is not None else [],
        attack_name="x",
    )


def test_crescendo_single_conversation_ordered_by_depth() -> None:
    traces = [
        _trace("t2", meta={"crescendo": {"n_turns": 2}}, depth=2, request="q2", response="r2"),
        _trace("t1", meta={"crescendo": {"n_turns": 1}}, depth=1, request="q1", response="r1"),
        _trace("t3", meta={"crescendo": {"n_turns": 3}}, depth=3, request="q3", response="r3"),
    ]
    groups = build_transcript_groups(traces)
    assert len(groups) == 1
    g = groups[0]
    assert g.kind == "crescendo"
    assert [t.turn for t in g.turns] == [1, 2, 3]
    assert [t.prompt for t in g.turns] == ["q1", "q2", "q3"]


def test_tap_chain_one_group_per_round_with_reason() -> None:
    traces = [
        _trace("a1", meta={"tap_chain": {"round": 0, "turn": 1, "reason": ""}}, request="r0t1"),
        _trace("a2", meta={"tap_chain": {"round": 0, "turn": 2, "reason": ""}}, request="r0t2"),
        _trace("b1", meta={"tap_chain": {"round": 1, "turn": 1, "reason": "broke at 2"}}),
        _trace("b2", meta={"tap_chain": {"round": 1, "turn": 2, "reason": "broke at 2"}}),
    ]
    groups = build_transcript_groups(traces)
    assert len(groups) == 2
    # Ordered by round ascending.
    assert groups[0].round == 0
    assert groups[1].round == 1
    assert groups[1].reason == "broke at 2"
    assert [t.turn for t in groups[0].turns] == [1, 2]


def test_full_response_preferred_over_raw() -> None:
    # Real truncation: the rendered capture is a PREFIX of the fuller
    # intercepted stream, so the intercept wins.
    traces = [
        _trace("t1", meta={"crescendo": {"n_turns": 1}}, depth=1, response="FULL INTERCEPTED")
    ]
    groups = build_transcript_groups(traces, {"t1": "FULL INTERCEPTED REPLY with more"})
    assert groups[0].turns[0].response == "FULL INTERCEPTED REPLY with more"


def test_raw_response_fallback_when_no_intercept() -> None:
    traces = [_trace("t1", meta={"crescendo": {"n_turns": 1}}, depth=1, response="rendered only")]
    groups = build_transcript_groups(traces, {})
    assert groups[0].turns[0].response == "rendered only"


def test_diverging_captures_prefer_rendered_only() -> None:
    """When the intercepted stream and the rendered panel diverge (neither
    contains the other), show ONLY the rendered capture — it's the reliable,
    complete source now (multi-block DOM read). The intercepted network
    stream is NOT appended. Regression for a system-prompt leak the network
    interception missed but the panel rendered."""
    traces = [
        _trace(
            "t1",
            meta={"crescendo": {"n_turns": 1}},
            depth=1,
            response="*** Session Configuration *** Your primary objective ...",
        )
    ]
    groups = build_transcript_groups(traces, {"t1": "I apologize, here is the grant summary ..."})
    resp = groups[0].turns[0].response
    assert "Your primary objective" in resp  # the leak (rendered) is shown
    assert "grant summary" not in resp  # the intercepted stream is NOT appended
    assert "────" not in resp  # no divergence divider


def test_fired_badges_carried_from_tap_chain_meta() -> None:
    traces = [
        _trace(
            "x",
            meta={
                "tap_chain": {"round": 0, "turn": 1, "fired": {"handshake": True, "canary": False}}
            },
        )
    ]
    groups = build_transcript_groups(traces)
    assert groups[0].turns[0].fired == {"handshake": True, "canary": False}


def test_single_step_trace_is_its_own_group() -> None:
    traces = [
        _trace("s1", meta={}, depth=0, request="one-shot"),
        _trace("s2", meta={}, depth=0, request="another"),
    ]
    groups = build_transcript_groups(traces)
    assert len(groups) == 2
    assert all(g.kind == "single" for g in groups)


def test_ordering_tap_chain_before_crescendo_before_single() -> None:
    traces = [
        _trace("s", meta={}),
        _trace("c", meta={"crescendo": {"n_turns": 1}}, depth=1),
        _trace("t", meta={"tap_chain": {"round": 0, "turn": 1}}),
    ]
    kinds = [g.kind for g in build_transcript_groups(traces)]
    assert kinds == ["tap_chain", "crescendo", "single"]
