"""Tests for ``advtest_ext.core.orchestrator``.

All tests use scripted ``AttackPlugin`` + ``DocumentTarget`` +
``JudgeBase`` doubles — no real LLM calls. The doubles are designed
so each test asserts one orchestrator property (trace count, MCTS
arm preference, budget hard-stop, error isolation, event emission,
trace persistence) without entanglement with the others.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from advtest_ext.attacks.base import (
    AttackAttempt,
    AttackPlugin,
    AttackPluginClass,
)
from advtest_ext.core.models import (
    Behavior,
    Budget,
    JudgeScore,
    Payload,
)
from advtest_ext.core.orchestrator import (
    GridOrchestrator,
    MctsOrchestrator,
    OrchestratorEvent,
    summarise,
)
from advtest_ext.document import StubDocumentTarget
from advtest_ext.document.target import (
    DocumentCapabilities,
    DocumentTarget,
    NativeContext,
    Response,
)
from advtest_ext.judges.base import JudgeBase

# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


class _FixedAttack(AttackPlugin):
    """Yields a deterministic, finite list of attempts. Each attempt's
    payload text encodes the attack name + index so per-attack output
    is identifiable in the resulting traces."""

    attack_class: AttackPluginClass = "single_turn"

    def __init__(self, *, name: str, n_attempts: int = 3) -> None:
        # Override class attrs at the instance level.
        self.name = name
        self.references = ["test"]
        self._n_attempts = n_attempts
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Any,
        target: Any,
        budget: Any,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget, context
        for i in range(self._n_attempts):
            yield AttackAttempt(
                payload=Payload(
                    id=f"{self.name}-{behavior.id}-{i}",
                    text=f"<{self.name}#{i}> {behavior.description}",
                    modality="text",
                    generator_name=self.name,
                ),
                user_prompt=f"<{self.name}#{i}>",
                metadata={"i": i},
            )


class _RaisesAttack(AttackPlugin):
    """Yields N attempts; the K-th raises so the orchestrator must
    isolate the failure and continue with the rest."""

    attack_class: AttackPluginClass = "single_turn"

    def __init__(self, *, name: str, n_attempts: int = 3, raise_at: int = 1) -> None:
        self.name = name
        self.references = ["test"]
        self._n = n_attempts
        self._raise_at = raise_at
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Any,
        target: Any,
        budget: Any,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget, context
        for i in range(self._n):
            yield AttackAttempt(
                payload=Payload(
                    id=f"{self.name}-{behavior.id}-{i}",
                    text=f"<{self.name}#{i}>",
                    modality="text",
                    generator_name=self.name,
                ),
                user_prompt=f"<{self.name}#{i}>" + (" RAISE" if i == self._raise_at else ""),
            )


class _RaisingTarget(DocumentTarget):
    """Target whose ``invoke`` raises when the user prompt contains
    ``RAISE``. Used to exercise per-attempt error isolation."""

    async def setup(self) -> None: ...
    async def teardown(self, context: NativeContext) -> None: ...

    async def render_document(self, doc: Any) -> NativeContext:
        return NativeContext(rendered={})

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        if "RAISE" in user_prompt:
            raise RuntimeError("scripted target failure")
        return Response(text="ok", complied=False)

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(block_types={"text"})

    def supported_techniques(self) -> set[str]:
        return {"*"}


class _MarkerJudge(JudgeBase):
    """Scores high when the payload text contains the configured
    ``high_score_marker`` substring, low otherwise. Lets a test pre-
    seed which arm should look most attractive to MCTS."""

    name: str = "marker_judge"
    references: list[str] = []  # noqa: RUF012

    def __init__(
        self,
        *,
        high_score_marker: str,
        high_score: float = 0.9,
        low_score: float = 0.1,
    ) -> None:
        self._marker = high_score_marker
        self._high = high_score
        self._low = low_score
        self.calls = 0
        super().__init__()

    async def evaluate(self, *, trace: Any, behavior: Any) -> JudgeScore:
        del behavior
        self.calls += 1
        text = trace.raw_request
        score = self._high if self._marker in text else self._low
        return JudgeScore(
            judge_name=self.name,
            score=score,
            reasoning="marker hit" if score == self._high else "no marker",
            flagged=score >= 0.5,
            judge_version="test",
        )


class _ConstantJudge(JudgeBase):
    """Returns a constant score on every call."""

    name: str = "constant"
    references: list[str] = []  # noqa: RUF012

    def __init__(self, *, score: float = 0.05) -> None:
        self._score = score
        self.calls = 0
        super().__init__()

    async def evaluate(self, *, trace: Any, behavior: Any) -> JudgeScore:
        del trace, behavior
        self.calls += 1
        return JudgeScore(
            judge_name=self.name,
            score=self._score,
            reasoning="constant",
            flagged=self._score >= 0.5,
            judge_version="test",
        )


def _behavior(behavior_id: str = "b1") -> Behavior:
    return Behavior(
        id=behavior_id,
        description=f"behaviour {behavior_id}",
        success_criteria="hits the marker",
        severity="high",
        source_citation="test",
        category="exfiltration",
    )


# ----------------------------------------------------------------------
# GridOrchestrator
# ----------------------------------------------------------------------


async def test_grid_yields_attacks_x_behaviors_x_attempts() -> None:
    """Trace count == sum over (attack, behavior) of that attack's
    ``n_attempts``. With 2 attacks (3 attempts each) x 2 behaviours we
    expect 12 traces."""
    attacks = [
        _FixedAttack(name="atk_a", n_attempts=3),
        _FixedAttack(name="atk_b", n_attempts=3),
    ]
    behaviors = [_behavior("b1"), _behavior("b2")]
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=attacks,
        behaviors=behaviors,
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    assert len(result.traces) == 12
    # Per-(attack, behavior) breakdown row sums to 3 attempts each.
    for row in result.summary_stats.asr_breakdown:
        assert row.attempts == 3
    assert result.summary_stats.total_traces == 12


async def test_grid_concurrency_does_not_lose_traces() -> None:
    """Running with max_concurrency=4 must yield the same trace count
    as serial. Floats race-prone bookkeeping (budget reservation,
    trace list, summary)."""
    attacks = [_FixedAttack(name=f"atk_{i}", n_attempts=2) for i in range(4)]
    behaviors = [_behavior(f"b{i}") for i in range(3)]
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=attacks,
        behaviors=behaviors,
        judge=_ConstantJudge(score=0.1),
        max_concurrency=4,
    )
    result = await orch.run()
    assert len(result.traces) == 4 * 3 * 2  # 24


async def test_grid_isolates_per_attempt_errors_into_error_traces() -> None:
    """One attempt raising must not abort the run; it lands as a
    ``verdict='error'`` trace and the rest of the iteration proceeds."""
    attack = _RaisesAttack(name="raisy", n_attempts=3, raise_at=1)
    orch = GridOrchestrator(
        target=_RaisingTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    assert len(result.traces) == 3
    verdicts = [t.verdict for t in result.traces]
    assert verdicts.count("error") == 1
    # Error traces are excluded from ASR denominators.
    assert result.summary_stats.total_traces == 3
    counted = sum(r.attempts for r in result.summary_stats.asr_breakdown)
    assert counted == 2


class _MarkerTarget(DocumentTarget):
    """Target that echoes a fixed marker into every response, so a
    ``token_appears`` signal fires on it."""

    async def setup(self) -> None: ...
    async def teardown(self, context: NativeContext) -> None: ...

    async def render_document(self, doc: Any) -> NativeContext:
        return NativeContext(rendered={})

    async def invoke(self, context: NativeContext, user_prompt: str) -> Response:
        del context, user_prompt
        return Response(text="here is the summary. HX-WHITE", complied=False)

    def capabilities(self) -> DocumentCapabilities:
        return DocumentCapabilities(block_types={"text"})

    def supported_techniques(self) -> set[str]:
        return set()


async def test_fired_signal_only_lifts_verdict_to_partial() -> None:
    """A marker match alone does not verify an unauthorized effect."""
    orch = GridOrchestrator(
        target=_MarkerTarget(),
        attacks=[_FixedAttack(name="atk", n_attempts=1)],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.05),  # judge says refused
        signal_specs=[
            {
                "kind": "token_appears",
                "name": "hide_white",
                "token": "HX-WHITE",
                "where": "ui_response",
            },
        ],
    )
    result = await orch.run()
    assert len(result.traces) == 1
    # The marker corroborates exposure, but does not establish success.
    assert result.traces[0].verdict == "partial"
    # The raw judge score is preserved for transparency.
    assert result.traces[0].judge_scores[0].score == 0.05


async def test_no_signal_no_upgrade_verdict_stays_judge_only() -> None:
    """Regression: without a signal stack, the verdict is judge-only —
    a low judge score stays 'refused'. Single-judge runs unchanged."""
    orch = GridOrchestrator(
        target=_MarkerTarget(),
        attacks=[_FixedAttack(name="atk", n_attempts=1)],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.05),
    )
    result = await orch.run()
    assert result.traces[0].verdict == "refused"


async def test_l0_l2_conflict_scores_high_uncertainty() -> None:
    """Measurement spine: a fired signal (L0 says landed) against a
    low judge score (L2 says refused) is the dominant uncertainty axis,
    so the trace is flagged for expert review with a high score."""
    orch = GridOrchestrator(
        target=_MarkerTarget(),
        attacks=[_FixedAttack(name="atk", n_attempts=1)],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.05),  # L2 refused
        signal_specs=[
            {
                "kind": "token_appears",
                "name": "hide_white",
                "token": "HX-WHITE",
                "where": "ui_response",
            },
        ],
    )
    result = await orch.run()
    trace = result.traces[0]
    assert trace.metadata["uncertainty"] >= 0.7
    assert "conflict" in trace.metadata["uncertainty_reason"]


async def test_agreeing_oracle_scores_low_uncertainty() -> None:
    """When the judge is confident and no signal contradicts it, the
    oracle's layers agree — low review priority."""
    orch = GridOrchestrator(
        target=_MarkerTarget(),
        attacks=[_FixedAttack(name="atk", n_attempts=1)],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.95),
    )
    result = await orch.run()
    assert result.traces[0].metadata["uncertainty"] < 0.2


