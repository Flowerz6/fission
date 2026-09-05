"""Tests for the measurement-spine gold set (``GoldLabel``).

The gold set is the calibration ground truth the PPI metric trusts and
the flywheel later trains on (``docs/FISSION_MEASUREMENT_SPINE.md`` §2).
Two properties matter and are exercised here: the oracle prediction is
frozen at sample time, and a label outlives the trace/run it came from
(``ON DELETE SET NULL``, not CASCADE) so deleting a run can never
destroy ground truth.

In-memory SQLite is the default; the schema is Postgres-portable so
these round-trips hold there too.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.storage import (
    GoldLabel,
    Run,
    count_gold_labels,
    get_gold_label,
    insert_gold_label,
    insert_trace,
    list_gold_labels,
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)
from advtest_ext.storage.repository import _SAMPLE_REASONS, _VERDICTS

# Reuse the domain-trace builder from the storage suite so the seeded
# trace matches what the orchestrator actually persists.
from tests.test_storage import _domain_trace


def _factory():
    engine = make_engine("sqlite:///:memory:")
    return make_session_factory(engine)


def _seed_run_with_trace(factory, *, run_id="run-g", trace_id="t1") -> None:
    with session_scope(factory) as s:
        upsert_run(s, run_id=run_id, target_name="stub", strategy="grid")
        insert_trace(s, run_id=run_id, trace=_domain_trace(trace_id))


def test_gold_label_round_trips_and_freezes_oracle_prediction() -> None:
    factory = _factory()
    _seed_run_with_trace(factory)

    with session_scope(factory) as s:
        insert_gold_label(
            s,
            trace_id="t1",
            run_id="run-g",
            behavior_id="behaviour_x",
            oracle_verdict="success",
            oracle_score=0.42,
            oracle_layers_fired={"canary_appears": True, "ipi_verdict": 0.42},
            response_snapshot="the assistant leaked the canary XYZZY",
            sample_reason="disagreement",
            expert_verdict="refused",
            expert_confidence=5,
            reviewer_id="alice",
            review_seconds=18,
            reason="canary present but only quoted, not acted on",
        )

    with session_scope(factory) as s:
        labels = list_gold_labels(s, run_id="run-g")
        assert len(labels) == 1
        gl = labels[0]
        # Oracle prediction is frozen exactly as supplied at sample time.
        assert gl.oracle_verdict == "success"
        assert gl.oracle_score == pytest.approx(0.42)
        assert gl.oracle_layers_fired == {"canary_appears": True, "ipi_verdict": 0.42}
        # Expert label is the ground truth and can disagree with the oracle.
        assert gl.expert_verdict == "refused"
        assert gl.expert_confidence == 5
        assert gl.sample_reason == "disagreement"
        assert gl.response_snapshot.endswith("XYZZY")
        assert gl.frozen is True


def test_expert_confidence_is_clamped_to_1_5() -> None:
    factory = _factory()
    _seed_run_with_trace(factory)
    with session_scope(factory) as s:
        insert_gold_label(
            s,
            trace_id="t1",
            run_id="run-g",
            behavior_id="b",
            oracle_verdict="success",
            expert_verdict="success",
            sample_reason="uniform",
            expert_confidence=99,
        )
        insert_gold_label(
            s,
            trace_id="t1",
            run_id="run-g",
            behavior_id="b",
            oracle_verdict="success",
            expert_verdict="success",
            sample_reason="uniform",
            expert_confidence=-4,
        )
    with session_scope(factory) as s:
        confs = sorted(gl.expert_confidence for gl in list_gold_labels(s))
        assert confs == [1, 5]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"oracle_verdict": "bogus"},
        {"expert_verdict": "bogus"},
        {"sample_reason": "bogus"},
    ],
)
def test_invalid_enums_fail_loudly(kwargs) -> None:
    factory = _factory()
    _seed_run_with_trace(factory)
    base: dict[str, Any] = dict(
        trace_id="t1",
        run_id="run-g",
        behavior_id="b",
        oracle_verdict="success",
        expert_verdict="success",
        sample_reason="uniform",
    )
    base.update(kwargs)
    with session_scope(factory) as s, pytest.raises(ValueError):
        insert_gold_label(s, **base)


def test_label_survives_run_deletion() -> None:
    """A gold label is permanent ground truth: deleting its source run
    must NOT delete the label. GoldLabel is deliberately absent from
    Run's ORM cascade relationships, so this holds on every dialect;
    the ``ON DELETE SET NULL`` FK additionally nulls the pointers on a
    FK-enforcing backend (Postgres). Either way the denormalised
    behaviour_id / response_snapshot keep the label self-contained."""
    factory = _factory()
    _seed_run_with_trace(factory)
    with session_scope(factory) as s:
        insert_gold_label(
            s,
            trace_id="t1",
            run_id="run-g",
            behavior_id="behaviour_x",
            oracle_verdict="success",
            expert_verdict="success",
            sample_reason="uniform",
            response_snapshot="frozen text",
        )

    with session_scope(factory) as s:
        run = s.get(Run, "run-g")
        s.delete(run)  # cascades to traces; must NOT reach gold labels

    with session_scope(factory) as s:
        labels = list_gold_labels(s)
        assert len(labels) == 1, "gold label must outlive its run"
        gl = labels[0]
        # Self-contained regardless of whether the FK was nulled.
        assert gl.behavior_id == "behaviour_x"
        assert gl.response_snapshot == "frozen text"
        assert gl.expert_verdict == "success"


def test_count_and_filter_by_sample_slice() -> None:
    factory = _factory()
    _seed_run_with_trace(factory)
    with session_scope(factory) as s:
        for reason in ("uniform", "uniform", "disagreement"):
            insert_gold_label(
                s,
                trace_id="t1",
                run_id="run-g",
                behavior_id="b",
                oracle_verdict="success",
                expert_verdict="success",
                sample_reason=reason,
            )
    with session_scope(factory) as s:
        assert count_gold_labels(s) == 3
        assert count_gold_labels(s, run_id="run-g") == 3
        assert count_gold_labels(s, run_id="nope") == 0
        # The uniform slice is the one PPI uses for an unbiased estimate.
        assert len(list_gold_labels(s, sample_reason="uniform")) == 2
        assert len(list_gold_labels(s, sample_reason="disagreement")) == 1


def test_get_gold_label_by_id() -> None:
    factory = _factory()
    _seed_run_with_trace(factory)
    with session_scope(factory) as s:
        label = insert_gold_label(
            s,
            trace_id="t1",
            run_id="run-g",
            behavior_id="b",
            oracle_verdict="success",
            expert_verdict="partial",
            sample_reason="uniform",
        )
        s.flush()
        label_id = label.id
    with session_scope(factory) as s:
        fetched = get_gold_label(s, label_id)
        assert fetched is not None
        assert fetched.expert_verdict == "partial"
        assert get_gold_label(s, "missing") is None


def test_enum_sets_are_the_contract() -> None:
    # Guard the allowed vocabularies so a widening is a deliberate edit.
    assert _VERDICTS == frozenset({"success", "partial", "refused", "error"})
    assert _SAMPLE_REASONS == frozenset({"disagreement", "uniform"})


def test_gold_label_is_exported() -> None:
    assert GoldLabel.__tablename__ == "gold_labels"


def test_ppi_asr_for_run_uses_uniform_slice_only() -> None:
    """End-to-end DB glue: PPI reads the run's oracle counts from Trace
    and the rectifier from the UNIFORM gold-label slice (the disagreement
    slice must NOT enter the unbiased estimate)."""
    from advtest_ext.core.ppi import ppi_asr_for_run
    from advtest_ext.storage import insert_trace

    factory = _factory()
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-ppi", target_name="stub", strategy="grid")
        # 10 traces, oracle calls 8 success → oracle ASR 0.8.
        for i in range(10):
            v = "success" if i < 8 else "refused"
            insert_trace(
                s,
                run_id="run-ppi",
                trace=_domain_trace(f"t{i}", verdict=v, score=0.9 if v == "success" else 0.1),
            )
        # Uniform slice: on 4 labels the oracle over-called success twice.
        for i in range(4):
            insert_gold_label(
                s,
                trace_id=f"t{i}",
                run_id="run-ppi",
                behavior_id="b",
                oracle_verdict="success",
                expert_verdict="refused" if i < 2 else "success",
                sample_reason="uniform",
            )
        # A disagreement-slice label that would skew the estimate if used.
        insert_gold_label(
            s,
            trace_id="t9",
            run_id="run-ppi",
            behavior_id="b",
            oracle_verdict="refused",
            expert_verdict="refused",
            sample_reason="disagreement",
        )

    with session_scope(factory) as s:
        est = ppi_asr_for_run(s, "run-ppi")
        assert est.method == "ppi"
        assert est.n_total == 10
        assert est.oracle_asr == pytest.approx(0.8)
        assert est.n_labeled == 4  # uniform only — disagreement excluded
        # rectifier = mean(1-0, 1-0, 1-1, 1-1) = 0.5 → ASR = 0.8 - 0.5 = 0.3
        assert est.rectifier == pytest.approx(0.5)
        assert est.asr == pytest.approx(0.3)


# ----------------------------------------------------------------------
# Rater panels — the shape core.agreement consumes
# ----------------------------------------------------------------------


def test_rater_panels_group_independent_reviews_of_one_trace() -> None:
    """Two labellers reviewing the same trace produce two GoldLabel rows
    (the insert mints a fresh id rather than upserting on trace_id). The
    panel query regroups them so the trace contributes ONE consensus item
    and ONE multi-rater panel — which is what makes the human-ceiling
    alpha computable at all."""
    from advtest_ext.storage import gold_label_rater_panels

    factory = _factory()
    _seed_run_with_trace(factory)

    with session_scope(factory) as s:
        for reviewer, verdict in (("alice", "success"), ("bob", "partial")):
            insert_gold_label(
                s,
                trace_id="t1",
                run_id="run-g",
                behavior_id="behaviour_x",
                oracle_verdict="success",
                sample_reason="uniform",
                expert_verdict=verdict,
                reviewer_id=reviewer,
            )

    with session_scope(factory) as s:
        panels = gold_label_rater_panels(s, run_id="run-g")

    assert len(panels) == 1
    trace_id, oracle_verdict, experts = panels[0]
    assert trace_id == "t1"
    assert oracle_verdict == "success"
    assert sorted(experts) == ["partial", "success"]


def test_agreement_for_gold_set_reads_frozen_predictions() -> None:
    """End-to-end: gold labels in, Judge Quality numbers out. A single
    labeller means kappa is computable but the ceiling is not, and the
    report has to say so rather than render a zero."""
    from advtest_ext.core.agreement import agreement_for_gold_set

    factory = _factory()
    _seed_run_with_trace(factory)
    with session_scope(factory) as s:
        insert_trace(s, run_id="run-g", trace=_domain_trace("t2"))
        insert_trace(s, run_id="run-g", trace=_domain_trace("t3"))

    # Oracle called all three success; the expert overturns one.
    with session_scope(factory) as s:
        for trace_id, expert in (("t1", "success"), ("t2", "success"), ("t3", "refused")):
            insert_gold_label(
                s,
                trace_id=trace_id,
                run_id="run-g",
                behavior_id="behaviour_x",
                oracle_verdict="success",
                sample_reason="uniform",
                expert_verdict=expert,
                reviewer_id="alice",
            )

    with session_scope(factory) as s:
        report = agreement_for_gold_set(s, run_id="run-g")

    assert report.n == 3
    assert not report.has_ceiling  # only one labeller so far
    # One of three predicted successes was overturned.
    assert report.false_positive.numerator == 1
    assert report.false_positive.n == 3
    assert report.false_positive.is_estimable
    # ...and with n=3 the interval is necessarily enormous. That is the
    # honest rendering, not a defect.
    assert report.false_positive.ci_high > 0.6


def test_repeat_labels_by_one_reviewer_do_not_fake_a_ceiling() -> None:
    """Regression: the human ceiling must measure agreement between
    DIFFERENT people.

    A duplicated queue entry (or a reviewer revisiting a trace) produced
    several GoldLabel rows with the same reviewer_id. Grouped naively by
    trace they looked like independent raters, so Krippendorff's alpha
    ended up measuring one person's self-consistency — reliably far
    higher than inter-rater agreement, and inflating the very ceiling
    the oracle is scored against.
    """
    from advtest_ext.storage import gold_label_rater_panels

    factory = _factory()
    _seed_run_with_trace(factory)

    with session_scope(factory) as s:
        # Alice labels the same trace three times; Bob once.
        for verdict in ("success", "partial", "success"):
            insert_gold_label(
                s,
                trace_id="t1",
                run_id="run-g",
                behavior_id="behaviour_x",
                oracle_verdict="success",
                sample_reason="uniform",
                expert_verdict=verdict,
                reviewer_id="alice",
            )
        insert_gold_label(
            s,
            trace_id="t1",
            run_id="run-g",
            behavior_id="behaviour_x",
            oracle_verdict="success",
            sample_reason="uniform",
            expert_verdict="refused",
            reviewer_id="bob",
        )

    with session_scope(factory) as s:
        panels = gold_label_rater_panels(s, run_id="run-g")

    assert len(panels) == 1
    _tid, _oracle, experts = panels[0]
    # Two reviewers, not four labels.
    assert len(experts) == 2
    assert sorted(experts) == ["refused", "success"]


def test_unattributed_labels_collapse_to_one_anonymous_rater() -> None:
    """Labels with no reviewer_id cannot be attributed, so they must not
    be counted as separate people — conservative by construction, since
    collapsing them can only lower the ceiling, never inflate it."""
    from advtest_ext.storage import gold_label_rater_panels

    factory = _factory()
    _seed_run_with_trace(factory)
    with session_scope(factory) as s:
        for verdict in ("success", "success", "success"):
            insert_gold_label(
                s,
                trace_id="t1",
                run_id="run-g",
                behavior_id="behaviour_x",
                oracle_verdict="success",
                sample_reason="uniform",
                expert_verdict=verdict,
            )

    with session_scope(factory) as s:
        panels = gold_label_rater_panels(s, run_id="run-g")

    assert len(panels[0][2]) == 1, "three unattributed labels are one anonymous rater"
