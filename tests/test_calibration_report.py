"""Tests for ``core.calibration_report`` — oracle agreement (F1) and
calibration (ECE) against labelled benchmarks.

The load-bearing properties: F1 rewards agreement on the positive
(attack-succeeded) class; ECE is ~0 when the oracle's confidence matches
its accuracy and grows when it's over/under-confident; per-stratum
breakdowns isolate weak datasets.
"""

from __future__ import annotations

import pytest

from advtest_ext.core.calibration_report import (
    LabeledPrediction,
    build_calibration_report,
    compute_metrics,
    expected_calibration_error,
)


def _p(expected, predicted, score=None, dataset="d", attack_type="direct_pi"):
    # Default score consistent with the predicted verdict.
    if score is None:
        score = 0.9 if predicted == "success" else 0.1
    return LabeledPrediction(
        expected=expected,
        predicted=predicted,
        score=score,
        dataset=dataset,
        attack_type=attack_type,
    )


def test_perfect_agreement_scores_f1_one() -> None:
    preds = [_p("success", "success")] * 5 + [_p("refused", "refused")] * 5
    m = compute_metrics(preds)
    assert m.f1 == pytest.approx(1.0)
    assert m.accuracy == pytest.approx(1.0)
    assert (m.tp, m.tn, m.fp, m.fn) == (5, 5, 0, 0)


def test_confusion_counts_and_precision_recall() -> None:
    # 3 real successes: oracle catches 2 (TP), misses 1 (FN); also 1 FP.
    preds = [
        _p("success", "success"),
        _p("success", "success"),
        _p("success", "refused"),
        _p("refused", "success"),
        _p("refused", "refused"),
    ]
    m = compute_metrics(preds)
    assert (m.tp, m.fn, m.fp, m.tn) == (2, 1, 1, 1)
    assert m.precision == pytest.approx(2 / 3)
    assert m.recall == pytest.approx(2 / 3)
    assert m.f1 == pytest.approx(2 / 3)


def test_empty_denominators_are_zero_not_error() -> None:
    # No positive predictions and no positive labels → clean zeros.
    m = compute_metrics([_p("refused", "refused")] * 3)
    assert m.precision == 0.0 and m.recall == 0.0 and m.f1 == 0.0
    assert m.accuracy == pytest.approx(1.0)  # all correctly refused


def test_ece_near_zero_when_confidence_matches_accuracy() -> None:
    # Oracle right ~90% of the time AND states ~0.9 confidence → low ECE.
    preds = [_p("success", "success", 0.9) for _ in range(9)]
    preds += [_p("refused", "success", 0.9)]  # 1 wrong at high confidence
    ece, _ = expected_calibration_error(preds)
    assert ece < 0.15


def test_ece_high_when_overconfident() -> None:
    # Oracle claims 0.99 confidence but is right only half the time.
    preds = [_p("success", "success", 0.99) for _ in range(5)]
    preds += [_p("refused", "success", 0.99) for _ in range(5)]
    ece, _ = expected_calibration_error(preds)
    assert ece > 0.4


def test_reliability_bins_sum_to_all_examples() -> None:
    preds = [_p("success", "success", s / 10) for s in range(11)]
    _, rel = expected_calibration_error(preds, n_bins=5)
    assert sum(b["n"] for b in rel) == len(preds)
    assert len(rel) == 5


def test_report_breaks_down_by_dataset_and_attack_type() -> None:
    preds = (
        [_p("success", "success", dataset="jbb", attack_type="jailbreak")] * 4
        + [_p("success", "refused", dataset="gandalf", attack_type="direct_pi")]
        * 4  # judge misses all
    )
    rep = build_calibration_report(preds)
    assert rep.n == 8
    by_ds = {m.label: m for m in rep.by_dataset}
    assert by_ds["jbb"].f1 == pytest.approx(1.0)
    assert by_ds["gandalf"].recall == pytest.approx(0.0)  # missed every success
    by_at = {m.label: m for m in rep.by_attack_type}
    assert set(by_at) == {"jailbreak", "direct_pi"}


def test_empty_report_is_safe() -> None:
    rep = build_calibration_report([])
    assert rep.n == 0
    assert rep.ece == 0.0
    assert rep.overall.f1 == 0.0
    assert rep.reliability == []
