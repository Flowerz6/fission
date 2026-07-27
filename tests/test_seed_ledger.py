"""Tests for the seed effectiveness ledger.

The ledger is the downstream half of the passive-human loop: it decides
which SEEDS a human should triage between runs. The load-bearing logic is
the status classification — a seed that ever converts is working; among
the rest, the most actionable signal (a near-miss a human can close)
outranks a diffuse refusal or drift pattern. These pin that ordering and
the aggregation, and lock the honest empty state.
"""

from __future__ import annotations

from advtest_ext.core.models import JudgeScore
from advtest_ext.dashboard.seed_ledger import SeedRow, build_seed_ledger
from advtest_ext.storage import (
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)


def _factory():
    return make_session_factory(make_engine("sqlite:///:memory:"))


def _seed_trace(
    factory, *, run_id, trace_id, behavior, verdict, score=0.0, response="ok", drift=None
):
    from advtest_ext.storage import insert_trace
    from tests.test_storage import _domain_trace

    with session_scope(factory) as s:
        upsert_run(s, run_id=run_id, target_name="stub", strategy="grid")
        t = _domain_trace(trace_id)
        t.behavior_id = behavior
        t.verdict = verdict
        t.raw_response = response
        if score:
            t.judge_scores = [JudgeScore(judge_name="j", score=score)]
        if drift:
            t.metadata = {"drift": drift}
        insert_trace(s, run_id=run_id, trace=t)


# ----------------------------------------------------------------------
# Status classification (the triage logic)
# ----------------------------------------------------------------------


def test_status_converging_beats_everything() -> None:
    """A seed that ever converts is working, regardless of noise around it."""
    r = SeedRow(seed_id="s", attempts=10, conversions=1, near_misses=5, refusals=4, drift_events=9)
    assert r.status == "converging"
    assert not r.needs_triage


def test_near_miss_is_the_top_triage_signal() -> None:
    """No conversion but a partial-band attempt → the cheapest human win,
    ranked above refusal and drift."""
    r = SeedRow(seed_id="s", attempts=10, conversions=0, near_misses=2, refusals=5, drift_events=8)
    assert r.status == "near-miss"
    assert r.needs_triage


def test_refused_when_majority_blocked_and_no_near_miss() -> None:
    r = SeedRow(seed_id="s", attempts=10, conversions=0, near_misses=0, refusals=6)
    assert r.status == "refused"


def test_drifting_when_only_signal_is_drift() -> None:
    r = SeedRow(seed_id="s", attempts=4, conversions=0, near_misses=0, refusals=0, drift_events=3)
    assert r.status == "drifting"


def test_no_traction_when_nothing_fired() -> None:
    r = SeedRow(seed_id="s", attempts=5)
    assert r.status == "no-traction"
    assert r.needs_triage


# ----------------------------------------------------------------------
# Aggregation across runs
# ----------------------------------------------------------------------


def test_ledger_aggregates_a_seed_across_runs() -> None:
    factory = _factory()
    # Same seed in two runs: one success, one partial-band miss.
    _seed_trace(factory, run_id="r1", trace_id="t1", behavior="leak", verdict="success", score=0.8)
    _seed_trace(factory, run_id="r2", trace_id="t2", behavior="leak", verdict="refused", score=0.3)

    with session_scope(factory) as s:
        ledger = build_seed_ledger(s)

    assert ledger.total_seeds == 1
    row = ledger.rows[0]
    assert row.attempts == 2
    assert row.conversions == 1
    assert row.runs == 2
    assert row.best_score == 0.8
    # It converged, so the partial is not counted as an open near-miss.
    assert row.status == "converging"


def test_near_miss_counted_only_without_conversion() -> None:
    factory = _factory()
    _seed_trace(factory, run_id="r1", trace_id="t1", behavior="stuck", verdict="refused", score=0.3)
    _seed_trace(
        factory, run_id="r1", trace_id="t2", behavior="stuck", verdict="refused", score=0.35
    )

    with session_scope(factory) as s:
        row = build_seed_ledger(s).rows[0]
    assert row.conversions == 0
    assert row.near_misses == 2
    assert row.status == "near-miss"


def test_drift_events_are_counted_from_metadata() -> None:
    factory = _factory()
    _seed_trace(
        factory,
        run_id="r1",
        trace_id="t1",
        behavior="wander",
        verdict="refused",
        drift=[{"scope": "attacker_goal", "kind": "off_mechanism", "detail": "d"}],
    )
    with session_scope(factory) as s:
        row = build_seed_ledger(s).rows[0]
    assert row.drift_events == 1
    assert row.status == "drifting"


def test_ordering_puts_near_miss_first_converging_last() -> None:
    factory = _factory()
    _seed_trace(factory, run_id="r1", trace_id="a", behavior="good", verdict="success", score=0.9)
    _seed_trace(factory, run_id="r1", trace_id="b", behavior="close", verdict="refused", score=0.3)

    with session_scope(factory) as s:
        rows = build_seed_ledger(s).near_miss_first
    assert rows[0].seed_id == "close"  # near-miss first
    assert rows[-1].seed_id == "good"  # converging last


def test_empty_ledger_is_clean() -> None:
    factory = _factory()
    with session_scope(factory) as s:
        ledger = build_seed_ledger(s)
    assert ledger.total_seeds == 0
    assert ledger.triage_queue == 0


# ----------------------------------------------------------------------
# Route
# ----------------------------------------------------------------------


def test_ledger_route_renders_and_is_in_nav() -> None:
    import os
    import tempfile

    # A shared file DB so the app and the seeding share state (an
    # in-memory engine would give the app a different connection).
    import tempfile as _t  # noqa: F401

    from fastapi.testclient import TestClient

    from advtest_ext.dashboard import create_app
    from advtest_ext.storage import make_engine as _mk

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        eng = _mk(f"sqlite:///{path}")
        f2 = make_session_factory(eng)
        from advtest_ext.storage import insert_trace
        from tests.test_storage import _domain_trace

        with session_scope(f2) as s:
            upsert_run(s, run_id="r1", target_name="stub", strategy="grid")
            t = _domain_trace("t1")
            t.behavior_id = "demo_seed"
            t.verdict = "success"
            t.judge_scores = [JudgeScore(judge_name="j", score=0.8)]
            insert_trace(s, run_id="r1", trace=t)

        with TestClient(create_app(engine=eng)) as c:
            r = c.get("/ledger")
            assert r.status_code == 200
            assert "demo_seed" in r.text
            # Seed ledger is no longer a headline nav tab, but the route stays live.
    finally:
        os.unlink(path)
