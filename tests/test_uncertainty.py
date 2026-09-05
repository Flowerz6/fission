"""Tests for ``core.uncertainty`` — the measurement spine's per-trace
review-priority score.

The ordering guarantee is the load-bearing property: a hard L0<->L2
conflict must always outrank an uncorroborated success or a
near-threshold verdict, so the stratified sampler pulls the right
traces first. The one-sidedness of L0 evidence (a non-fired signal is
NOT proof of refusal) is the subtle correctness point exercised here.
"""

from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.core.uncertainty import Uncertainty, score_uncertainty


def _score(**kw) -> Uncertainty:
    base: dict[str, Any] = dict(
        judge_score=0.0, n_signals_fired=0, n_signals_total=0, threshold=0.5
    )
    base.update(kw)
    return score_uncertainty(**base)


def test_l0_l2_conflict_is_the_dominant_axis() -> None:
    # Signal fired (L0 says landed) but judge scored a confident refuse.
    u = _score(judge_score=0.0, n_signals_fired=1, n_signals_total=2)
    assert u.l0_l2_conflict is True
    assert u.uncorroborated_success is False
    assert u.score >= 0.7
    assert "conflict" in u.reason


def test_non_fired_signal_is_not_treated_as_refusal_evidence() -> None:
    """One-sidedness: judge says success, no signal fired. That is NOT a
    conflict (absence of a signal doesn't mean L0 disagrees) — it's the
    weaker 'uncorroborated success' axis."""
    u = _score(judge_score=0.9, n_signals_fired=0, n_signals_total=3)
    assert u.l0_l2_conflict is False
    assert u.uncorroborated_success is True
    assert u.score <= 0.6


def test_uncorroborated_requires_available_signals() -> None:
    # No signals configured at all → can't be 'uncorroborated', falls to
    # the near-threshold axis only.
    u = _score(judge_score=0.9, n_signals_fired=0, n_signals_total=0)
    assert u.uncorroborated_success is False
    assert u.l0_l2_conflict is False


def test_near_threshold_peaks_at_the_boundary() -> None:
    at = _score(judge_score=0.5, n_signals_fired=0, n_signals_total=0)
    far = _score(judge_score=0.0, n_signals_fired=0, n_signals_total=0)
    mid = _score(judge_score=0.25, n_signals_fired=0, n_signals_total=0)
    assert at.near_threshold == pytest.approx(1.0)
    assert far.near_threshold == pytest.approx(0.0)
    assert mid.near_threshold == pytest.approx(0.5)
    # score tracks proximity for the pure near-threshold case
    assert at.score > mid.score > far.score


def test_conflict_always_outranks_non_conflict() -> None:
    # Worst-case conflict (score just 0.7 when far from threshold) still
    # beats the best-case non-conflict (0.6 at the boundary).
    conflict_far = _score(judge_score=0.0, n_signals_fired=1, n_signals_total=1)
    uncorr_at = _score(judge_score=0.5 + 1e-9, n_signals_fired=0, n_signals_total=1)
    near_at = _score(judge_score=0.5, n_signals_fired=0, n_signals_total=0)
    assert conflict_far.score >= 0.7
    assert uncorr_at.score <= 0.6
    assert near_at.score <= 0.6
    assert conflict_far.score > uncorr_at.score
    assert conflict_far.score > near_at.score


def test_multiple_signals_fired_and_judge_agrees_is_not_conflict() -> None:
    # Judge success AND signal fired → oracle layers agree, low priority.
    u = _score(judge_score=0.9, n_signals_fired=2, n_signals_total=2)
    assert u.l0_l2_conflict is False
    assert u.uncorroborated_success is False
    # far from threshold, agreeing → low
    assert u.score < 0.2


def test_score_is_bounded_unit_interval() -> None:
    for js in (0.0, 0.2, 0.49, 0.5, 0.51, 0.8, 1.0):
        for fired in (0, 1, 3):
            for total in (0, 1, 3):
                if fired > total:
                    continue
                u = _score(judge_score=js, n_signals_fired=fired, n_signals_total=total)
                assert 0.0 <= u.score <= 1.0


def test_custom_threshold_shifts_the_boundary() -> None:
    # With threshold 0.8, a 0.6 judge score is now BELOW the cut, so a
    # fired signal conflicts with it.
    u = _score(judge_score=0.6, n_signals_fired=1, n_signals_total=1, threshold=0.8)
    assert u.l0_l2_conflict is True
