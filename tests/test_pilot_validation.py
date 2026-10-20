"""Validation records require review provenance and are immutable once set."""

from __future__ import annotations

import pytest

from advtest_ext.delivery.export import build_dataset_from_traces
from advtest_ext.delivery.validation import mark_trace_validation
from advtest_ext.storage import (
    get_trace,
    insert_trace,
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)
from tests.test_storage import _domain_trace


def _factory():
    return make_session_factory(make_engine("sqlite:///:memory:"))


def _record(session, trace_id: str = "t1", *, effect_verified: bool = True) -> None:
    mark_trace_validation(
        session,
        trace_id=trace_id,
        expected_run_id="r1",
        candidate_id="candidate-1",
        episode_id="episode-1",
        environment_id="fixture",
        target_version="target-v1",
        evaluator_version="evaluator-v1",
        evidence_ref="review:case-1",
        verification_source="human_review",
        effect_verified=effect_verified,
    )


def test_review_record_is_persisted_and_idempotent() -> None:
    factory = _factory()
    with session_scope(factory) as session:
        upsert_run(session, run_id="r1", target_name="fixture", strategy="grid")
        insert_trace(session, run_id="r1", trace=_domain_trace("t1", verdict="success"))
        _record(session)
        _record(session)
    with session_scope(factory) as session:
        trace = get_trace(session, "t1")
        assert trace is not None
        assert trace.extra_metadata["pilot_validation"]["evidence_ref"] == "review:case-1"
        dataset = build_dataset_from_traces([trace], run_ids=["r1"], min_validation_trials=1)
    assert len(dataset.successful) == 1
    assert dataset.successful[0].success_rate.trials_n == 1


def test_review_record_rejects_missing_evidence_and_conflicting_overwrite() -> None:
    factory = _factory()
    with session_scope(factory) as session:
        upsert_run(session, run_id="r1", target_name="fixture", strategy="grid")
        insert_trace(session, run_id="r1", trace=_domain_trace("t1", verdict="success"))
        _record(session)
        with pytest.raises(ValueError, match="different validation record"):
            _record(session, effect_verified=False)
        with pytest.raises(ValueError, match="nonempty"):
            mark_trace_validation(
                session,
                trace_id="t1",
                expected_run_id="r1",
                candidate_id="candidate-1",
                episode_id="episode-2",
                environment_id="fixture",
                target_version="target-v1",
                evaluator_version="evaluator-v1",
                evidence_ref="",
                verification_source="human_review",
                effect_verified=True,
            )