# ----------------------------------------------------------------------
# Budget enforcement
# ----------------------------------------------------------------------


async def test_grid_hard_stops_on_max_queries_mid_attack() -> None:
    """``Budget(max_queries=1)`` must short-circuit after the first
    attempt — even though the attack has more queued. The remaining
    attempts must NOT execute."""
    attack = _FixedAttack(name="atk", n_attempts=10)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        budget=Budget(max_queries=1),
    )
    result = await orch.run()
    assert len(result.traces) == 1


async def test_mcts_hard_stops_on_max_queries() -> None:
    attack = _FixedAttack(name="atk", n_attempts=10)
    orch = MctsOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        budget=Budget(max_queries=2),
        success_threshold=0.99,
    )
    result = await orch.run()
    assert len(result.traces) == 2


# ----------------------------------------------------------------------
# MctsOrchestrator
# ----------------------------------------------------------------------


async def test_mcts_prefers_high_scoring_arm_over_iterations() -> None:
    """With two arms — one tagged ``HIGH`` (judge returns 0.9) and one
    tagged ``LOW`` (judge returns 0.1) — UCB1 should drive most of
    the budget into the HIGH arm after the cold-start round-robin.
    Asserting the inequality (not an exact ratio) keeps the test
    robust to UCB constant tweaks."""
    attacks = [
        _FixedAttack(name="HIGH_arm", n_attempts=100),
        _FixedAttack(name="LOW_arm", n_attempts=100),
    ]
    judge = _MarkerJudge(high_score_marker="HIGH_arm")
    orch = MctsOrchestrator(
        target=StubDocumentTarget(),
        attacks=attacks,
        behaviors=[_behavior()],
        judge=judge,
        budget=Budget(max_queries=20),
        # Set above 0.9 so the loop keeps exploring instead of
        # short-circuiting after the first HIGH hit.
        success_threshold=0.95,
    )
    result = await orch.run()
    counts: dict[str, int] = {}
    for trace in result.traces:
        counts[trace.payload.generator_name] = counts.get(trace.payload.generator_name, 0) + 1
    assert counts.get("HIGH_arm", 0) > counts.get("LOW_arm", 0)
    # The HIGH arm should clearly dominate (>= 60% of pulls) after
    # cold-start. If this is flaky on future tweaks, lower the bound,
    # but document why in the commit message.
    assert counts.get("HIGH_arm", 0) / sum(counts.values()) >= 0.6


