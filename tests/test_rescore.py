"""Tests for ``core.rescore`` — re-judging persisted traces offline.

The scenario these guard is the one that emptied the real database: a
run scored by a judge that couldn't read the behaviour under test
returned 0.0 on every trace, so no Finding was ever promoted and the
review queue never filled. Re-scoring has to reconstruct all of that
from stored responses without re-running the attack, so the properties
under test are:

* the L0 signal layer is READ from storage, never re-evaluated
* re-scoring twice is idempotent per judge
* one judge failure doesn't abort the pass
* ``--dry-run`` really writes nothing

A scripted judge stands in for the LLM so the whole suite stays offline
and deterministic.
"""

from __future__ import annotations

import asyncio

import pytest

from advtest_ext.core.models import JudgeScore
from advtest_ext.core.rescore import (
    placeholder_behavior,
    rescore_traces,
    trace_row_to_domain,
)
from advtest_ext.judges.base import JudgeBase
from advtest_ext.storage import (
    count_hitl,
    get_trace,
    insert_signal_records,
    insert_trace,
    list_findings,
    list_traces_for_run,
    make_engine,
    make_session_factory,
    promote_finding,
    session_scope,
    upsert_run,
)
from tests.test_storage import _domain_trace


class ScriptedJudge(JudgeBase):
    """Returns a fixed score, or raises for trace ids in ``fail_on``."""

    name = "scripted"
    version = "1.0.0"
    description = "test double"

    def __init__(self, score: float = 0.9, fail_on: set[str] | None = None) -> None:
        super().__init__()
        self._score = score
        self._fail_on = fail_on or set()
        self.seen: list[str] = []

    async def evaluate(self, *, trace, behavior) -> JudgeScore:
        if trace.id in self._fail_on:
            raise RuntimeError("judge exploded")
        self.seen.append(trace.id)
        return JudgeScore(
            judge_name=self.name,
            score=self._score,
            reasoning=f"scored {behavior.id}",
            judge_version=self.version,
        )


def _factory():
    return make_session_factory(make_engine("sqlite:///:memory:"))


def _seed(factory, *, run_id="run-r", n=3, response="the assistant leaked XYZZY"):
    with session_scope(factory) as s:
        upsert_run(s, run_id=run_id, target_name="stub", strategy="grid")
        for i in range(n):
            trace = _domain_trace(f"t{i}")
            trace.raw_response = response
            trace.verdict = "refused"
            insert_trace(s, run_id=run_id, trace=trace)
    return run_id


def _run(session, **kw):
    return asyncio.run(rescore_traces(session, **kw))


# ----------------------------------------------------------------------
# Row -> domain
# ----------------------------------------------------------------------


def test_trace_row_to_domain_preserves_what_a_judge_reads() -> None:
    factory = _factory()
    _seed(factory, n=1, response="hello world")
    with session_scope(factory) as s:
        row = list_traces_for_run(s, "run-r")[0]
        trace = trace_row_to_domain(row)
    assert trace.id == row.id
    assert trace.raw_response == "hello world"
    assert trace.behavior_id == row.behavior_id
    assert trace.run_id == "run-r"


def test_placeholder_behavior_for_ids_outside_the_catalogue() -> None:
    """Guide- and recipe-driven runs mint behaviour ids that were never
    catalogue entries; those traces must still be scoreable."""
    b = placeholder_behavior("sheets_skill_leak")
    assert b.id == "sheets_skill_leak"
    assert b.success_criteria


# ----------------------------------------------------------------------
# Core re-scoring
# ----------------------------------------------------------------------


def test_rescore_flips_verdicts_and_promotes_findings() -> None:
    """The headline repair: traces stored as ``refused`` under a judge
    that scored 0.0 become successes with a judge that can read them,
    and Findings finally get promoted."""
    factory = _factory()
    run_id = _seed(factory, n=3)

    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.9), threshold=0.5)

    assert stats.n_traces == 3
    assert stats.n_scored == 3
    assert stats.n_changed == 3
    assert all(old == "refused" and new == "success" for _, old, new in stats.verdict_changes)
    assert stats.n_findings == 3

    with session_scope(factory) as s:
        assert all(t.verdict == "success" for t in list_traces_for_run(s, run_id))
        assert len(list_findings(s, run_id=run_id)) == 3


def test_low_score_does_not_promote() -> None:
    factory = _factory()
    run_id = _seed(factory, n=2)
    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.1), threshold=0.5)
    assert stats.n_findings == 0
    with session_scope(factory) as s:
        assert all(t.verdict == "refused" for t in list_traces_for_run(s, run_id))


