"""The target-agnostic loop core (advtest_ext/strategy/loop.py).

Locks the contract both the standalone runner and the attack plugin
depend on: one Step per iteration, no-op turns surfaced with rollout=None
but still recorded in history, and stop-right-after-the-first-hit.
"""

from __future__ import annotations

import pytest

from advtest_ext.strategy.attacker import ScriptedAttacker
from advtest_ext.strategy.loop import Step, iterate_sample
from advtest_ext.strategy.models import Attempt, RolloutResult, Sample


def _sample() -> Sample:
    return Sample(
        suite_name="google_docs",
        user_task_id="t1",
        injection_task_id="exfil",
        user_task_prompt="Summarise.",
        injection_task_goal="Exfiltrate the body.",
        context_with_placeholder="body {INJECTION:doc}",
        injection_candidates=["doc"],
    )


def _script(injection: str) -> str:
    return f"<analysis>a</analysis><injection>{injection}</injection>"


async def _collect(**kw) -> list[Step]:
    steps: list[Step] = []
    async for step in iterate_sample(**kw):
        steps.append(step)
    return steps


@pytest.mark.asyncio
async def test_stops_right_after_first_hit() -> None:
    # Miss, then hit: rollout flips security True on the 2nd injection.
    async def rollout(sample: Sample, injection: str) -> RolloutResult:
        hit = injection == "win"
        return RolloutResult(utility=hit, security=hit, final_output=f"out:{injection}")

    history: list[Attempt] = []
    steps = await _collect(
        sample=_sample(),
        strategy_doc="DOC",
        attacker=ScriptedAttacker([_script("nope"), _script("win"), _script("extra")]),
        rollout=rollout,
        n_max=10,
        history=history,
    )

    # Two steps only — it stopped the instant the hit landed, never firing "extra".
    assert [s.attempt.injection for s in steps] == ["nope", "win"]
    assert steps[-1].attempt.security is True
    assert steps[-1].rollout is not None and steps[-1].rollout.security is True
    # history mirrors the yielded steps (shared in place).
    assert [a.injection for a in history] == ["nope", "win"]


@pytest.mark.asyncio
async def test_noop_turn_has_none_rollout_but_is_recorded() -> None:
    # An attacker that yields an empty injection -> parse_attempt raises
    # ValueError -> a no-op Step (rollout is None) that the plugin skips,
    # but which still lands in history so the next prompt sees it.
    calls: list[str] = []

    async def rollout(sample: Sample, injection: str) -> RolloutResult:
        calls.append(injection)
        return RolloutResult(utility=False, security=False)

    history: list[Attempt] = []
    steps = await _collect(
        sample=_sample(),
        strategy_doc="DOC",
        # First script has no <injection> block -> ValueError -> no-op turn.
        attacker=ScriptedAttacker(["<analysis>thinking only</analysis>", _script("real")]),
        rollout=rollout,
        n_max=2,
        history=history,
    )

    assert len(steps) == 2
    assert steps[0].rollout is None  # no-op: target never invoked this turn
    assert steps[0].attempt.injection == ""
    assert steps[1].rollout is not None
    # rollout only fired for the real injection, not the no-op turn.
    assert calls == ["real"]
    # Both turns recorded in history.
    assert len(history) == 2


@pytest.mark.asyncio
async def test_respects_n_max_on_all_misses() -> None:
    async def rollout(sample: Sample, injection: str) -> RolloutResult:
        return RolloutResult(utility=False, security=False)

    steps = await _collect(
        sample=_sample(),
        strategy_doc="DOC",
        attacker=ScriptedAttacker([_script("x")]),  # last script repeats
        rollout=rollout,
        n_max=3,
    )
    assert len(steps) == 3
    assert all(s.attempt.security is False for s in steps)