async def test_mcts_stops_early_on_success_threshold() -> None:
    """As soon as a pulled arm scores ``>= success_threshold`` we
    return. No more arms are pulled even if budget remains."""
    attacks = [_FixedAttack(name="atk", n_attempts=10)]
    judge = _MarkerJudge(high_score_marker="atk#")  # every attempt scores high
    orch = MctsOrchestrator(
        target=StubDocumentTarget(),
        attacks=attacks,
        behaviors=[_behavior()],
        judge=judge,
        budget=Budget(max_queries=10),
        success_threshold=0.6,
    )
    result = await orch.run()
    assert len(result.traces) == 1
    assert result.traces[0].verdict == "success"


async def test_mcts_max_iterations_caps_pulls() -> None:
    attack = _FixedAttack(name="atk", n_attempts=100)
    orch = MctsOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        max_iterations=5,
        success_threshold=0.99,
    )
    result = await orch.run()
    assert len(result.traces) == 5


async def test_mcts_handles_arm_exhaustion() -> None:
    """When an arm's iterator runs out before budget, it's marked
    exhausted and the loop finishes early without raising."""
    short = _FixedAttack(name="short", n_attempts=2)
    long = _FixedAttack(name="long", n_attempts=2)
    orch = MctsOrchestrator(
        target=StubDocumentTarget(),
        attacks=[short, long],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        success_threshold=0.99,
    )
    result = await orch.run()
    assert len(result.traces) == 4  # both arms drained


