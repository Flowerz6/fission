"""Tests for ``core.sampling`` — the measurement spine's stratified
review sampler.

The statistical property that matters: the uniform slice is a genuine
simple random sample of the *whole* run (drawn first, from all traces),
and the two slices are disjoint. Determinism under a fixed seed is
required so a run's review queue is reproducible.
"""

from __future__ import annotations

from advtest_ext.core.sampling import stratified_sample


def _traces(n: int) -> list[tuple[str, float]]:
    # uncertainty increases with index so top-K is predictable.
    return [(f"t{i}", i / n) for i in range(n)]


def test_empty_or_zero_budget_selects_nothing() -> None:
    assert stratified_sample([], budget=5).all == []
    assert stratified_sample(_traces(10), budget=0).all == []


def test_slices_are_disjoint_and_within_budget() -> None:
    plan = stratified_sample(_traces(100), budget=20, uniform_fraction=0.5, seed=1)
    ids = [s.trace_id for s in plan.all]
    assert len(ids) == len(set(ids)), "no trace appears in both slices"
    assert len(plan.all) == 20
    assert len(plan.uniform) == 10
    assert len(plan.disagreement) == 10


def test_disagreement_slice_takes_highest_uncertainty() -> None:
    # All-uniform=0 so the whole budget is the disagreement slice: it
    # must be the top-K by uncertainty.
    plan = stratified_sample(_traces(50), budget=5, uniform_fraction=0.0)
    picked = {s.trace_id for s in plan.disagreement}
    assert picked == {"t49", "t48", "t47", "t46", "t45"}
    assert all(s.sample_reason == "disagreement" for s in plan.disagreement)


def test_uniform_fraction_splits_the_budget() -> None:
    plan = stratified_sample(_traces(100), budget=10, uniform_fraction=0.6, seed=0)
    assert len(plan.uniform) == 6
    assert len(plan.disagreement) == 4
    assert all(s.sample_reason == "uniform" for s in plan.uniform)


def test_min_uncertainty_floors_the_disagreement_slice() -> None:
    # Half the traces have uncertainty 0. With a floor > 0 and no uniform
    # quota, only the non-zero ones are eligible for disagreement.
    traces = [(f"z{i}", 0.0) for i in range(10)] + [(f"h{i}", 0.9) for i in range(3)]
    plan = stratified_sample(traces, budget=10, uniform_fraction=0.0, min_uncertainty=0.1)
    assert len(plan.disagreement) == 3
    assert all(s.trace_id.startswith("h") for s in plan.disagreement)


def test_deterministic_under_seed() -> None:
    a = stratified_sample(_traces(100), budget=15, seed=42)
    b = stratified_sample(_traces(100), budget=15, seed=42)
    assert [s.trace_id for s in a.all] == [s.trace_id for s in b.all]
    # A different seed shuffles the uniform slice (overwhelmingly likely).
    c = stratified_sample(_traces(100), budget=15, seed=7)
    assert {s.trace_id for s in a.uniform} != {s.trace_id for s in c.uniform}


def test_budget_larger_than_population_takes_all_above_floor() -> None:
    # All uncertainties strictly positive → nothing is floored out, so an
    # over-budget request selects the entire population.
    traces = [(f"t{i}", (i + 1) / 5) for i in range(5)]
    plan = stratified_sample(traces, budget=99, uniform_fraction=0.5)
    assert {s.trace_id for s in plan.all} == {f"t{i}" for i in range(5)}


def test_zero_uncertainty_non_uniform_trace_is_left_out() -> None:
    """Deliberate: a trace the oracle is certain about (uncertainty 0)
    is never enqueued as 'disagreement'. If it isn't drawn into the
    uniform slice it is simply not reviewed — even with spare budget —
    because there is no disagreement signal to justify an expert's time."""
    traces = [("certain", 0.0), ("hot", 0.9)]
    plan = stratified_sample(traces, budget=10, uniform_fraction=0.0)
    assert [s.trace_id for s in plan.all] == ["hot"]


def test_uniform_slice_can_include_high_uncertainty_trace() -> None:
    """A high-uncertainty trace drawn into the uniform slice stays
    labelled 'uniform' (it must still count toward the unbiased
    estimator) and is NOT double-counted in disagreement."""
    plan = stratified_sample(_traces(20), budget=20, uniform_fraction=1.0, seed=3)
    assert len(plan.uniform) == 20
    assert plan.disagreement == []
    assert all(s.sample_reason == "uniform" for s in plan.all)
