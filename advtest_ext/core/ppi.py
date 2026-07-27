"""Prediction-powered inference (PPI) for the run-level ASR
(``docs/FISSION_MEASUREMENT_SPINE.md`` §4).

The oracle labels the *whole* run cheaply but with bias; experts label a
small *uniform* random slice, unbiased but with a wide CI on its own.
PPI combines them: take the oracle's ASR over the full run, then correct
it by the oracle-vs-expert disagreement measured on the labeled slice,
and produce a CI that accounts for both the sample size and the oracle's
reliability. The output is the headline a reviewer can trust —
``ASR = 70% ± 3% (95% CI)`` — not "the oracle said 70%".

Estimator (Angelopoulos et al. 2023, the mean case). With ``f`` the
oracle success-indicator and ``y`` the expert success-indicator:

    theta_hat = mean_full(f)  -  mean_labeled(f - y)
    Var       = Var_full(f)/N  +  Var_labeled(f - y)/n

The labeled slice MUST be a simple random sample of the run for the
rectifier to be unbiased — i.e. the sampler's ``uniform`` slice, NOT the
disagreement slice (which is deliberately skewed toward hard cases).
The caller is responsible for passing only uniform-slice pairs.

Pure and dependency-free (stdlib ``statistics`` only) so the estimator
is unit-testable in isolation; ``ppi_asr_for_run`` is the thin DB glue.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

__all__ = ["PpiEstimate", "ppi_asr", "ppi_asr_for_run"]


def _is_success(verdict: str) -> int:
    """ASR success indicator: 1 for ``success``, 0 otherwise (``partial``
    and ``refused`` both count as not-success, matching the orchestrator's
    ASR convention)."""
    return 1 if verdict == "success" else 0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass(frozen=True)
class PpiEstimate:
    """A run's ASR with a calibrated confidence interval.

    ``method`` is ``ppi`` when a uniform labeled slice rectified the
    estimate, ``oracle_only`` when no labels exist yet (the CI is then
    just the oracle proportion's own sampling interval — honest but
    *uncalibrated*, so the UI should say "collect labels"), or ``empty``
    for a run with no scorable traces.
    """

    asr: float
    ci_low: float
    ci_high: float
    n_total: int
    n_labeled: int
    oracle_asr: float
    rectifier: float
    method: str
    confidence: float = 0.95

    @property
    def ci_half_width(self) -> float:
        return (self.ci_high - self.ci_low) / 2.0


def _z_for(confidence: float) -> float:
    """Two-sided normal quantile (1.96 at 95%)."""
    return statistics.NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


def ppi_asr(
    *,
    n_total: int,
    n_oracle_success: int,
    labeled_pairs: Sequence[tuple[str, str]],
    confidence: float = 0.95,
) -> PpiEstimate:
    """Compute the PPI ASR estimate.

    ``n_total`` / ``n_oracle_success`` describe the oracle's labels over
    the whole run (excluding ``error`` traces). ``labeled_pairs`` are
    ``(oracle_verdict, expert_verdict)`` for the **uniform** slice only.
    """
    if n_total <= 0:
        return PpiEstimate(0.0, 0.0, 0.0, 0, 0, 0.0, 0.0, "empty", confidence)

    oracle_asr = n_oracle_success / n_total
    z = _z_for(confidence)

    if not labeled_pairs:
        # No calibration labels: fall back to the oracle proportion's own
        # normal-approx CI. Honest but NOT rectified for oracle error.
        se = (oracle_asr * (1.0 - oracle_asr) / n_total) ** 0.5
        return PpiEstimate(
            asr=oracle_asr,
            ci_low=_clamp01(oracle_asr - z * se),
            ci_high=_clamp01(oracle_asr + z * se),
            n_total=n_total,
            n_labeled=0,
            oracle_asr=oracle_asr,
            rectifier=0.0,
            method="oracle_only",
            confidence=confidence,
        )

    diffs = [_is_success(o) - _is_success(y) for (o, y) in labeled_pairs]
    n = len(diffs)
    rectifier = statistics.fmean(diffs)
    theta = _clamp01(oracle_asr - rectifier)

    var_f = oracle_asr * (1.0 - oracle_asr) / n_total
    var_d = (statistics.variance(diffs) / n) if n >= 2 else 0.0
    se = (var_f + var_d) ** 0.5

    return PpiEstimate(
        asr=theta,
        ci_low=_clamp01(theta - z * se),
        ci_high=_clamp01(theta + z * se),
        n_total=n_total,
        n_labeled=n,
        oracle_asr=oracle_asr,
        rectifier=rectifier,
        method="ppi",
        confidence=confidence,
    )


def ppi_asr_for_run(
    session: Session,
    run_id: str,
    *,
    confidence: float = 0.95,
) -> PpiEstimate:
    """DB glue: assemble the oracle counts + uniform-slice labels for one
    run and compute its PPI ASR. Lazy-imports storage to keep ``core``
    free of a hard storage dependency at module load."""
    from advtest_ext.storage.repository import (
        gold_label_oracle_expert_pairs,
        run_oracle_asr_counts,
    )

    n_total, n_success = run_oracle_asr_counts(session, run_id)
    pairs = gold_label_oracle_expert_pairs(session, run_id, sample_reason="uniform")
    return ppi_asr(
        n_total=n_total,
        n_oracle_success=n_success,
        labeled_pairs=pairs,
        confidence=confidence,
    )