# ----------------------------------------------------------------------
# Events
# ----------------------------------------------------------------------


async def test_events_emit_run_and_attempt_signals() -> None:
    queue: asyncio.Queue[OrchestratorEvent] = asyncio.Queue()
    attack = _FixedAttack(name="atk", n_attempts=2)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        events=queue,
    )
    await orch.run()
    seen: list[str] = []
    while not queue.empty():
        seen.append((await queue.get()).type)
    assert "run_started" in seen
    assert "attack_started" in seen
    assert seen.count("attempt_started") == 2
    assert seen.count("attempt_completed") == 2
    assert "attack_completed" in seen
    assert "run_completed" in seen


async def test_events_emit_budget_exceeded() -> None:
    queue: asyncio.Queue[OrchestratorEvent] = asyncio.Queue()
    attack = _FixedAttack(name="atk", n_attempts=10)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        budget=Budget(max_queries=1),
        events=queue,
    )
    await orch.run()
    seen: list[str] = []
    while not queue.empty():
        seen.append((await queue.get()).type)
    assert "budget_exceeded" in seen


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


async def test_traces_and_run_result_persist_to_disk(tmp_path: Path) -> None:
    """When ``output_dir`` is set, every trace lands as a JSON file
    under ``<output_dir>/<run_id>/traces/``, and the run-level
    ``run_result.json`` aggregates them."""
    attack = _FixedAttack(name="atk", n_attempts=3)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        output_dir=tmp_path,
        run_id="run-fixed",
    )
    result = await orch.run()
    run_dir = tmp_path / "run-fixed"
    assert run_dir.exists()
    trace_files = sorted((run_dir / "traces").glob("*.json"))
    assert len(trace_files) == 3
    run_result = json.loads((run_dir / "run_result.json").read_text())
    assert run_result["run_id"] == "run-fixed"
    assert len(run_result["traces"]) == 3
    assert run_result["summary_stats"]["total_traces"] == 3
    # Sanity: the persisted RunResult round-trips through the model.
    from advtest_ext.core.models import RunResult

    rebuilt = RunResult.model_validate(run_result)
    assert rebuilt.run_id == result.run_id


