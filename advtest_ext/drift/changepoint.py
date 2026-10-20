"""Sequential change-point detection + the Q4 undecayed-prior arithmetic.

**Detector choice — CUSUM over BOCPD.** The signal-verified series is a stream of
Bernoulli outcomes and the operational question is "did the success rate shift
away from a known baseline, and when." A two-sided CUSUM on that series is online,
O(1) memory, and — critically for :mod:`advtest_ext.drift.calibrate` — has a
single threshold ``h`` whose false-alarm rate is directly tunable to a target
rate. BOCPD gives a full run-length posterior but needs a hazard prior and a
conjugate model per cell, and its alarm rule is harder to calibrate to a flat
false-drift budget. We take the simpler, directly-calibratable detector.

**The Q4 trap.** When a new ``target_profile`` carried a prior forward
(``seed_from_previous_version``), the cell's CURRENT ``alpha``/``beta`` is the
*decayed* prior — same mean as the old belief but an inflated variance (lower
ESS). Comparing fresh evidence against that decayed prior UNDER-reports drift,
because the decayed prior's wide credible interval swallows a genuine shift. Drift
must be measured against the persisted UNDECAYED ``prev_alpha``/``prev_beta`` from
Part A, whose tight interval reveals the shift.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MORE_VULNERABLE = "more_vulnerable"
LESS_VULNERABLE = "less_vulnerable"
NO_DIRECTION = "none"


@dataclass(frozen=True)
class ChangePoint:
    """The result of a CUSUM scan over a binary success series."""

    detected: bool
    index: int | None  # first alarm index (≈ the change cycle)
    direction: str  # more_vulnerable | less_vulnerable | none
    statistic: float  # the CUSUM value at alarm (or max reached)


def cusum(
    series: Sequence[bool],
    *,
    baseline_rate: float,
    threshold: float,
    slack: float = 0.05,
) -> ChangePoint:
    """Two-sided CUSUM for a shift in a Bernoulli success rate.

    ``baseline_rate`` is the pre-change mean (e.g. the undecayed prior mean or the
    first stable window). ``threshold`` (``h``) is the alarm level;
    :mod:`advtest_ext.drift.calibrate` tunes it to a false-alarm budget. ``slack``
    (``k``) is the allowance that keeps small noise from accumulating.
    """
    s_up = 0.0
    s_down = 0.0
    peak = 0.0
    for i, outcome in enumerate(series):
        x = 1.0 if outcome else 0.0
        s_up = max(0.0, s_up + (x - (baseline_rate + slack)))
        s_down = max(0.0, s_down + ((baseline_rate - slack) - x))
        peak = max(peak, s_up, s_down)
        if s_up > threshold:
            return ChangePoint(detected=True, index=i, direction=MORE_VULNERABLE, statistic=s_up)
        if s_down > threshold:
            return ChangePoint(detected=True, index=i, direction=LESS_VULNERABLE, statistic=s_down)
    return ChangePoint(detected=False, index=None, direction=NO_DIRECTION, statistic=peak)


@dataclass(frozen=True)
class PosteriorDrift:
    """Divergence of fresh evidence from the UNDECAYED prior belief."""

    magnitude: float  # |mean_new - mean_prior|
    z: float  # standardized by the PRIOR's std — this is what the decayed prior deflates
    direction: str
    mean_new: float
    mean_prior: float


def _beta_mean_var(alpha: float, beta: float) -> tuple[float, float]:
    total = alpha + beta
    mean = alpha / total
    var = (alpha * beta) / (total * total * (total + 1.0))
    return mean, var


def posterior_drift(
    *,
    new_alpha: float,
    new_beta: float,
    prior_alpha: float,
    prior_beta: float,
) -> PosteriorDrift:
    """Score fresh evidence ``Beta(new)`` against a prior ``Beta(prior)``.

    ``z`` standardizes the mean shift by the PRIOR's standard deviation, so a
    tight (undecayed) prior yields a large ``z`` for a real shift while a wide
    (decayed) prior deflates it — the Q4 under-reporting the caller must avoid by
    passing the UNDECAYED prev. See :func:`posterior_drift_for_cell`.
    """
    mean_new, _ = _beta_mean_var(new_alpha, new_beta)
    mean_prior, var_prior = _beta_mean_var(prior_alpha, prior_beta)
    std_prior = math.sqrt(var_prior) if var_prior > 0 else 0.0
    magnitude = abs(mean_new - mean_prior)
    z = magnitude / std_prior if std_prior > 0 else 0.0
    if mean_new > mean_prior:
        direction = MORE_VULNERABLE
    elif mean_new < mean_prior:
        direction = LESS_VULNERABLE
    else:
        direction = NO_DIRECTION
    return PosteriorDrift(
        magnitude=magnitude, z=z, direction=direction, mean_new=mean_new, mean_prior=mean_prior
    )


def posterior_drift_for_cell(cell: Any, *, new_alpha: float, new_beta: float) -> PosteriorDrift:
    """Drift of fresh evidence against a cell's UNDECAYED carried-forward prior.

    ``cell`` must expose ``prev_alpha``/``prev_beta`` (Part A's persisted undecayed
    belief). Using the cell's live ``alpha``/``beta`` (the decayed seed) here would
    under-report — this function deliberately reads only the undecayed fields.
    """
    prev_alpha = getattr(cell, "prev_alpha", None)
    prev_beta = getattr(cell, "prev_beta", None)
    if prev_alpha is None or prev_beta is None:
        raise ValueError(
            "cell carries no undecayed prev_alpha/prev_beta — no prior to drift against"
        )
    return posterior_drift(
        new_alpha=new_alpha, new_beta=new_beta, prior_alpha=prev_alpha, prior_beta=prev_beta
    )


__all__ = [
    "LESS_VULNERABLE",
    "MORE_VULNERABLE",
    "NO_DIRECTION",
    "ChangePoint",
    "PosteriorDrift",
    "cusum",
    "posterior_drift",
    "posterior_drift_for_cell",
]
