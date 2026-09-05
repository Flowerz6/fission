"""Inter-rater agreement — how much do we trust the oracle that
produced the ASR? (``docs/FISSION_MEASUREMENT_SPINE.md`` §7 TREND;
technical report §1.3(4) "measured oracle, not asserted oracle".)

``core.ppi`` gives a run a calibrated ASR ± CI. This module answers the
question a reviewer asks next: *how often is the judge wrong?* It scores
the oracle's verdicts against expert labels and reports the three
numbers the report commits to publishing per Run:

* **Cohen's κ** — oracle vs the expert consensus. The headline
  agreement number, chance-corrected so a lopsided verdict distribution
  (most attacks refuse) can't inflate it the way raw accuracy does.
* **Krippendorff's alpha** — agreement among the *experts themselves*. This
  is the **ceiling**: an oracle cannot beat the humans it is scored
  against. Reporting κ without it is misleading, because a κ of 0.77
  reads as mediocre until you know the humans only reach 0.84. Unlike
  κ, alpha handles a variable number of raters per item, which is the
  normal case once a review queue assigns overlapping slices.
* **False-positive / false-negative rate**, each with a **Wilson**
  interval. Wilson rather than the normal approximation because at the
  n a gold set realistically reaches (100-300) the normal approximation
  puts bounds outside [0, 1] and anyone who does this for a living will
  notice.

Verdict vocabulary is the project-wide ``success | partial | refused``.
κ and alpha treat those as unordered nominal categories; the error rates
collapse them to a binary with ``success`` as the positive class, which
is the same convention ``core.ppi`` and ``core.calibration_report``
already use so the numbers on one screen agree with each other.

Pure and dependency-free (stdlib ``statistics`` only) so every estimator
is unit-testable without a DB or a live model — the project deliberately
carries no numpy/scipy/sklearn, and κ/alpha/Wilson are a few lines each.
``agreement_for_gold_set`` is the thin DB glue.
"""

from __future__ import annotations

import itertools
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

__all__ = [
    "AgreementReport",
    "RateWithCI",
    "agreement_for_gold_set",
    "build_agreement_report",
    "cohen_kappa",
    "error_rates",
    "krippendorff_alpha",
    "wilson_interval",
]

# Positive class for the binary error rates, matching core.ppi and
# core.calibration_report ("partial" and "refused" are both not-success).
_POSITIVE = "success"


def _z_for(confidence: float) -> float:
    """Two-sided normal quantile (1.96 at 95%)."""
    return statistics.NormalDist().inv_cdf(1.0 - (1.0 - confidence) / 2.0)


# ----------------------------------------------------------------------
# Interval estimation
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class RateWithCI:
    """A proportion with its Wilson score interval.

    ``n`` is the DENOMINATOR the rate was computed over, not the size of
    the gold set — an FP rate is "of the items the oracle called success,
    how many did experts overturn", so its n is the count of predicted
    positives. Rendering the rate without that n invites a reader to
    assume the whole gold set backed it.
    """

    rate: float
    ci_low: float
    ci_high: float
    numerator: int
    n: int
    confidence: float = 0.95

    @property
    def ci_half_width(self) -> float:
        return (self.ci_high - self.ci_low) / 2.0

    @property
    def is_estimable(self) -> bool:
        """False when the denominator is empty — the UI must show an
        empty state, never ``0.0%``, which reads as "no errors"."""
        return self.n > 0