# ----------------------------------------------------------------------
# Summary aggregation
# ----------------------------------------------------------------------


def test_summarise_excludes_error_traces_from_asr() -> None:
    from advtest_ext.core.models import AttackTrace, Verdict

    payload = Payload(id="p", text="x", modality="text", generator_name="atk")
    verdicts: list[Verdict] = ["success", "refused", "error", "success"]
    traces = [
        AttackTrace(
            id=f"t{i}",
            run_id="r",
            behavior_id="b1",
            payload=payload,
            target_name="stub",
            raw_request="x",
            raw_response="y",
            verdict=v,
        )
        for i, v in enumerate(verdicts)
    ]
    stats = summarise(traces)
    assert stats.total_traces == 4
    assert stats.successful_traces == 2
    # Denominator excludes the error trace.
    row = stats.asr_breakdown[0]
    assert row.attempts == 3
    assert row.successes == 2
    assert stats.overall_asr == pytest.approx(2 / 3)


def test_summarise_excludes_invalid_technical_not_just_error() -> None:
    # A judge that itself errored (carried-forward score) and an anti-automation
    # / selector-drift technical failure are non-measurements and must be
    # excluded from the ASR, even though their verdict is not "error".
    from advtest_ext.core.models import AttackTrace

    payload = Payload(id="p", text="x", modality="text", generator_name="atk")
    traces = [
        AttackTrace(  # a real success
            id="t0", run_id="r", behavior_id="b1", payload=payload,
            target_name="stub", raw_request="x", raw_response="y", verdict="success",
        ),
        AttackTrace(  # carried-forward "success" but the judge errored -> excluded
            id="t1", run_id="r", behavior_id="b1", payload=payload,
            target_name="stub", raw_request="x", raw_response="y", verdict="success",
            metadata={"tap_crescendo": {"judge_error": True}},
        ),
        AttackTrace(  # selector-drift failure surfaced as a refusal -> excluded
            id="t2", run_id="r", behavior_id="b1", payload=payload,
            target_name="stub", raw_request="x", raw_response="y", verdict="refused",
            metadata={"error_kind": "selector_drift"},
        ),
        AttackTrace(  # a genuine refusal -> counts as a non-success attempt
            id="t3", run_id="r", behavior_id="b1", payload=payload,
            target_name="stub", raw_request="x", raw_response="y", verdict="refused",
        ),
    ]
    stats = summarise(traces)
    assert stats.total_traces == 4  # all visible in the failure surface
    assert stats.successful_traces == 1  # the judge-errored success is not counted
    row = stats.asr_breakdown[0]
    assert row.attempts == 2  # only the real success + the genuine refusal
    assert stats.overall_asr == pytest.approx(1 / 2)


def test_summarise_rolls_up_asr_per_carrier() -> None:
    from advtest_ext.core.models import AttackTrace, HidingTechnique, Verdict

    payload = Payload(id="p", text="x", modality="text", generator_name="ii")

    def _tech(tid: str, ac: str) -> HidingTechnique:
        return HidingTechnique(
            id=tid,
            name=tid,
            description="",
            references=[],
            attack_class=ac,  # type: ignore[arg-type]
            applies_to=[],
            requires_capability=[],
        )

    def _trace(i: int, tech: HidingTechnique | None, v: Verdict) -> AttackTrace:
        return AttackTrace(
            id=f"t{i}",
            run_id="r",
            behavior_id="b1",
            payload=payload,
            technique=tech,
            target_name="stub",
            raw_request="x",
            raw_response="y",
            verdict=v,
        )

    wt = _tech("white_text", "white_text")
    hs = _tech("hidden_span", "white_text")
    traces = [
        _trace(0, wt, "success"),
        _trace(1, wt, "refused"),
        _trace(2, hs, "success"),
        _trace(3, hs, "error"),  # error → excluded from the denominator
        _trace(4, None, "success"),  # direct prompt (no carrier) → not rolled up
    ]
    stats = summarise(traces)
    by_id = {c.carrier_id: c for c in stats.asr_by_carrier}

    assert set(by_id) == {"white_text", "hidden_span"}  # the None-technique trace is excluded
    assert (by_id["white_text"].successes, by_id["white_text"].attempts) == (1, 2)
    assert by_id["white_text"].asr == pytest.approx(0.5)
    assert by_id["white_text"].attack_class == "white_text"
    # the error trace is excluded → hidden_span counts 1 attempt, not 2
    assert (by_id["hidden_span"].successes, by_id["hidden_span"].attempts) == (1, 1)
    assert by_id["hidden_span"].asr == pytest.approx(1.0)
    # most-effective carrier sorts first
    assert stats.asr_by_carrier[0].carrier_id == "hidden_span"


