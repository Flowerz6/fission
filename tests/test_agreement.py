"""Tests for ``core.agreement`` — the Judge Quality panel's statistics.

Two kinds of test here:

* **Known-value tests** against published reference results, so the
  estimators are verified against something outside this repo rather
  than against themselves. Krippendorff's own canonical worked example
  pins alpha; the JailbreakBench judge-comparison set (MIT, 300 items, three
  human annotators plus four published baseline judges) pins the whole
  report shape on real data.
* **Degenerate-input tests**, because the gold set starts empty and a
  panel that crashes or silently renders ``0.0%`` on a thin set is worse
  than one that says "not estimable".

``tests/data/jbb_judge_comparison_labels.csv`` is the labels-only slice
of JailbreakBench's ``judge_comparison`` split — the prompts and model
responses are deliberately NOT vendored (they are jailbreak strings and
harmful completions; the agreement maths needs only the labels).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from advtest_ext.core.agreement import (
    build_agreement_report,
    cohen_kappa,
    error_rates,
    krippendorff_alpha,
    wilson_interval,
)

_JBB = Path(__file__).parent / "data" / "jbb_judge_comparison_labels.csv"

# JailbreakBench encodes "the jailbreak succeeded" as 1. Map onto the
# project verdict vocabulary so the estimators are exercised through the
# same string labels the gold set actually stores.
_VERDICT = {"1": "success", "0": "refused"}


def _jbb_rows() -> list[dict[str, str]]:
    with _JBB.open(newline="") as fh:
        return list(csv.DictReader(fh))


# ----------------------------------------------------------------------
# Krippendorff's alpha
# ----------------------------------------------------------------------


def test_alpha_matches_krippendorffs_canonical_example() -> None:
    """Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability",
    3 observers x 15 units with missing ratings. Published nominal
    alpha = 0.691. This is the load-bearing correctness check: it verifies
    both the coincidence-matrix maths AND the missing-data handling,
    which is the whole reason alpha is used instead of κ for the ceiling."""
    matrix = [
        [None, None, None, None, None, 3, 4, 1, 2, 1, 1, 3, 3, None, 3],
        [1, None, 2, 1, 3, 3, 4, 3, None, None, None, None, None, None, None],
        [None, None, 2, 1, 3, 4, 4, None, 2, 1, 1, 3, 3, None, 4],
    ]
    units = [[str(row[u]) for row in matrix if row[u] is not None] for u in range(15)]
    assert krippendorff_alpha(units) == pytest.approx(0.691, abs=0.001)


def test_alpha_perfect_and_degenerate_cases() -> None:
    assert krippendorff_alpha([["a", "a"], ["b", "b"]]) == 1.0
    # Every rating identical: no chance disagreement to correct against.
    assert krippendorff_alpha([["a", "a"], ["a", "a"]]) == 1.0
    # Nothing double-rated -> undefined, must not pretend to be 0-agreement.
    assert krippendorff_alpha([["a"], ["b"]]) == 0.0
    assert krippendorff_alpha([]) == 0.0


def test_alpha_ignores_single_rated_units() -> None:
    """Single-rated items carry no agreement information, so adding them
    must not move alpha — otherwise the ceiling would drift as the review
    queue grows even when no new double-labelling happened."""
    doubled = [["a", "a"], ["b", "b"], ["a", "b"]]
    padded = [*doubled, ["a"], ["b"], ["a"]]
    assert krippendorff_alpha(padded) == pytest.approx(krippendorff_alpha(doubled))


# ----------------------------------------------------------------------
# Cohen's kappa
# ----------------------------------------------------------------------


def test_kappa_is_chance_corrected_not_raw_agreement() -> None:
    """The point of κ: on a lopsided set, high raw agreement is mostly
    chance. Two raters who both say "refused" 90% of the time
    independently agree ~82% of the time while κ stays near 0."""
    a = ["refused"] * 9 + ["success"]
    b = ["refused"] * 8 + ["success", "refused"]
    raw = sum(1 for x, y in zip(a, b, strict=True) if x == y) / len(a)
    assert raw >= 0.8
    assert cohen_kappa(a, b) < 0.5


def test_kappa_degenerate_cases() -> None:
    assert cohen_kappa(["a", "b"], ["a", "b"]) == 1.0
    assert cohen_kappa([], []) == 0.0
    # Mismatched lengths are a caller bug, not a crash.
    assert cohen_kappa(["a"], ["a", "b"]) == 0.0
    # Both raters used exactly one category: pe == 1, kappa undefined.
    assert cohen_kappa(["a", "a"], ["a", "a"]) == 1.0
    assert cohen_kappa(["a", "a"], ["b", "b"]) == 0.0


# ----------------------------------------------------------------------
# Wilson intervals
# ----------------------------------------------------------------------


def test_wilson_stays_inside_unit_interval_at_zero_events() -> None:
    """The case that motivated Wilson over the normal approximation: the
    current gold set has ~6 predicted positives, so an FP rate of 0/6
    must report a wide honest interval, not a degenerate [0, 0] that
    reads as "no false positives"."""
    lo, hi = wilson_interval(0, 6)
    assert lo == 0.0
    assert 0.35 < hi < 0.45
    for k, n in ((0, 1), (1, 1), (0, 500), (500, 500), (3, 7)):
        lo, hi = wilson_interval(k, n)
        assert 0.0 <= lo <= hi <= 1.0


def test_wilson_narrows_as_n_grows() -> None:
    wide = wilson_interval(5, 10)
    narrow = wilson_interval(500, 1000)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_wilson_empty_denominator() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)


# ----------------------------------------------------------------------
# Error rates
# ----------------------------------------------------------------------


def test_error_rate_denominators_are_not_the_gold_set_size() -> None:
    """FP rate is over PREDICTED positives, FN over ACTUAL positives —
    both far smaller than n. Rendering either against n would overstate
    the evidence behind the number."""
    predicted = ["success", "success", "refused", "refused", "refused"]
    truth = ["success", "refused", "success", "refused", "refused"]
    fp, fn = error_rates(predicted, truth)
    assert (fp.numerator, fp.n) == (1, 2)  # 2 predicted positive, 1 wrong
    assert (fn.numerator, fn.n) == (1, 2)  # 2 actually positive, 1 missed
    assert fp.rate == pytest.approx(0.5)
    assert fn.rate == pytest.approx(0.5)


def test_error_rates_report_not_estimable_when_no_positives() -> None:
    fp, fn = error_rates(["refused", "refused"], ["refused", "refused"])
    assert not fp.is_estimable
    assert not fn.is_estimable


def test_partial_counts_as_not_success() -> None:
    """Matches the ASR convention in core.ppi: only ``success`` is the
    positive class, so a ``partial`` the oracle called ``success`` is a
    false positive."""
    fp, _ = error_rates(["success"], ["partial"])
    assert (fp.numerator, fp.n) == (1, 1)


# ----------------------------------------------------------------------
# Whole report, on real published data
# ----------------------------------------------------------------------


def test_human_ceiling_on_jailbreakbench_annotators() -> None:
    """Three independent human annotators over 300 real jailbreak
    transcripts. This is a genuine published ceiling, computed from
    third-party labels — the number the panel must render beside κ."""
    rows = _jbb_rows()
    assert len(rows) == 300
    panels = [[_VERDICT[r[f"human{i}"]] for i in (1, 2, 3)] for r in rows]
    assert krippendorff_alpha(panels) == pytest.approx(0.841, abs=0.005)


@pytest.mark.parametrize(
    ("judge", "expected_kappa"),
    [
        ("harmbench_cf", 0.564),
        ("gpt4_cf", 0.795),
        ("llamaguard2_cf", 0.741),
        ("llama3_cf", 0.805),
    ],
)
def test_published_baseline_judges_reproduce(judge: str, expected_kappa: float) -> None:
    """Four published judges scored against the same human consensus.
    These are the baselines Fission's own judge is compared against on
    the Judge Quality panel, so the maths behind that comparison is
    pinned here. HarmBench-cls scoring lowest is not incidental — the
    technical report names it as an L1 attack-success classifier
    candidate, and its κ of 0.56 is the argument against that choice."""
    rows = _jbb_rows()
    truth = [_VERDICT[r["human_majority"]] for r in rows]
    predicted = [_VERDICT[r[judge]] for r in rows]
    assert cohen_kappa(predicted, truth) == pytest.approx(expected_kappa, abs=0.005)


def test_report_places_oracle_under_the_human_ceiling() -> None:
    """End-to-end shape check on real data, standing in for a Fission
    oracle by using the strongest published baseline. Every judge in
    this set sits below the human ceiling; a report claiming otherwise
    on a real gold set means too few items were double-labelled."""
    rows = _jbb_rows()
    report = build_agreement_report(
        oracle=[_VERDICT[r["llama3_cf"]] for r in rows],
        expert=[_VERDICT[r["human_majority"]] for r in rows],
        expert_panels=[[_VERDICT[r[f"human{i}"]] for i in (1, 2, 3)] for r in rows],
    )
    assert report.n == 300
    assert report.n_multi_rated == 300
    assert report.has_ceiling
    assert report.kappa_oracle == pytest.approx(0.805, abs=0.005)
    assert report.alpha_human == pytest.approx(0.841, abs=0.005)
    assert report.ceiling_gap > 0
    assert report.observed_agreement > report.kappa_oracle  # chance correction bites
    assert report.false_positive.is_estimable
    assert report.false_negative.is_estimable
    assert sum(report.verdict_distribution.values()) == 300


def test_report_without_double_labelling_has_no_ceiling() -> None:
    """The cold-start case: one labeller, so κ is computable but the
    ceiling is not. The panel must be able to tell those apart."""
    report = build_agreement_report(
        oracle=["success", "refused", "refused"],
        expert=["success", "refused", "success"],
        expert_panels=[],
    )
    assert report.n == 3
    assert not report.has_ceiling
    assert report.n_multi_rated == 0
    assert report.alpha_human == 0.0


def test_empty_report_is_safe() -> None:
    report = build_agreement_report(oracle=[], expert=[], expert_panels=[])
    assert report.n == 0
    assert report.kappa_oracle == 0.0
    assert not report.has_ceiling
    assert not report.false_positive.is_estimable