def wilson_interval(
    successes: int,
    n: int,
    *,
    confidence: float = 0.95,
) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal (Wald) approximation because it stays
    inside [0, 1] and keeps sane coverage at small n and at proportions
    near 0 or 1 — exactly where a gold set of ~100-300 items with a
    handful of positives lives. Returns ``(0.0, 0.0)`` for an empty
    denominator; callers should check ``RateWithCI.is_estimable``.
    """
    if n <= 0:
        return (0.0, 0.0)
    z = _z_for(confidence)
    p = successes / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = (z * ((p * (1.0 - p) / n + z * z / (4.0 * n * n)) ** 0.5)) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def _rate(numerator: int, n: int, confidence: float) -> RateWithCI:
    lo, hi = wilson_interval(numerator, n, confidence=confidence)
    return RateWithCI(
        rate=(numerator / n) if n else 0.0,
        ci_low=lo,
        ci_high=hi,
        numerator=numerator,
        n=n,
        confidence=confidence,
    )


# ----------------------------------------------------------------------
# Agreement statistics
# ----------------------------------------------------------------------


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    """Cohen's κ between two equal-length label sequences.

    Chance-corrected agreement: ``(po - pe) / (1 - pe)`` where ``po`` is
    observed agreement and ``pe`` the agreement expected if both raters
    labelled independently at their own marginal rates.

    Degenerate cases return a defensible scalar rather than raising, so
    a thin gold set can't crash the dashboard: an empty or mismatched
    input gives 0.0, and ``pe == 1`` (both raters used a single
    category, so chance agreement is total and κ is undefined) gives
    1.0 on perfect agreement else 0.0.
    """
    n = len(a)
    if n == 0 or n != len(b):
        return 0.0
    po = sum(1 for x, y in zip(a, b, strict=True) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    pe = sum((ca[c] / n) * (cb[c] / n) for c in set(ca) | set(cb))
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def krippendorff_alpha(units: Sequence[Sequence[str]]) -> float:
    """Krippendorff's alpha for nominal data, tolerant of missing ratings.

    ``units`` is one sequence of labels per item — *only the labels that
    exist*. Items rated by fewer than two annotators carry no
    information about agreement and are skipped, which is precisely why
    alpha is used here instead of κ: a review queue that assigns an
    overlapping subset to a second labeller produces exactly that ragged
    shape, and κ cannot consume it.

    Computed from the coincidence matrix:

        alpha = 1 - D_o / D_e

    with ``D_o`` the observed disagreement and ``D_e`` the disagreement
    expected by chance. Returns 0.0 when no item has ≥2 ratings (alpha is
    undefined — the caller should render an empty state, not a zero).
    """
    coincidence: defaultdict[tuple[str, str], float] = defaultdict(float)
    for unit in units:
        m = len(unit)
        if m < 2:
            continue
        for x, y in itertools.permutations(unit, 2):
            coincidence[(x, y)] += 1.0 / (m - 1)

    if not coincidence:
        return 0.0

    marginals: defaultdict[str, float] = defaultdict(float)
    for (c, _k), v in coincidence.items():
        marginals[c] += v
    n = sum(marginals.values())
    if n <= 1:
        return 0.0

    observed = sum(v for (c, k), v in coincidence.items() if c != k)
    expected = sum(marginals[c] * marginals[k] for c in marginals for k in marginals if c != k) / (
        n - 1
    )

    if expected <= 0:
        # Every rating is the same category: no chance disagreement to
        # correct against. Total agreement by construction.
        return 1.0 if observed == 0 else 0.0
    return 1.0 - observed / expected


def error_rates(
    predicted: Sequence[str],
    truth: Sequence[str],
    *,
    confidence: float = 0.95,
) -> tuple[RateWithCI, RateWithCI]:
    """``(false_positive_rate, false_negative_rate)`` with Wilson CIs.

    * FP rate — of the items the ORACLE called ``success``, the share
      experts overturned. Denominator = predicted positives. This is the
      number a buyer should ask every vendor for.
    * FN rate — of the items EXPERTS called ``success``, the share the
      oracle missed. Denominator = actual positives.

    Both denominators are usually far smaller than the gold set, which
    is why each rate carries its own ``n``.
    """
    tp = fp = fn = 0
    for p, t in zip(predicted, truth, strict=False):
        pred_pos, true_pos = p == _POSITIVE, t == _POSITIVE
        if pred_pos and true_pos:
            tp += 1
        elif pred_pos:
            fp += 1
        elif true_pos:
            fn += 1
    return (
        _rate(fp, tp + fp, confidence),
        _rate(fn, tp + fn, confidence),
    )


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class AgreementReport:
    """Everything the Judge Quality panel renders.

    ``alpha_human`` is the ceiling and MUST be displayed beside
    ``kappa_oracle``; ``n_multi_rated`` says how many items actually
    carried ≥2 expert labels, because an alpha computed over 12 overlapping
    items is not the same claim as one over 300 and the panel has to say
    which it is.
    """

    n: int
    """Gold-set items with an oracle prediction and an expert verdict."""
    kappa_oracle: float
    """Cohen's κ, oracle vs expert consensus."""
    alpha_oracle: float
    """Krippendorff's alpha, oracle vs expert consensus (comparable scale to
    ``alpha_human``, which κ is not)."""
    alpha_human: float
    """Krippendorff's alpha among experts — THE CEILING."""
    n_multi_rated: int
    """Items with ≥2 independent expert labels (the alpha denominator)."""
    observed_agreement: float
    """Raw oracle/expert agreement, uncorrected for chance. Shown next to
    κ so the chance correction is visible rather than implied."""
    false_positive: RateWithCI
    false_negative: RateWithCI
    verdict_distribution: dict[str, int] = field(default_factory=dict)
    """Expert verdict counts — context for how lopsided the set is."""

    @property
    def ceiling_gap(self) -> float:
        """How far the oracle sits below the human ceiling. Near zero is
        the strong result; negative means the oracle agrees with the
        consensus more than the humans agree with each other, which
        usually signals too few multi-rated items rather than a
        superhuman judge."""
        return self.alpha_human - self.alpha_oracle

    @property
    def has_ceiling(self) -> bool:
        """False when no item was double-labelled, so the panel must show
        the ceiling slot empty instead of implying 0.0."""
        return self.n_multi_rated > 0