# ----------------------------------------------------------------------
# Validation
# ----------------------------------------------------------------------


def test_orchestrator_rejects_empty_inputs() -> None:
    with pytest.raises(ValueError, match="AttackPlugin"):
        GridOrchestrator(
            target=StubDocumentTarget(),
            attacks=[],
            behaviors=[_behavior()],
            judge=_ConstantJudge(),
        )
    with pytest.raises(ValueError, match="Behavior"):
        GridOrchestrator(
            target=StubDocumentTarget(),
            attacks=[_FixedAttack(name="atk")],
            behaviors=[],
            judge=_ConstantJudge(),
        )


def test_mcts_rejects_invalid_args() -> None:
    with pytest.raises(ValueError, match="success_threshold"):
        MctsOrchestrator(
            target=StubDocumentTarget(),
            attacks=[_FixedAttack(name="atk")],
            behaviors=[_behavior()],
            judge=_ConstantJudge(),
            success_threshold=1.5,
        )
    with pytest.raises(ValueError, match="exploration_c"):
        MctsOrchestrator(
            target=StubDocumentTarget(),
            attacks=[_FixedAttack(name="atk")],
            behaviors=[_behavior()],
            judge=_ConstantJudge(),
            exploration_c=-1.0,
        )


# ----------------------------------------------------------------------
# parent_id / depth: first-class columns + orchestrator hoisting
# ----------------------------------------------------------------------


def test_attack_trace_persists_parent_id_and_depth_round_trip() -> None:
    """Pure-model lock: ``AttackTrace`` carries ``parent_id`` and
    ``depth`` as first-class fields. They survive
    ``model_dump_json`` → ``model_validate_json`` losslessly so any
    on-disk format (per-trace JSON file today, JSONL or SQLite later)
    reflects them as real columns rather than burying them in
    metadata blobs."""
    from advtest_ext.core.models import AttackTrace

    payload = Payload(id="p", text="x", modality="text", generator_name="tap")
    trace = AttackTrace(
        id="t-leaf",
        run_id="r-1",
        behavior_id="b1",
        payload=payload,
        target_name="stub",
        raw_request="x",
        raw_response="y",
        verdict="success",
        parent_id="t-root",
        depth=3,
    )
    restored = AttackTrace.model_validate_json(trace.model_dump_json())
    assert restored == trace
    assert restored.parent_id == "t-root"
    assert restored.depth == 3


def test_attack_trace_defaults_parent_id_none_and_depth_zero() -> None:
    """Single-step attacks must end up with ``parent_id=None`` and
    ``depth=0`` by default. Locks the back-compat path: legacy traces
    that don't carry these fields still validate cleanly."""
    from advtest_ext.core.models import AttackTrace

    payload = Payload(id="p", text="x", modality="text", generator_name="static")
    trace = AttackTrace(
        id="t",
        run_id="r",
        behavior_id="b1",
        payload=payload,
        target_name="stub",
        raw_request="x",
        raw_response="y",
    )
    assert trace.parent_id is None
    assert trace.depth == 0


class _MetadataAttack(AttackPlugin):
    """Yields one attempt with caller-supplied metadata. Used to drive
    the orchestrator's hoist logic with crafted TAP / PAIR / Crescendo
    metadata shapes without spinning up the real attacks (which need
    Provider stubs of their own)."""

    attack_class: AttackPluginClass = "single_turn"

    def __init__(self, *, name: str, metadata: dict[str, Any]) -> None:
        self.name = name
        self.references = ["test"]
        self._metadata = metadata
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Any,
        target: Any,
        budget: Any,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget, context
        yield AttackAttempt(
            payload=Payload(
                id=f"{self.name}-p",
                text="hoist-test",
                modality="text",
                generator_name=self.name,
            ),
            user_prompt="hoist-test",
            metadata=self._metadata,
        )


