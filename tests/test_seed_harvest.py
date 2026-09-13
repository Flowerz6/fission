"""Tests for harvesting signal-verified winning trajectories into the seed corpus.

The harvest closes the flywheel's back half (a landed crescendo variant becomes a
reusable seed), but it is only useful if it is GATED. The load-bearing property,
exercised here, is the **validity gate**: an oracle-only success (the LLM judge
alone) is NOT harvested, because the oracle over-calls; only a success corroborated
by a deterministic (non-``llm_judge_verdict``) signal is. Also covered: the verdict
gate, trajectory reconstruction along ``parent_id``, provenance/shape, and
idempotent novelty dedup.
"""

from __future__ import annotations

from advtest_ext.core.orchestrator import harvest_verified_seeds
from advtest_ext.storage import (
    insert_signal_records,
    insert_trace,
    list_seeds,
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)
from tests.test_storage import _domain_trace

_RUN = "run-harvest"


def _factory():
    engine = make_engine("sqlite:///:memory:")
    return make_session_factory(engine)


def _seed(factory) -> None:
    """One run with four traces exercising every branch of the gate."""
    with session_scope(factory) as s:
        upsert_run(s, run_id=_RUN, target_name="stub", strategy="grid")
        # verified success: deterministic signal fired -> HARVEST
        insert_trace(s, run_id=_RUN, trace=_domain_trace("t_verified", verdict="success"))
        insert_signal_records(
            s,
            trace_id="t_verified",
            records=[{"name": "canary", "kind": "canary_appears", "fired": True}],
        )
        # oracle-only success: only the LLM judge signal fired -> DO NOT harvest
        insert_trace(s, run_id=_RUN, trace=_domain_trace("t_oracle", verdict="success"))
        insert_signal_records(
            s,
            trace_id="t_oracle",
            records=[{"name": "judge", "kind": "llm_judge_verdict", "fired": True}],
        )
        # refused turn with a deterministic signal fired -> DO NOT harvest (verdict gate)
        insert_trace(s, run_id=_RUN, trace=_domain_trace("t_root", verdict="refused"))
        insert_signal_records(
            s,
            trace_id="t_root",
            records=[{"name": "canary", "kind": "canary_appears", "fired": False}],
        )
        # verified success that descends from t_root -> HARVEST, trajectory = [root, child]
        insert_trace(
            s,
            run_id=_RUN,
            trace=_domain_trace("t_child", verdict="success", parent_id="t_root", depth=1),
        )
        insert_signal_records(
            s,
            trace_id="t_child",
            records=[{"name": "beacon", "kind": "regex_match", "fired": True}],
        )


def test_only_signal_verified_successes_are_harvested() -> None:
    factory = _factory()
    _seed(factory)

    count = harvest_verified_seeds(factory, _RUN)
    assert count == 2  # t_verified + t_child; NOT t_oracle, NOT t_root

    with session_scope(factory) as s:
        seeds = list_seeds(s, kind="harvested")
    assert len(seeds) == 2
    trace_ids = {sd.payload["declared_metadata"]["trace_id"] for sd in seeds}
    assert trace_ids == {"t_verified", "t_child"}
    # the over-called oracle success is absent
    assert "t_oracle" not in trace_ids


def test_harvested_seed_shape_and_provenance() -> None:
    factory = _factory()
    _seed(factory)
    harvest_verified_seeds(factory, _RUN)

    with session_scope(factory) as s:
        seeds = {
            sd.payload["declared_metadata"]["trace_id"]: sd
            for sd in list_seeds(s, kind="harvested")
        }

    verified = seeds["t_verified"]
    assert verified.kind == "harvested"
    assert verified.source_format == "tap_harvest"
    assert verified.submitted_by == f"harvest:tap:{_RUN}"
    meta = verified.payload["declared_metadata"]
    assert meta["status"] == "candidate"  # revalidate before use
    assert meta["verified_signals"] == ["canary_appears:canary"]
    assert verified.payload["extracted_turns"]  # trajectory captured


def test_trajectory_follows_parent_chain() -> None:
    factory = _factory()
    _seed(factory)
    harvest_verified_seeds(factory, _RUN)

    with session_scope(factory) as s:
        child = next(
            sd
            for sd in list_seeds(s, kind="harvested")
            if sd.payload["declared_metadata"]["trace_id"] == "t_child"
        )
    # the winning child's trajectory includes the earlier (refused) ramp turn
    assert len(child.payload["extracted_turns"]) == 2


def test_harvest_is_idempotent() -> None:
    factory = _factory()
    _seed(factory)

    first = harvest_verified_seeds(factory, _RUN)
    second = harvest_verified_seeds(factory, _RUN)
    assert first == 2
    assert second == 0  # novelty gate: same trajectories, no new rows

    with session_scope(factory) as s:
        assert len(list_seeds(s, kind="harvested")) == 2