def build_agreement_report(
    *,
    oracle: Sequence[str],
    expert: Sequence[str],
    expert_panels: Sequence[Sequence[str]] = (),
    confidence: float = 0.95,
) -> AgreementReport:
    """Assemble the panel from aligned label sequences.

    ``oracle`` / ``expert`` are per-item verdicts (expert = the
    consensus label). ``expert_panels`` is the per-item list of ALL
    independent expert labels, used only for the human ceiling; pass an
    empty sequence when nothing has been double-labelled yet and the
    report will report ``has_ceiling == False``.
    """
    n = min(len(oracle), len(expert))
    oracle, expert = list(oracle[:n]), list(expert[:n])

    fp, fn = error_rates(oracle, expert, confidence=confidence)
    multi = [p for p in expert_panels if len(p) >= 2]

    return AgreementReport(
        n=n,
        kappa_oracle=cohen_kappa(oracle, expert),
        alpha_oracle=krippendorff_alpha([[o, e] for o, e in zip(oracle, expert, strict=True)]),
        alpha_human=krippendorff_alpha(multi),
        n_multi_rated=len(multi),
        observed_agreement=(
            sum(1 for o, e in zip(oracle, expert, strict=True) if o == e) / n if n else 0.0
        ),
        false_positive=fp,
        false_negative=fn,
        verdict_distribution=dict(Counter(expert)),
    )


def agreement_for_gold_set(
    session: Session,
    *,
    run_id: str | None = None,
    confidence: float = 0.95,
) -> AgreementReport:
    """DB glue: build the report from stored :class:`GoldLabel` rows.

    Each gold label carries the oracle's FROZEN prediction alongside the
    expert verdict, so agreement is always scored against the predictor
    that actually labelled the trace — not whatever the oracle has since
    been retrained into. Labels are grouped by ``trace_id`` so a trace
    reviewed by two labellers contributes one consensus item plus one
    multi-rated panel entry for the ceiling.

    Lazy storage import keeps ``core`` free of a hard DB dependency at
    module load, matching ``core.ppi``.
    """
    from advtest_ext.storage.repository import gold_label_rater_panels

    panels = gold_label_rater_panels(session, run_id=run_id)

    oracle: list[str] = []
    expert: list[str] = []
    expert_panels: list[list[str]] = []
    for _trace_id, oracle_verdict, expert_verdicts in panels:
        if not expert_verdicts:
            continue
        oracle.append(oracle_verdict)
        expert.append(_consensus(expert_verdicts))
        expert_panels.append(list(expert_verdicts))

    return build_agreement_report(
        oracle=oracle,
        expert=expert,
        expert_panels=expert_panels,
        confidence=confidence,
    )


def _consensus(verdicts: Sequence[str]) -> str:
    """Majority expert verdict for one item.

    Ties break toward the LESS severe verdict (``refused`` < ``partial``
    < ``success``) so a split panel never manufactures a success the
    experts didn't agree on — the conservative direction for a metric
    whose headline is an attack-success rate.
    """
    counts = Counter(verdicts)
    top = max(counts.values())
    tied = [v for v, c in counts.items() if c == top]
    if len(tied) == 1:
        return tied[0]
    order = {"refused": 0, "partial": 1, "success": 2}
    return min(tied, key=lambda v: order.get(v, 0))