async def test_orchestrator_hoists_tap_parent_and_depth_onto_trace() -> None:
    """TAP attaches ``parent_id`` and ``depth`` under
    ``metadata['tap']``. The orchestrator surfaces ``depth`` onto the
    trace's first-class column directly; ``parent_id`` is rewritten
    from TAP's in-memory ``node_id`` to the persisted parent trace's
    primary key (or ``None`` if the parent never persisted, like the
    synthetic ``tap-root`` sentinel). Locks the contract that the
    dashboard tree view can SQL-self-join on ``Trace.parent_id``
    without parsing metadata blobs."""
    attack = _MetadataAttack(
        name="tap_like",
        metadata={
            "tap": {
                "parent_id": "tap-root",
                "depth": 2,
                "branch": 1,
                "node_id": "tap-node-abc",
            },
        },
    )
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    assert len(result.traces) == 1
    trace = result.traces[0]
    # ``tap-root`` is the synthetic root that's never persisted; the
    # orchestrator translates it to None so the trace shows as a
    # tree root in the dashboard.
    assert trace.parent_id is None
    assert trace.depth == 2


async def test_orchestrator_hoists_pair_round_as_depth() -> None:
    """PAIR's metadata records ``round`` (1-indexed); the orchestrator
    hoists that into ``depth``. PAIR rounds form a single chain, so
    ``parent_id`` stays None — the chain is reconstructable from
    ``payload.parent_payload_id`` if needed."""
    attack = _MetadataAttack(
        name="pair_like",
        metadata={"pair": {"round": 5, "judge_score": 0.3}},
    )
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    trace = result.traces[0]
    assert trace.parent_id is None
    assert trace.depth == 5


async def test_orchestrator_hoists_crescendo_n_turns_as_depth() -> None:
    """Crescendo's final aggregated attempt records ``n_turns``;
    that becomes the trace's ``depth`` so the dashboard's chain-length
    histogram counts Crescendo runs by length."""
    attack = _MetadataAttack(
        name="crescendo_like",
        metadata={"crescendo": {"n_turns": 4, "final_attempt": True}},
    )
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    trace = result.traces[0]
    assert trace.depth == 4


async def test_orchestrator_hoists_through_composed_payload_metadata() -> None:
    """ComposedAttack nests upstream metadata under
    ``payload_metadata`` (see ``ComposedAttack._merge``). The hoist
    must look there too — otherwise composed runs (TAP ∘
    IndirectInjection) lose their tree linkage."""
    attack = _MetadataAttack(
        name="composed_like",
        metadata={
            "indirect_injection": {"technique_id": "white_text"},
            "payload_metadata": {
                "tap": {"parent_id": "tap-parent-xyz", "depth": 7},
            },
        },
    )
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    trace = result.traces[0]
    # ``tap-parent-xyz`` was never persisted (this attack only emits
    # one attempt, so no prior trace registered that node id). The
    # parent_id translation falls back to None — same shape as the
    # depth-1 root case.
    assert trace.parent_id is None
    assert trace.depth == 7


async def test_orchestrator_defaults_to_none_zero_for_single_step_attacks() -> None:
    """Single-step attacks (Static, BoN-only, IndirectInjection alone,
    PAP) emit no chain metadata; the trace stays at ``parent_id=None``
    / ``depth=0``. Locks the back-compat default for the bulk of
    today's runs."""
    attack = _FixedAttack(name="single_step", n_attempts=1)
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    trace = result.traces[0]
    assert trace.parent_id is None
    assert trace.depth == 0


