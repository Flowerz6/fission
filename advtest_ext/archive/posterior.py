"""Per-cell Beta(alpha, beta) posterior over a strategy region's success rate.

**Why hand-rolled (not SciPy).** The repo keeps its measurement stats
dependency-light — ``core/ppi.py`` computes its ASR CI with stdlib ``statistics``
and a hard-coded normal quantile rather than importing SciPy/NumPy (neither is a
declared dependency in ``pyproject.toml``). We follow that convention: the
conjugate Beta update is a one-line pseudo-count bump, and the credible interval
is a normal approximation on the *exact* Beta variance, clamped to ``[0, 1]`` —
honest at the sample sizes MAP-Elites cells reach, and consistent with the PPI
metric it sits beside. An exact Beta quantile would need SciPy's ``betaincinv``;
the structure here (mean + variance) drops that in later without an API change.

Design rules baked in (see ``docs/INTEGRATION_CONTRACT.md`` + Part-A spec):

* **Q6 — one deterministic success channel.** Only a *signal-verified* success
  moves ``alpha``. A judge-only success MUST NOT touch ``alpha``/``beta`` — it is
  routed elsewhere (``judge_only_signal``) by :func:`apply_observation`.
* **Q5 — resolution weighting.** ``native`` observations update at weight 1.0;
  ``backfilled`` ones at a discounted weight (default 0.25), because a
  history-reconstructed descriptor vector is a weaker measurement.
* **Q4 — carry-forward.** :meth:`BetaPosterior.seed_from_previous_version` seeds a
  new target-profile cell from the previous profile's posterior with its
  effective sample size scaled by ``decay`` (so fresh evidence can overturn it),
  AND stores the *undecayed* previous belief (``prev_posterior_ref``) — the drift
  part compares new evidence against that undecayed belief, not the decayed prior.
* **Independence.** Turns within a crescendo are not independent trials; only the
  terminal node of a root-to-leaf path is observed (enforced by the caller, see
  ``backfill``/live wiring), so each conversation contributes one update per cell.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from advtest_ext.core.orch_trace_meta import RunOutcome

Resolution = Literal["native", "medium", "coarse"]
"""Provenance/quality of an observation. ``native`` = computed forward at run
time (full weight). ``medium`` / ``coarse`` = history-reconstructed at partial
descriptor recovery (discounted)."""

NATIVE_WEIGHT = 1.0
BACKFILL_WEIGHT_DEFAULT = 0.25

# Beta(1, 1) — the uniform prior. A cell with no evidence reads mean 0.5 with a
# wide interval, which is the correct "we don't know" default.
_PRIOR_ALPHA = 1.0
_PRIOR_BETA = 1.0

_Z_95 = 1.959963984540054  # two-sided 95% normal quantile (matches core/ppi.py)


def _weight_for(resolution: Resolution) -> float:
    return NATIVE_WEIGHT if resolution == "native" else BACKFILL_WEIGHT_DEFAULT


@dataclass
class CredibleInterval:
    low: float
    high: float
    level: float = 0.95


@dataclass
class BetaPosterior:
    """A conjugate Beta belief with pseudo-counts ``alpha`` / ``beta``.

    ``prev_posterior_ref`` holds the *undecayed* ``(alpha, beta)`` this cell was
    seeded from on a target-version change (``None`` if never seeded) — kept for
    the drift comparison, never folded back into ``alpha``/``beta``.
    """

    alpha: float = _PRIOR_ALPHA
    beta: float = _PRIOR_BETA
    prev_posterior_ref: tuple[float, float] | None = None
    # Bookkeeping (not part of the belief maths): observation counts by kind.
    n_success: float = 0.0
    n_failure: float = 0.0

    # -- updates -----------------------------------------------------------
    def update(
        self,
        *,
        success_signal_verified: bool,
        confidence: float = 1.0,
        resolution: Resolution = "native",
    ) -> None:
        """Apply one Bernoulli observation.

        ``success_signal_verified=True`` bumps ``alpha`` (a deterministic win);
        ``False`` bumps ``beta`` (a valid refusal). The bump is
        ``weight(resolution) * clamp(confidence)``. Callers must NOT invoke this
        for a judge-only success — use :func:`apply_observation`, which enforces
        the single-success-channel rule.
        """
        weight = _weight_for(resolution) * _clamp01(confidence)
        if weight <= 0.0:
            return
        if success_signal_verified:
            self.alpha += weight
            self.n_success += weight
        else:
            self.beta += weight
            self.n_failure += weight

    # -- summaries ---------------------------------------------------------
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    def variance(self) -> float:
        a, b = self.alpha, self.beta
        s = a + b
        return (a * b) / (s * s * (s + 1.0))

    def effective_sample_size(self) -> float:
        """Total Beta concentration ``alpha + beta`` — the pseudo-count mass.

        Includes the prior (a fresh cell has ESS 2.0). Carry-forward seeding
        scales this quantity by ``decay``.
        """
        return self.alpha + self.beta

    def credible_interval(self, level: float = 0.95) -> CredibleInterval:
        """Normal-approximation credible interval on the exact Beta variance.

        Clamped to ``[0, 1]``. See the module docstring for why this over an
        exact Beta quantile.
        """
        z = _Z_95 if abs(level - 0.95) < 1e-9 else _normal_quantile(level)
        m = self.mean()
        half = z * math.sqrt(self.variance())
        return CredibleInterval(low=_clamp01(m - half), high=_clamp01(m + half), level=level)

    # -- carry-forward (Q4) ------------------------------------------------
    @classmethod
    def seed_from_previous_version(
        cls, previous: BetaPosterior, *, decay: float = 0.4
    ) -> BetaPosterior:
        """Seed a new target-profile cell from ``previous`` with ESS ``x decay``.

        The mean is preserved; the concentration (ESS) is scaled down by
        ``decay`` so a handful of fresh weight-1 observations can overturn the
        carried belief. The *undecayed* previous ``(alpha, beta)`` is stored on
        the result as ``prev_posterior_ref`` for the drift comparison.
        """
        if not 0.0 < decay <= 1.0:
            raise ValueError(f"decay must be in (0, 1]; got {decay}")
        a, b = previous.alpha, previous.beta
        concentration = a + b
        mean = a / concentration if concentration > 0 else 0.5
        new_conc = concentration * decay
        return cls(
            alpha=mean * new_conc,
            beta=(1.0 - mean) * new_conc,
            prev_posterior_ref=(a, b),
        )


def _clamp01(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _normal_quantile(level: float) -> float:
    """Two-sided normal quantile for an arbitrary confidence ``level``.

    Acklam's rational approximation of the inverse normal CDF — stdlib-only, so
    no SciPy. Only used off the 95% fast-path.
    """
    p = 1.0 - (1.0 - level) / 2.0
    a = [
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    ]
    b = [
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    ]
    c = [
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    ]
    d = [
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    ]
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
        (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
    )


# ---------------------------------------------------------------------------
# The gated observation entry point — enforces Q6 + run-outcome filtering
# ---------------------------------------------------------------------------

ObservationEffect = Literal["updated_success", "updated_failure", "judge_only", "skipped"]


@dataclass
class Observation:
    """The result of routing one scored leaf into a cell posterior."""

    effect: ObservationEffect
    resolution: Resolution


def apply_observation(
    posterior: BetaPosterior,
    *,
    outcome: RunOutcome,
    signal_verified: bool,
    resolution: Resolution = "native",
    confidence: float = 1.0,
) -> Observation:
    """Route one scored leaf into ``posterior``, enforcing every gate.

    * ``invalid_technical`` → ``skipped`` (posterior untouched).
    * ``valid_success`` + ``signal_verified`` → ``alpha`` bump (``updated_success``).
    * ``valid_success`` + judge-only → ``judge_only`` (posterior untouched; the
      caller records it to the ``judge_only_signal`` sink). **This is the Q6
      single-success-channel guarantee.**
    * ``valid_refusal`` → ``beta`` bump (``updated_failure``).
    """
    if outcome == "invalid_technical":
        return Observation(effect="skipped", resolution=resolution)
    if outcome == "valid_success":
        if not signal_verified:
            return Observation(effect="judge_only", resolution=resolution)
        posterior.update(success_signal_verified=True, confidence=confidence, resolution=resolution)
        return Observation(effect="updated_success", resolution=resolution)
    # valid_refusal
    posterior.update(success_signal_verified=False, confidence=confidence, resolution=resolution)
    return Observation(effect="updated_failure", resolution=resolution)


__all__ = [
    "BACKFILL_WEIGHT_DEFAULT",
    "NATIVE_WEIGHT",
    "BetaPosterior",
    "CredibleInterval",
    "Observation",
    "ObservationEffect",
    "Resolution",
    "apply_observation",
]