def test_l0_signals_are_read_not_re_evaluated_and_still_upgrade() -> None:
    """A fired HIGH-PRECISION signal (an exact canary / token echoed) is
    ground truth that the attack landed, so it upgrades the verdict even
    when the judge scores low — the same layered-oracle rule the
    orchestrator applies. The signal is read from storage; nothing
    re-runs it."""
    factory = _factory()
    run_id = _seed(factory, n=2)
    with session_scope(factory) as s:
        insert_signal_records(
            s,
            trace_id="t0",
            records=[{"name": "canary", "kind": "canary_appears", "fired": True}],
        )
        insert_signal_records(
            s,
            trace_id="t1",
            records=[{"name": "canary", "kind": "canary_appears", "fired": False}],
        )

    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.05), threshold=0.5)

    assert stats.n_signal_upgrades == 1
    with session_scope(factory) as s:
        t0 = get_trace(s, "t0")
        t1 = get_trace(s, "t1")
        assert t0 is not None
        assert t1 is not None
        assert t0.verdict == "success"  # high-precision signal fired
        assert t1.verdict == "refused"  # judge said no, no signal


def test_loose_regex_signal_only_lifts_to_partial_not_success() -> None:
    """A low-precision ``regex_match`` (e.g. a tool-name keyword list)
    only CORROBORATES — the token can appear inside a refusal — so on its
    own it lifts a refused verdict to ``partial``, never ``success``, and
    does not mint a Finding."""
    factory = _factory()
    run_id = _seed(factory, n=1)
    with session_scope(factory) as s:
        insert_signal_records(
            s,
            trace_id="t0",
            records=[{"name": "internal_tool_names", "kind": "regex_match", "fired": True}],
        )

    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.05), threshold=0.5)

    assert stats.n_signal_upgrades == 0
    assert stats.n_findings == 0
    with session_scope(factory) as s:
        t0 = get_trace(s, "t0")
        assert t0 is not None
        assert t0.verdict == "partial"


def test_rescoring_twice_is_idempotent_per_judge() -> None:
    """Re-running must replace this judge's score, not append — otherwise
    a trace accumulates duplicate rows and its score history lies."""
    factory = _factory()
    run_id = _seed(factory, n=1)

    for score in (0.9, 0.3):
        with session_scope(factory) as s:
            _run(s, run_id=run_id, judge=ScriptedJudge(score), threshold=0.5)

    with session_scope(factory) as s:
        trace = get_trace(s, "t0")
        assert trace is not None
        scripted = [j for j in trace.judge_scores if j.judge_name == "scripted"]
        assert len(scripted) == 1
        assert scripted[0].score == pytest.approx(0.3)


def test_other_judges_scores_are_left_alone() -> None:
    """Cross-judge comparison on one trace is the whole point of the
    Judge Quality panel, so re-scoring with judge A must not delete
    judge B's row."""
    factory = _factory()
    run_id = _seed(factory, n=1)
    with session_scope(factory) as s:
        _run(s, run_id=run_id, judge=ScriptedJudge(0.9), threshold=0.5)

    other = ScriptedJudge(0.2)
    other.name = "other_judge"
    with session_scope(factory) as s:
        _run(s, run_id=run_id, judge=other, threshold=0.5)

    with session_scope(factory) as s:
        trace = get_trace(s, "t0")
        assert trace is not None
        names = {j.judge_name for j in trace.judge_scores}
    assert {"scripted", "other_judge"} <= names


def test_judge_failure_is_recorded_not_raised() -> None:
    factory = _factory()
    run_id = _seed(factory, n=3)
    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.9, fail_on={"t1"}), threshold=0.5)
    assert stats.n_scored == 2
    assert len(stats.errors) == 1
    assert stats.errors[0][0] == "t1"


def test_empty_responses_are_skipped() -> None:
    """Nothing for a judge to read — must not be sent, and must be
    counted separately so the operator sees why n dropped."""
    factory = _factory()
    run_id = _seed(factory, n=2, response="   ")
    judge = ScriptedJudge(0.9)
    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=judge, threshold=0.5)
    assert stats.n_skipped_empty == 2
    assert stats.n_scored == 0
    assert judge.seen == []


# ----------------------------------------------------------------------
# Review queue
# ----------------------------------------------------------------------