class _TapChainAttack(AttackPlugin):
    """Yields a sequence of TAP-shaped attempts so the per-iteration
    ``node_id → trace_id`` translation can be exercised end-to-end.
    Each attempt's metadata names its own ``node_id`` and its
    ``parent_id`` (a node id from a prior attempt — or the synthetic
    ``tap-root`` for depth-1)."""

    attack_class: AttackPluginClass = "multi_turn"

    def __init__(self, *, chain: list[dict[str, Any]]) -> None:
        self.name = "tap_chain"
        self.references = ["test"]
        self._chain = chain
        super().__init__()

    async def generate(
        self,
        *,
        behavior: Any,
        target: Any,
        budget: Any,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[AttackAttempt]:
        del target, budget, context
        for step in self._chain:
            yield AttackAttempt(
                payload=Payload(
                    id=f"chain-{step['node_id']}",
                    text=f"prompt-{step['node_id']}",
                    modality="text",
                    generator_name=self.name,
                ),
                user_prompt=f"prompt-{step['node_id']}",
                metadata={"tap": step},
            )


async def test_orchestrator_chains_tap_parent_ids_via_node_id_map() -> None:
    """End-to-end: a TAP-shaped attack yielding a root (parent =
    ``tap-root`` sentinel) followed by a child (parent = root's
    node id) must produce two persisted traces where the child's
    ``Trace.parent_id`` equals the root's actual ``Trace.id`` — not
    the in-memory TAP node id and not None.

    This is the contract the dashboard's tree view depends on: a
    SQL self-join on ``traces.parent_id = traces.id`` must walk the
    real attack tree, with the synthetic root translating to None
    so depth-1 branches sit at the top of the rendered forest."""
    attack = _TapChainAttack(
        chain=[
            {
                "node_id": "tap-node-root",
                "parent_id": "tap-root",
                "depth": 1,
                "branch": 1,
            },
            {
                "node_id": "tap-node-child",
                "parent_id": "tap-node-root",
                "depth": 2,
                "branch": 1,
            },
            {
                "node_id": "tap-node-grandchild",
                "parent_id": "tap-node-child",
                "depth": 3,
                "branch": 1,
            },
        ],
    )
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
    )
    result = await orch.run()
    assert len(result.traces) == 3
    root_trace, child_trace, grandchild_trace = result.traces
    # Depth column is hoisted directly from metadata.
    assert (root_trace.depth, child_trace.depth, grandchild_trace.depth) == (1, 2, 3)
    # The root's parent is the synthetic ``tap-root`` sentinel,
    # which is never persisted → translates to None.
    assert root_trace.parent_id is None
    # The child's parent is the root's TAP node_id, which WAS
    # registered when the root persisted → translates to the
    # root's actual trace id.
    assert child_trace.parent_id == root_trace.id
    # And the chain extends one more hop.
    assert grandchild_trace.parent_id == child_trace.id


async def test_persisted_traces_keep_parent_id_and_depth_on_disk(
    tmp_path: Path,
) -> None:
    """Round-trip via the persistence layer: a trace with hoisted
    chain columns must read back losslessly from
    ``run_result.json``. This is the requirement that motivated the
    hoist — chain reconstruction should never depend on metadata
    JSON survival."""
    attack = _MetadataAttack(
        name="tap_like",
        metadata={"tap": {"parent_id": "tap-root", "depth": 9}},
    )
    orch = GridOrchestrator(
        target=StubDocumentTarget(),
        attacks=[attack],
        behaviors=[_behavior()],
        judge=_ConstantJudge(score=0.1),
        output_dir=tmp_path,
        run_id="run-chain",
    )
    await orch.run()
    rr_path = tmp_path / "run-chain" / "run_result.json"
    payload = json.loads(rr_path.read_text())
    persisted = payload["traces"][0]
    # Synthetic ``tap-root`` translates to None at persistence —
    # depth survives losslessly.
    assert persisted["parent_id"] is None
    assert persisted["depth"] == 9
    # And the per-trace file (the unit a JSONL/SQLite exporter would
    # consume) carries the same columns.
    trace_files = list((tmp_path / "run-chain" / "traces").glob("*.json"))
    assert len(trace_files) == 1
    one = json.loads(trace_files[0].read_text())
    assert one["parent_id"] is None
    assert one["depth"] == 9
