"""Unit tests for the TAP-chain refinement engine.

Parsers (LLM replies are messy) + the hill-climb control flow with
injected fakes — no LLM, no browser. The load-bearing behaviours:
improvement is adopted, non-improvement is rejected, patience stops the
loop, threshold exits early, the prefix before the break is preserved,
and failed tails are fed back to the refiner.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.attacks.multi_turn_refine import (
    ChainResult,
    Diagnosis,
    aggregate_score,
    hill_climb,
    parse_chain_messages,
    parse_diagnosis,
)

# ----------------------------------------------------------------------
# Parsers
# ----------------------------------------------------------------------


def test_aggregate_score_is_max_or_zero() -> None:
    assert aggregate_score([0.1, 0.7, 0.3]) == 0.7
    assert aggregate_score([]) == 0.0


def test_parse_diagnosis_clean() -> None:
    d = parse_diagnosis('{"failure_turn": 3, "reason": "named the role too directly"}')
    assert d.failure_turn == 3
    assert "role" in d.reason


def test_parse_diagnosis_wrapped_in_prose_and_fence() -> None:
    text = 'Here is my analysis:\n```json\n{"failure_turn": 2, "reason": "refusal"}\n```\nThanks!'
    d = parse_diagnosis(text)
    assert d.failure_turn == 2
    assert d.reason == "refusal"


def test_parse_diagnosis_garbage_falls_back() -> None:
    d = parse_diagnosis("no json here", default_turn=1)
    assert d.failure_turn == 1


def test_parse_diagnosis_string_turn_coerced() -> None:
    d = parse_diagnosis('{"failure_turn": "4", "reason": "x"}')
    assert d.failure_turn == 4


def test_parse_chain_messages_object_and_array() -> None:
    assert parse_chain_messages('{"messages": ["a", "b"]}') == ["a", "b"]
    assert parse_chain_messages('["x", "y"]') == ["x", "y"]


def test_parse_chain_messages_drops_empties_and_nonstrings() -> None:
    assert parse_chain_messages('["a", "", 5, "  b  "]') == ["a", "b"]


def test_parse_chain_messages_garbage_is_empty() -> None:
    assert parse_chain_messages("not json") == []


# ----------------------------------------------------------------------
# hill_climb control flow
# ----------------------------------------------------------------------


def _result(chain: list[str], score: float) -> ChainResult:
    return ChainResult(chain=chain, score=score, per_turn_scores=[score])


def _scripted_replay(scores: list[float], *, record: list[list[str]] | None = None) -> Any:
    """replay_fn that returns the next scripted score each call."""
    it = iter(scores)

    async def replay_fn(chain: list[str], round_idx: int) -> ChainResult:
        if record is not None:
            record.append(list(chain))
        return _result(chain, next(it))

    return replay_fn


def _diagnose_at(turn: int) -> Any:
    async def diagnose_fn(result: ChainResult) -> Diagnosis:
        return Diagnosis(failure_turn=turn, reason="fake")

    return diagnose_fn


@pytest.mark.asyncio
async def test_threshold_exit_skips_refinement() -> None:
    """Seed already at threshold → return immediately, never diagnose."""
    diagnosed = []

    async def diagnose_fn(result: ChainResult) -> Diagnosis:
        diagnosed.append(True)
        return Diagnosis(failure_turn=1)

    async def refine_fn(prefix, diag, prev):
        raise AssertionError("refine should not be called")

    best = await hill_climb(
        seed_chain=["a", "b"],
        replay_fn=_scripted_replay([0.9]),
        diagnose_fn=diagnose_fn,
        refine_fn=refine_fn,
        threshold=0.85,
        log=lambda _m: None,
    )
    assert best.score == 0.9
    assert diagnosed == []


@pytest.mark.asyncio
async def test_improvement_is_adopted() -> None:
    async def refine_fn(prefix, diag, prev):
        return ["new-tail"]

    # seed 0.3, candidate 0.6, single round so only 2 replays happen
    best = await hill_climb(
        seed_chain=["t1", "t2", "t3"],
        replay_fn=_scripted_replay([0.3, 0.6]),
        diagnose_fn=_diagnose_at(2),
        refine_fn=refine_fn,
        max_rounds=1,
        threshold=0.95,
        log=lambda _m: None,
    )
    assert best.score == 0.6
    # break@turn2 → prefix is turn1 only, tail replaces the rest
    assert best.chain == ["t1", "new-tail"]


@pytest.mark.asyncio
async def test_prefix_before_break_is_preserved() -> None:
    seen_prefixes = []

    async def refine_fn(prefix, diag, prev):
        seen_prefixes.append(list(prefix))
        return ["tail"]

    await hill_climb(
        seed_chain=["t1", "t2", "t3", "t4"],
        replay_fn=_scripted_replay([0.3, 0.3]),  # no improvement, single round (patience hits)
        diagnose_fn=_diagnose_at(3),
        refine_fn=refine_fn,
        max_rounds=4,
        patience=1,
        threshold=0.95,
        log=lambda _m: None,
    )
    # break@turn3 → prefix = turns 1,2
    assert seen_prefixes[0] == ["t1", "t2"]


@pytest.mark.asyncio
async def test_patience_stops_after_non_improving_rounds() -> None:
    calls = {"n": 0}

    async def refine_fn(prefix, diag, prev):
        calls["n"] += 1
        return ["tail"]

    # seed 0.3, then every candidate also 0.3 → never improves
    best = await hill_climb(
        seed_chain=["t1", "t2"],
        replay_fn=_scripted_replay([0.3, 0.3, 0.3, 0.3, 0.3]),
        diagnose_fn=_diagnose_at(1),
        refine_fn=refine_fn,
        max_rounds=10,
        patience=2,
        threshold=0.95,
        log=lambda _m: None,
    )
    assert best.score == 0.3
    assert calls["n"] == 2  # stopped after `patience` non-improving rounds


@pytest.mark.asyncio
async def test_failed_tails_are_fed_back_for_same_break() -> None:
    prev_seen: list[list[list[str]]] = []
    tails = iter([["tailA"], ["tailB"], ["tailC"]])

    async def refine_fn(prefix, diag, prev):
        prev_seen.append([list(t) for t in prev])
        return next(tails)

    await hill_climb(
        seed_chain=["t1", "t2"],
        replay_fn=_scripted_replay([0.3, 0.3, 0.3]),  # no improvements
        diagnose_fn=_diagnose_at(2),
        refine_fn=refine_fn,
        max_rounds=10,
        patience=2,
        threshold=0.95,
        log=lambda _m: None,
    )
    # First refine sees no prior failures; second sees the first failed tail.
    assert prev_seen[0] == []
    assert prev_seen[1] == [["tailA"]]