def test_review_budget_fills_the_queue_with_both_slices() -> None:
    """The queue is what a labelling session consumes. The uniform slice
    is load-bearing for an unbiased PPI estimate, so it must actually be
    drawn, not just the high-uncertainty tail."""
    factory = _factory()
    run_id = _seed(factory, n=10)
    with session_scope(factory) as s:
        stats = _run(
            s,
            run_id=run_id,
            judge=ScriptedJudge(0.6),
            threshold=0.5,
            review_budget=6,
            uniform_fraction=0.5,
            seed=1,
        )
    assert stats.n_enqueued_uniform + stats.n_enqueued_disagreement > 0
    assert stats.n_enqueued_uniform == 3
    with session_scope(factory) as s:
        assert count_hitl(s, status="pending") == (
            stats.n_enqueued_uniform + stats.n_enqueued_disagreement
        )


def test_zero_budget_leaves_the_queue_untouched() -> None:
    factory = _factory()
    run_id = _seed(factory, n=4)
    with session_scope(factory) as s:
        _run(s, run_id=run_id, judge=ScriptedJudge(0.9), threshold=0.5, review_budget=0)
    with session_scope(factory) as s:
        assert count_hitl(s, status="pending") == 0


# ----------------------------------------------------------------------
# Dry run
# ----------------------------------------------------------------------


def test_dry_run_reports_churn_but_writes_nothing() -> None:
    factory = _factory()
    run_id = _seed(factory, n=3)
    with session_scope(factory) as s:
        stats = _run(
            s,
            run_id=run_id,
            judge=ScriptedJudge(0.9),
            threshold=0.5,
            review_budget=3,
            dry_run=True,
        )

    # It still tells you what WOULD change...
    assert stats.n_scored == 3
    assert stats.n_changed == 3
    # ...but nothing landed.
    assert stats.n_findings == 0
    assert stats.n_enqueued_uniform == 0
    with session_scope(factory) as s:
        assert all(t.verdict == "refused" for t in list_traces_for_run(s, run_id))
        assert list_findings(s, run_id=run_id) == []
        assert count_hitl(s, status="pending") == 0


# ----------------------------------------------------------------------
# CLI event-loop lifetime
# ----------------------------------------------------------------------


class LoopRecordingJudge(ScriptedJudge):
    """Records the running event loop for every evaluate() call."""

    name = "loop_recorder"

    def __init__(self) -> None:
        super().__init__(0.9)
        self.loops: list[int] = []

    async def evaluate(self, *, trace, behavior) -> JudgeScore:
        self.loops.append(id(asyncio.get_running_loop()))
        return await super().evaluate(trace=trace, behavior=behavior)


def test_cli_scores_every_run_on_a_single_event_loop(tmp_path, monkeypatch) -> None:
    """Regression: the CLI must not call asyncio.run() per run.

    A per-run loop is closed as soon as that run finishes, while the
    judge's HTTP client still holds connections bound to it — the next
    run then dies mid-teardown with 'Event loop is closed'. The symptom
    only appears with a real network client, so the invariant is pinned
    here instead: every judge call across every run shares one loop.
    """
    from typer.testing import CliRunner

    from advtest_ext import _cli_factories
    from advtest_ext.cli import app

    db = tmp_path / "rescore.db"
    factory = make_session_factory(make_engine(f"sqlite:///{db}"))
    for run_id in ("run-a", "run-b", "run-c"):
        with session_scope(factory) as s:
            upsert_run(s, run_id=run_id, target_name="stub", strategy="grid")
            for i in range(2):
                trace = _domain_trace(f"{run_id}-t{i}")
                trace.raw_response = "the assistant leaked XYZZY"
                trace.verdict = "refused"
                insert_trace(s, run_id=run_id, trace=trace)

    judge = LoopRecordingJudge()
    monkeypatch.setattr(_cli_factories, "_resolve_judge", lambda _name: judge)

    result = CliRunner().invoke(
        app,
        ["rescore", "--all", "--judge", "loop_recorder", "--db-url", f"sqlite:///{db}"],
    )

    assert result.exit_code == 0, result.output
    # All three runs were scored...
    assert len(judge.loops) == 6
    # ...on exactly one event loop.
    assert len(set(judge.loops)) == 1


def test_l0_only_win_still_becomes_a_finding() -> None:
    """Regression: a trace the L2 judge scored LOW but a HIGH-PRECISION
    L0 signal (an exact planted token / canary) caught must still promote
    to a Finding.

    Promotion used to require verdict==success AND judge_score>=threshold.
    The L0 upgrade path satisfies the first and fails the second, so
    every signal-detected success was silently dropped — which is how a
    run of genuine successes ends up with an empty Findings table. The
    verdict alone is the gate now, because it already encodes both
    layers.
    """
    factory = _factory()
    run_id = _seed(factory, n=1)
    with session_scope(factory) as s:
        insert_signal_records(
            s,
            trace_id="t0",
            records=[{"name": "execplan_schema", "kind": "token_appears", "fired": True}],
        )

    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.05), threshold=0.5)

    assert stats.n_signal_upgrades == 1
    assert stats.n_findings == 1, "L0-only win must still promote"

    with session_scope(factory) as s:
        findings = list_findings(s, run_id=run_id)
        assert len(findings) == 1
        # The Finding records which layer caught it — a reader must not
        # be told "judge score 0.05 >= threshold 0.5".
        assert "L0 signal" in findings[0].description


