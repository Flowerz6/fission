"""Tests for harvesting signal-verified winning trajectories as guide VARIANTS.

A proven win is banked as a variant parented to its source guide (not a seed, not
a new top-level guide). The load-bearing property is the validity gate: an
oracle-only success (LLM judge alone) is NOT harvested, because the oracle
over-calls; only a success corroborated by a deterministic signal is. Also
covered: parentage, trajectory reconstruction, and idempotent dedup.
"""

from __future__ import annotations

from advtest_ext.core.orchestrator import harvest_verified_variants
from advtest_ext.storage import (
    insert_signal_records,
    insert_trace,
    list_guide_variants,
    make_engine,
    make_session_factory,
    session_scope,
    upsert_run,
)
from tests.test_storage import _domain_trace

_RUN = "run-harvest"
_GUIDE = "sheets_skill_leak"


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
        # verified success descending from t_root -> HARVEST, trajectory = [root, child]
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

    count = harvest_verified_variants(factory, _GUIDE, _RUN)
    assert count == 2  # t_verified + t_child; NOT t_oracle, NOT t_root

    with session_scope(factory) as s:
        variants = list_guide_variants(s, guide_id=_GUIDE)
    assert len(variants) == 2
    trace_ids = {v.provenance["trace_id"] for v in variants}
    assert trace_ids == {"t_verified", "t_child"}
    assert "t_oracle" not in trace_ids  # the over-called oracle success is absent


def test_variant_is_parented_and_shaped() -> None:
    factory = _factory()
    _seed(factory)
    harvest_verified_variants(factory, _GUIDE, _RUN)

    with session_scope(factory) as s:
        variants = {v.provenance["trace_id"]: v for v in list_guide_variants(s, guide_id=_GUIDE)}

    v = variants["t_verified"]
    assert v.guide_id == _GUIDE  # parented to its source guide
    assert v.status == "candidate"  # revalidate before it's canonical
    assert v.seed_chain  # winning trajectory captured as the replayable seed chain
    assert v.provenance["verified_signals"] == ["canary_appears:canary"]


def test_trajectory_follows_parent_chain() -> None:
    factory = _factory()
    _seed(factory)
    harvest_verified_variants(factory, _GUIDE, _RUN)

    with session_scope(factory) as s:
        child = next(
            v
            for v in list_guide_variants(s, guide_id=_GUIDE)
            if v.provenance["trace_id"] == "t_child"
        )
    assert len(child.seed_chain) == 2  # includes the earlier (refused) ramp turn


def test_harvest_is_idempotent() -> None:
    factory = _factory()
    _seed(factory)

    first = harvest_verified_variants(factory, _GUIDE, _RUN)
    second = harvest_verified_variants(factory, _GUIDE, _RUN)
    assert first == 2
    assert second == 0  # novelty gate: same trajectories, no new rows

    with session_scope(factory) as s:
        assert len(list_guide_variants(s, guide_id=_GUIDE)) == 2
