"""Fit the TAP search calibrators from expert-labelled gold rows.

The TAP attack's search gradient keys off two learned calibrators
(``advtest_ext.attacks.tap_search``):

* :class:`JudgeCalibrator` maps a raw judge score to its empirical success
  rate, so ranking / pruning / the TextGrad trigger track reality; and
* :class:`PartialSignalCalibrator` weights each partial-compliance signal
  by how often it actually preceded a real success.

Both MUST learn from expert ground truth — ``GoldLabel.expert_verdict`` —
never the oracle's own verdict. Calibrating the oracle against its own
labels is circular and would launder the judge's mistakes into the
gradient. This module turns gold rows into the calibrators' fit inputs and
drives them.

Pure aside from the calibrator file writes: the gold rows are passed in as
plain objects (anything exposing ``expert_verdict``, ``oracle_score``, and
``oracle_layers_fired``), so the fitting logic is unit-testable without a
database. The CLI (``advtest calibrate``) reads the rows and calls
:func:`fit_search_calibrators`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

# The positive class for calibration: "the attack genuinely succeeded".
_SUCCESS = "success"


class GoldRow(Protocol):
    """The slice of ``storage.models.GoldLabel`` calibration reads."""

    expert_verdict: str
    oracle_score: float
    oracle_layers_fired: dict[str, Any]


@dataclass
class SearchCalibrationResult:
    """What a calibration pass learned, for operator reporting."""

    judge_pairs: int
    partial_reviews: int
    judge_curve: list[tuple[float, float]] = field(default_factory=list)
    partial_signal_stats: dict[str, dict[str, float]] = field(default_factory=dict)
    skipped_no_score: int = 0
    skipped_no_signals: int = 0
    persisted: bool = False


def fired_signal_names(layers: dict[str, Any] | None) -> set[str]:
    """The signal names that fired for one labelled trace: keys of
    ``oracle_layers_fired`` whose value is truthy — a ``True`` flag, a
    positive numeric score, or a non-empty payload. A ``False`` / ``0`` /
    empty value means the signal was evaluated but did not fire."""
    names: set[str] = set()
    for name, value in (layers or {}).items():
        if isinstance(value, bool):
            fired = value
        elif isinstance(value, (int, float)):
            fired = value > 0
        else:
            fired = bool(value)
        if fired:
            names.add(str(name))
    return names


def build_judge_reviews(rows: Sequence[GoldRow]) -> tuple[list[tuple[float, bool]], int]:
    """``(oracle_score, was_success)`` pairs for judge calibration, plus the
    count of rows skipped for lacking a score."""
    reviews: list[tuple[float, bool]] = []
    skipped = 0
    for row in rows:
        score = getattr(row, "oracle_score", None)
        verdict = getattr(row, "expert_verdict", None)
        if score is None or not verdict:
            skipped += 1
            continue
        reviews.append((float(score), verdict == _SUCCESS))
    return reviews, skipped


def build_partial_reviews(rows: Sequence[GoldRow]) -> tuple[list[tuple[set[str], bool]], int]:
    """``(fired_signal_names, was_success)`` pairs for partial-signal
    calibration, plus the count of rows skipped for firing no signal."""
    reviews: list[tuple[set[str], bool]] = []
    skipped = 0
    for row in rows:
        verdict = getattr(row, "expert_verdict", None)
        fired = fired_signal_names(getattr(row, "oracle_layers_fired", None))
        if not verdict or not fired:
            skipped += 1
            continue
        reviews.append((fired, verdict == _SUCCESS))
    return reviews, skipped


def fit_search_calibrators(
    rows: Sequence[GoldRow],
    *,
    judge_calibration_path: str | Path | None,
    partial_calibration_path: str | Path | None,
    persist: bool = True,
) -> SearchCalibrationResult:
    """Fit both search calibrators from expert-labelled ``rows`` and, when
    ``persist`` is set, write each calibration file. Returns a summary of what
    was learned. Constructing the calibrators with ``None`` paths (``persist``
    off) keeps the pass read-only for a dry run."""
    # Imported lazily so this core module has no import-time dependency on the
    # attacks package (and the CLI stays fast to load).
    from advtest_ext.attacks.tap_search import JudgeCalibrator, PartialSignalCalibrator

    judge_reviews, skipped_score = build_judge_reviews(rows)
    partial_reviews, skipped_signals = build_partial_reviews(rows)

    judge = JudgeCalibrator(judge_calibration_path if persist else None)
    partial = PartialSignalCalibrator(partial_calibration_path if persist else None)
    if judge_reviews:
        judge.fit(judge_reviews)
    if partial_reviews:
        partial.fit(partial_reviews)

    return SearchCalibrationResult(
        judge_pairs=len(judge_reviews),
        partial_reviews=len(partial_reviews),
        judge_curve=list(judge.curve),
        partial_signal_stats=dict(partial.stats),
        skipped_no_score=skipped_score,
        skipped_no_signals=skipped_signals,
        persisted=persist and bool(judge_reviews or partial_reviews),
    )


__all__ = [
    "GoldRow",
    "SearchCalibrationResult",
    "build_judge_reviews",
    "build_partial_reviews",
    "fired_signal_names",
    "fit_search_calibrators",
]
