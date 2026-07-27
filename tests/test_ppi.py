"""Tests for ``core.ppi`` — prediction-powered ASR with a calibrated CI.

The properties that matter: the rectifier moves the oracle's ASR toward
the expert ground truth; the CI shrinks as more labels arrive; a perfect
oracle leaves the estimate unchanged; and the no-label / empty paths
degrade honestly.
"""

from __future__ import annotations

import pytest

from advtest_ext.core.ppi import PpiEstimate, ppi_asr


def _pairs(oracle: str, expert: str, k: int) -> list[tuple[str, str]]:
    return [(oracle, expert)] * k


def test_empty_run_is_degenerate() -> None:
    est = ppi_asr(n_total=0, n_oracle_success=0, labeled_pairs=[])
    assert est.method == "empty"
    assert est.asr == 0.0


def test_no_labels_falls_back_to_oracle_only() -> None:
    est = ppi_asr(n_total=100, n_oracle_success=70, labeled_pairs=[])
    assert est.method == "oracle_only"
    assert est.asr == pytest.approx(0.70)
    assert est.n_labeled == 0
    # A real (uncalibrated) sampling interval, strictly inside [0, 1].
    assert 0.0 < est.ci_low < 0.70 < est.ci_high < 1.0


def test_perfect_oracle_leaves_estimate_unchanged() -> None:
    # Oracle == expert on every labeled trace → rectifier 0 → theta = oracle ASR.
    est = ppi_asr(
        n_total=200,
        n_oracle_success=140,
        labeled_pairs=_pairs("success", "success", 10) + _pairs("refused", "refused", 10),
    )
    assert est.method == "ppi"
    assert est.rectifier == pytest.approx(0.0)
    assert est.asr == pytest.approx(0.70)


def test_over_optimistic_oracle_is_corrected_down() -> None:
    # Oracle over-calls success: on the labeled slice it says success but
    # experts say refused → rectifier > 0 → PPI ASR below the oracle's.
    est = ppi_asr(
        n_total=100,
        n_oracle_success=80,
        labeled_pairs=_pairs("success", "refused", 10) + _pairs("success", "success", 10),
    )
    assert est.oracle_asr == pytest.approx(0.80)
    # rectifier = mean(f - y) = mean(1-0 [x10], 1-1 [x10]) = 0.5
    assert est.rectifier == pytest.approx(0.5)
    assert est.asr == pytest.approx(0.30)
    assert est.asr < est.oracle_asr


def test_under_calling_oracle_is_corrected_up() -> None:
    est = ppi_asr(
        n_total=100,
        n_oracle_success=20,
        labeled_pairs=_pairs("refused", "success", 10) + _pairs("refused", "refused", 10),
    )
    # rectifier = mean(0-1 [x10], 0-0 [x10]) = -0.5 → theta = 0.2 - (-0.5) = 0.7
    assert est.rectifier == pytest.approx(-0.5)
    assert est.asr == pytest.approx(0.70)


def test_ci_shrinks_with_more_labels() -> None:
    few = ppi_asr(
        n_total=500,
        n_oracle_success=350,
        labeled_pairs=_pairs("success", "refused", 5) + _pairs("refused", "refused", 5),
    )
    many = ppi_asr(
        n_total=500,
        n_oracle_success=350,
        labeled_pairs=_pairs("success", "refused", 50) + _pairs("refused", "refused", 50),
    )
    assert many.ci_half_width < few.ci_half_width


def test_partial_counts_as_not_success_on_both_sides() -> None:
    # partial is not success for ASR, symmetric for oracle and expert.
    est = ppi_asr(
        n_total=10,
        n_oracle_success=0,
        labeled_pairs=_pairs("partial", "partial", 6),
    )
    assert est.rectifier == pytest.approx(0.0)
    assert est.asr == pytest.approx(0.0)


def test_estimate_and_ci_stay_in_unit_interval() -> None:
    # Extreme rectifier can't push the estimate or bounds outside [0, 1].
    est = ppi_asr(
        n_total=20,
        n_oracle_success=20,
        labeled_pairs=_pairs("success", "refused", 20),
    )
    assert 0.0 <= est.ci_low <= est.asr <= est.ci_high <= 1.0


def test_confidence_level_widens_interval() -> None:
    base = dict(
        n_total=200,
        n_oracle_success=120,
        labeled_pairs=_pairs("success", "refused", 10) + _pairs("refused", "refused", 10),
    )
    ci95 = ppi_asr(**base, confidence=0.95)
    ci99 = ppi_asr(**base, confidence=0.99)
    assert ci99.ci_half_width > ci95.ci_half_width


def test_estimate_is_dataclass_with_half_width() -> None:
    est = ppi_asr(n_total=10, n_oracle_success=5, labeled_pairs=_pairs("success", "success", 4))
    assert isinstance(est, PpiEstimate)
    assert est.ci_half_width == pytest.approx((est.ci_high - est.ci_low) / 2.0)
