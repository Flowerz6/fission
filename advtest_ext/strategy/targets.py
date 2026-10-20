"""The rollout seam: PIMiner's "reward", generalised.

A target takes a :class:`Sample` and one injection string, runs the
target agent against the polluted sample, and returns a
:class:`RolloutResult`. Three implementations satisfy the same protocol:

  * :class:`OfflineMarkerTarget` (here) — deterministic, no external
    calls; used to prove the loop mechanics and in unit tests.
  * an AgentDojo target (later) — wraps PIMiner's ``AgentDojoAttackReward``
    for the cheap grey-box training track.
  * a Workspace target (later) — plants via ``adapters/<app>`` and scores
    with fission's layered oracle for the live black-box track.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from advtest_ext.strategy.models import RolloutResult, Sample


@runtime_checkable
class AttackTarget(Protocol):
    """Runs one injection attempt and reports the outcome."""

    async def rollout(self, sample: Sample, injection: str) -> RolloutResult: ...


class OfflineMarkerTarget:
    """Deterministic reward with no external calls.

    ``security`` is True iff every marker in ``sample.success_markers`` is
    a (case-insensitive) substring of the injection text — a stand-in for
    "the injection embedded the canonical malicious arguments the real
    success-checker looks for". When a sample declares no markers, the
    injection goal text is used as the single marker. ``utility`` mirrors
    ``security`` (the offline model has no separate benign-task signal).

    This exists so the full route -> step -> rollout loop can be exercised
    end-to-end — in CI and in a first live-session smoke run — before any
    AgentDojo install or live-Workspace Playwright cost is incurred.
    """

    def __init__(self, *, case_sensitive: bool = False) -> None:
        self._case_sensitive = case_sensitive

    async def rollout(self, sample: Sample, injection: str) -> RolloutResult:
        markers = sample.success_markers or [sample.injection_task_goal]
        hay = injection if self._case_sensitive else injection.lower()
        needles = markers if self._case_sensitive else [m.lower() for m in markers]
        hit = all(n in hay for n in needles if n)
        return RolloutResult(
            utility=hit,
            security=hit,
            final_output=f"(offline marker target) markers_hit={hit}",
            trajectory="(offline target — no live trajectory)",
            extra={"markers": markers, "verdict_source": "offline_marker"},
        )
