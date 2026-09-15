"""Seed-reliability ledger: execution-only validity + the discard rule.

Covers the load-bearing property that a bot-blocked or harness-errored session is
*inconclusive* and never counts as an attack failure, so a seed Google keeps
blocking is not wrongly discarded; and that N genuine attack failures with no
success do retire the seed.
"""

from __future__ import annotations

from advtest_ext.core.reliability import (
    classify_session,
    recompute_seed_reliability,
    set_seed_status,
    summarize_seed_reliability,
)
from advtest_ext.storage import (
    SeedReliability,
    insert_trace,
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)
from tests.test_storage import _domain_trace

_GUIDE = "sheets_skill_leak"


def _factory():
    return make_session_factory(make_engine("sqlite:///:memory:"))


def _session(factory, run_id: str, *, verdict: str, error_kind: str | None = None) -> None:
    """Persist one run whose single root trace has the given (verdict, error_kind)."""
    with session_scope(factory) as s:
        upsert_run(s, run_id=run_id, target_name="stub", strategy="tap_crescendo")
        trace = _domain_trace(f"{run_id}-root", _GUIDE, verdict=verdict)
        if error_kind is not None:
            trace.metadata = {"error_kind": error_kind}
        # insert_trace uses the trace's own run_id; align it with this run.
        trace.run_id = run_id
        insert_trace(s, run_id=run_id, trace=trace)


# --- classify_session ---------------------------------------------------------


def test_classify_session_maps_verdicts() -> None:
    assert classify_session("success", None) == "success"
    assert classify_session("refused", None) == "attack_failed"
    assert classify_session("partial", None) == "attack_failed"
    assert classify_session("error", "bot_detected") == "bot_detected"
    assert classify_session("error", "selector_drift") == "error"
    assert classify_session("error", None) == "error"
    assert classify_session(None, None) == "error"  # un-adjudicated is inconclusive


# --- discard rule -------------------------------------------------------------


def test_bot_detections_never_retire_a_seed() -> None:
    factory = _factory()
    for i in range(15):  # well past the default N=10
        _session(factory, f"run-bot-{i}", verdict="error", error_kind="bot_detected")
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE)
    assert summary.n_bot_detected == 15
    assert summary.n_attack_failed == 0
    assert summary.status == "active"  # inconclusive, not failed


def test_selector_drift_is_inconclusive_too() -> None:
    factory = _factory()
    for i in range(12):
        _session(factory, f"run-drift-{i}", verdict="error", error_kind="selector_drift")
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE)
    assert summary.n_error == 12
    assert summary.n_attack_failed == 0
    assert summary.status == "active"


def test_n_attack_failures_with_no_success_retires() -> None:
    factory = _factory()
    for i in range(10):
        _session(factory, f"run-fail-{i}", verdict="refused")
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE)
    assert summary.n_attack_failed == 10
    assert summary.status == "retired"


def test_one_success_keeps_a_seed_alive_forever() -> None:
    factory = _factory()
    _session(factory, "run-win", verdict="success")
    for i in range(20):
        _session(factory, f"run-fail-{i}", verdict="refused")
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE)
    assert summary.n_success == 1
    assert summary.n_attack_failed == 20
    assert summary.status == "active"  # discard requires zero successes


def test_below_threshold_stays_active() -> None:
    factory = _factory()
    for i in range(9):  # one short of N=10
        _session(factory, f"run-fail-{i}", verdict="refused")
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE)
    assert summary.status == "active"


def test_custom_discard_n() -> None:
    factory = _factory()
    for i in range(3):
        _session(factory, f"run-fail-{i}", verdict="refused")
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE, discard_n=3)
    assert summary.status == "retired"


# --- per-run reduction --------------------------------------------------------


def test_a_run_with_any_success_root_counts_as_a_success_session() -> None:
    """Several root traces in one run reduce to the best outcome."""
    factory = _factory()
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-multi", target_name="stub", strategy="tap_crescendo")
        for tid, verdict in (("a", "refused"), ("b", "success"), ("c", "error")):
            t = _domain_trace(f"run-multi-{tid}", _GUIDE, verdict=verdict)
            t.run_id = "run-multi"
            insert_trace(s, run_id="run-multi", trace=t)
        summary = summarize_seed_reliability(s, _GUIDE)
    assert summary.n_sessions == 1
    assert summary.n_success == 1


def test_mutated_branches_do_not_count_as_seed_sessions() -> None:
    """Only root traces (parent_id is None) are the seed replay; branches are ignored."""
    factory = _factory()
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-branch", target_name="stub", strategy="tap_crescendo")
        root = _domain_trace("run-branch-root", _GUIDE, verdict="refused")
        root.run_id = "run-branch"
        insert_trace(s, run_id="run-branch", trace=root)
        child = _domain_trace(
            "run-branch-child", _GUIDE, verdict="success", parent_id="run-branch-root", depth=1
        )
        child.run_id = "run-branch"
        insert_trace(s, run_id="run-branch", trace=child)
        summary = summarize_seed_reliability(s, _GUIDE)
    # The seed (root) refused; the winning child is a mutation, not the seed.
    assert summary.n_sessions == 1
    assert summary.n_attack_failed == 1
    assert summary.n_success == 0


# --- persistence + manual override -------------------------------------------


def test_recompute_is_idempotent_and_persists_one_row() -> None:
    factory = _factory()
    _session(factory, "run-1", verdict="refused")
    with session_scope(factory) as s:
        recompute_seed_reliability(s, _GUIDE)
    with session_scope(factory) as s:
        recompute_seed_reliability(s, _GUIDE, run_id="run-1")
        rows = s.query(SeedReliability).all()
        assert len(rows) == 1
        assert rows[0].last_run_id == "run-1"
        assert rows[0].n_attack_failed == 1


def test_manual_override_survives_recompute() -> None:
    factory = _factory()
    for i in range(10):
        _session(factory, f"run-fail-{i}", verdict="refused")
    with session_scope(factory) as s:
        recompute_seed_reliability(s, _GUIDE)  # -> retired
        set_seed_status(s, _GUIDE, "active")  # operator says: it was bot-blocked
    with session_scope(factory) as s:
        summary = recompute_seed_reliability(s, _GUIDE)
        assert summary.status == "active"  # override respected
        row = s.get(SeedReliability, _GUIDE)
        assert row is not None and row.manual_override is True