def test_regrade_out_of_success_removes_stale_finding() -> None:
    """A trace promoted to a Finding under the old binary rule (a
    loose-regex-only "win") must have that stale Finding retracted when a
    re-score grades it below ``success``. Here the trace starts as a
    success with a Finding, then a re-score with only a low-precision
    regex signal re-grades it to ``partial`` and drops the Finding."""
    factory = _factory()
    run_id = _seed(factory, n=1)
    with session_scope(factory) as s:
        insert_signal_records(
            s,
            trace_id="t0",
            records=[{"name": "internal_tool_names", "kind": "regex_match", "fired": True}],
        )
        # Seed the pre-existing (stale) success + Finding.
        seed_trace = get_trace(s, "t0")
        assert seed_trace is not None
        seed_trace.verdict = "success"
        promote_finding(s, run_id=run_id, trace_id="t0", title="stale", description="old")

    with session_scope(factory) as s:
        assert len(list_findings(s, run_id=run_id)) == 1

    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=ScriptedJudge(0.05), threshold=0.5)

    assert stats.n_findings_removed == 1
    with session_scope(factory) as s:
        t0 = get_trace(s, "t0")
        assert t0 is not None
        assert t0.verdict == "partial"
        assert list_findings(s, run_id=run_id) == []


class FailingJudge(ScriptedJudge):
    """Returns an errored JudgeScore — the shape a judge produces when
    the model reply is empty or unparseable, not a real verdict."""

    name = "failing"

    async def evaluate(self, *, trace, behavior) -> JudgeScore:
        self.seen.append(trace.id)
        return JudgeScore(
            judge_name=self.name,
            score=0.0,
            reasoning="could not parse decomposed verdict: ''",
            error=True,
        )


def test_judge_errors_do_not_overwrite_verdicts() -> None:
    """Regression: an empty judge reply must not be persisted as 0.0.

    A failed judge call still returns a score, and 0.0 is the safe
    choice — but 0.0 is also a real verdict ("refused"). Persisting it
    would overwrite a genuine success with a fake refusal and feed that
    into the gold set, biasing the ASR down and corrupting every
    agreement statistic computed from it.
    """
    factory = _factory()
    run_id = _seed(factory, n=2)
    with session_scope(factory) as s:
        seed_trace = get_trace(s, "t0")
        assert seed_trace is not None
        seed_trace.verdict = "success"

    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=FailingJudge(), threshold=0.5)

    # Counted as failures, not as scored data.
    assert stats.n_scored == 0
    assert len(stats.errors) == 2
    assert "judge error" in stats.errors[0][1]
    # And nothing was written.
    assert stats.n_findings == 0
    with session_scope(factory) as s:
        t0 = get_trace(s, "t0")
        assert t0 is not None
        assert t0.verdict == "success"  # preserved
        assert not [j for j in t0.judge_scores if j.judge_name == "failing"]


def test_errored_score_never_reaches_the_sampler() -> None:
    """An errored trace must not be enqueued for review either — a
    labeller scoring a judge failure teaches the gold set nothing."""
    factory = _factory()
    run_id = _seed(factory, n=4)
    with session_scope(factory) as s:
        stats = _run(s, run_id=run_id, judge=FailingJudge(), threshold=0.5, review_budget=4)
    assert stats.n_enqueued_uniform == 0
    assert stats.n_enqueued_disagreement == 0
    with session_scope(factory) as s:
        assert count_hitl(s, status="pending") == 0


def test_rescoring_twice_does_not_duplicate_the_review_queue() -> None:
    """Regression: re-scoring is a normal repeatable operation, but each
    pass used to stack another copy of every sampled trace onto the
    queue. Five passes meant a reviewer met the same trace five times,
    and repeat labels on one trace corrupt the human-ceiling maths."""
    factory = _factory()
    run_id = _seed(factory, n=6)

    for _ in range(3):
        with session_scope(factory) as s:
            _run(
                s,
                run_id=run_id,
                judge=ScriptedJudge(0.6),
                threshold=0.5,
                review_budget=4,
                seed=1,
            )

    with session_scope(factory) as s:
        pending = count_hitl(s, status="pending")
    assert pending == 4, f"expected 4 queued traces, got {pending}"
