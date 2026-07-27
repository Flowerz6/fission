"""Integration test: the measurement loop closes end-to-end.

Drives the real pipeline (orchestrator -> uncertainty -> stratified
sampler -> HITL enqueue -> GoldLabel -> PPI) via ``scripts/spine_e2e``
and asserts a valid, calibrated ASR ± CI comes out. Deterministic in its
*structure* (fixed trace/label/slice counts); the exact ASR is not
asserted because it depends on which traces the random uniform slice
draws — which is a real property of sampling, not a bug.
"""

from __future__ import annotations

from scripts.spine_e2e import drive_spine

from advtest_ext.storage import make_engine, make_session_factory


async def test_measurement_loop_closes() -> None:
    factory = make_session_factory(make_engine("sqlite:///:memory:"))
    r = await drive_spine(factory, n_per_behavior=9, review_budget=12, seed=0)

    # 1. RUN — 3 behaviours x 9 attempts, all scorable (no errors).
    assert r["n_traces"] == 27
    assert 0 < r["oracle_success"] < r["n_traces"]

    # 2-3. SAMPLE + REVIEW — the whole budget was enqueued across BOTH
    # slices (counts are fraction-driven, so stable) and every queued item
    # became a gold label.
    assert r["labelled"] == 12
    assert r["n_uniform"] == 7 and r["n_disagreement"] == 5  # round(12*0.55) split
    assert r["n_uniform"] + r["n_disagreement"] == r["labelled"]

    # 4. PPI — a calibrated estimate with a valid interval, rectified on
    # the uniform slice only.
    ppi = r["ppi"]
    assert ppi.method == "ppi"
    assert 0.0 <= ppi.ci_low <= ppi.asr <= ppi.ci_high <= 1.0
    assert ppi.n_labeled == r["n_uniform"]  # PPI uses the uniform slice only

    # 5. CALIBRATION — the report computes and the decomposed oracle beats
    # the baseline on both F1 and ECE (deterministic simulated preds).
    b, d = r["calib"]["ipi_verdict"], r["calib"]["decomposed_ipi"]
    assert d.overall.f1 > b.overall.f1
    assert d.ece < b.ece


async def test_loop_structure_is_stable_across_seeds() -> None:
    """The pipeline's SHAPE (trace/label/slice counts, and that PPI yields
    a calibrated method) is invariant to the seed, even though the exact
    ASR is not."""
    f1 = make_session_factory(make_engine("sqlite:///:memory:"))
    f2 = make_session_factory(make_engine("sqlite:///:memory:"))
    a = await drive_spine(f1, seed=1)
    b = await drive_spine(f2, seed=999)
    for key in ("n_traces", "labelled", "n_uniform", "n_disagreement"):
        assert a[key] == b[key]
    assert a["ppi"].method == b["ppi"].method == "ppi"
