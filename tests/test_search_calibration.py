"""Tests for fitting the TAP search calibrators from expert-labelled gold rows
(``advtest_ext.core.search_calibration``)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from advtest_ext.attacks.tap_search import JudgeCalibrator
from advtest_ext.core.search_calibration import (
    build_judge_reviews,
    build_partial_reviews,
    fired_signal_names,
    fit_search_calibrators,
)


@dataclass
class _Gold:
    expert_verdict: str
    oracle_score: float = 0.0
    oracle_layers_fired: dict[str, Any] = field(default_factory=dict)


def test_fired_signal_names_extracts_only_truthy_layers() -> None:
    layers = {
        "canary_appears": True,
        "not_fired_flag": False,
        "ipi_verdict": 0.42,
        "zero_score": 0.0,
        "empty": "",
        "payload": "text",
    }
    assert fired_signal_names(layers) == {"canary_appears", "ipi_verdict", "payload"}
    assert fired_signal_names(None) == set()


def test_build_judge_reviews_skips_rows_without_a_score() -> None:
    rows = [
        _Gold("success", oracle_score=0.9),
        _Gold("refused", oracle_score=0.1),
        _Gold("", oracle_score=0.5),  # no verdict -> skipped
    ]
    reviews, skipped = build_judge_reviews(rows)
    assert reviews == [(0.9, True), (0.1, False)]
    assert skipped == 1


def test_build_partial_reviews_skips_rows_that_fired_no_signal() -> None:
    rows = [
        _Gold("success", oracle_layers_fired={"grounded": True}),
        _Gold("refused", oracle_layers_fired={"grounded": False}),  # nothing fired -> skipped
        _Gold("partial", oracle_layers_fired={"leak": 0.3}),
    ]
    reviews, skipped = build_partial_reviews(rows)
    assert ({"grounded"}, True) in reviews
    assert ({"leak"}, False) in reviews
    assert skipped == 1


def test_fit_persists_monotonic_judge_curve_and_signal_stats(tmp_path: Any) -> None:
    judge_path = tmp_path / "judge_calibration.json"
    partial_path = tmp_path / "partial_signal_calibration.json"
    # An over-confident oracle: high scores rarely actually succeeded.
    rows = (
        [_Gold("refused", oracle_score=0.9, oracle_layers_fired={"weak": True})] * 6
        + [_Gold("success", oracle_score=0.9, oracle_layers_fired={"strong": True})] * 2
        + [_Gold("success", oracle_score=0.2, oracle_layers_fired={"strong": True})] * 5
    )
    result = fit_search_calibrators(
        rows,
        judge_calibration_path=judge_path,
        partial_calibration_path=partial_path,
        persist=True,
    )
    assert result.persisted is True
    assert judge_path.exists() and partial_path.exists()
    assert result.judge_pairs == len(rows)

    # The persisted curve reloads and is monotonic non-decreasing.
    reloaded = JudgeCalibrator(judge_path)
    xs = [i / 20 for i in range(21)]
    ys = [reloaded.calibrate(x) for x in xs]
    assert all(ys[i] <= ys[i + 1] + 1e-9 for i in range(len(ys) - 1))

    # 'strong' preceded success more often than 'weak'.
    stats = result.partial_signal_stats
    assert stats["strong"]["mean"] > stats["weak"]["mean"]

    # The written signal file is valid JSON.
    json.loads(partial_path.read_text())


def test_dry_run_writes_nothing_but_still_reports(tmp_path: Any) -> None:
    judge_path = tmp_path / "judge_calibration.json"
    partial_path = tmp_path / "partial_signal_calibration.json"
    rows = [_Gold("success", oracle_score=0.8, oracle_layers_fired={"g": True})] * 4
    result = fit_search_calibrators(
        rows,
        judge_calibration_path=judge_path,
        partial_calibration_path=partial_path,
        persist=False,
    )
    assert result.persisted is False
    assert not judge_path.exists()
    assert not partial_path.exists()
    assert result.judge_pairs == 4  # still computed for the report
