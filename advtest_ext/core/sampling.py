"""Stratified review sampling — which traces go to a human
(``docs/FISSION_MEASUREMENT_SPINE.md`` §3).

The oracle labels the *whole* run cheaply; experts only label a
*sample*. That sample has two slices and PPI needs both:

* **uniform** — a simple random sample of the whole run. This is the
  load-bearing slice: it is what makes the ASR estimate an *unbiased*
  population claim, and the only thing that surfaces the oracle's
  confident-but-wrong blind spots. Drawn FIRST, from all traces.
* **disagreement** — the highest-uncertainty traces not already drawn
  uniformly (top-K by ``core.uncertainty`` score). Concentrates scarce
  expert time where the oracle is least sure.

Drawing uniform first and disagreement from the remainder keeps the
uniform slice a genuine simple random sample (a high-uncertainty trace
that happens to be drawn uniformly stays labelled ``uniform`` so it
still counts toward the unbiased estimator). The uniform:disagreement
split is a tunable that starts high on uniform at cold-start and decays
as a trained L1 gives uncertainty sampling real teeth.

Deterministic given a ``seed`` so a run's review queue is reproducible.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["SamplePlan", "SampledTrace", "stratified_sample"]


@dataclass(frozen=True)
class SampledTrace:
    trace_id: str
    uncertainty: float
    sample_reason: str  # "uniform" | "disagreement"


@dataclass(frozen=True)
class SamplePlan:
    uniform: list[SampledTrace]
    disagreement: list[SampledTrace]

    @property
    def all(self) -> list[SampledTrace]:
        """Every selected trace, uniform slice first."""
        return [*self.uniform, *self.disagreement]


def stratified_sample(
    traces: Sequence[tuple[str, float]],
    *,
    budget: int,
    uniform_fraction: float = 0.6,
    min_uncertainty: float = 0.0,
    seed: int = 0,
) -> SamplePlan:
    """Pick up to ``budget`` traces to enqueue for review.

    ``traces`` is ``(trace_id, uncertainty)`` for the whole run.
    ``uniform_fraction`` is the share of the budget spent on the
    unbiased uniform slice (default 0.6 — uniform-heavy for cold-start).
    ``min_uncertainty`` floors the disagreement slice so genuinely
    agreeing traces (uncertainty ~0) are never enqueued as
    "disagreement" once the uniform quota is met.

    Returns disjoint uniform / disagreement slices; a trace never
    appears in both. Deterministic given ``seed``.
    """
    if budget <= 0 or not traces:
        return SamplePlan(uniform=[], disagreement=[])

    unc_by_id = {tid: u for tid, u in traces}
    all_ids = list(unc_by_id)
    budget = min(budget, len(all_ids))

    n_uniform = min(len(all_ids), round(budget * uniform_fraction))
    rng = random.Random(seed)
    uniform_ids = set(rng.sample(all_ids, n_uniform)) if n_uniform else set()

    uniform = [
        SampledTrace(trace_id=tid, uncertainty=unc_by_id[tid], sample_reason="uniform")
        for tid in all_ids
        if tid in uniform_ids
    ]

    # Disagreement slice: top-K by uncertainty among the not-yet-sampled,
    # above the floor. Fills whatever the uniform slice left of the budget.
    n_disagreement = budget - len(uniform)
    remaining = [
        (tid, unc_by_id[tid])
        for tid in all_ids
        if tid not in uniform_ids and unc_by_id[tid] > min_uncertainty
    ]
    # Sort by uncertainty desc, tie-break by id for determinism.
    remaining.sort(key=lambda t: (-t[1], t[0]))
    disagreement = [
        SampledTrace(trace_id=tid, uncertainty=u, sample_reason="disagreement")
        for tid, u in remaining[:n_disagreement]
    ]

    return SamplePlan(uniform=uniform, disagreement=disagreement)
