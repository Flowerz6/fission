"""Oracle calibration metrics — how well does the judge agree with
ground truth? (``docs/FISSION_MEASUREMENT_SPINE.md`` §7 / production-gap
"no judge calibration set").

The measurement spine gives a run a calibrated ASR ± CI. The obvious next
question a reviewer asks is: *how much do I trust the oracle that
produced it?* This module answers that by scoring the oracle's verdicts
against the labelled public benchmarks ingested as ``CalibrationExample``
rows — reporting precision/recall/F1 (agreement) AND expected calibration
error (are the confidence scores honest?), overall and per stratum
(dataset, attack type) so weak spots are visible.

Pure and dependency-free (stdlib only): it takes ``(expected, predicted,
score)`` records — the LLM judge that produces them lives in the runner
(``scripts/run_calibration.py``) — so every metric is unit-testable
without a live model.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

__all__ = [
    "CalibrationReport",
    "LabeledPrediction",
    "StratumMetrics",
    "build_calibration_report",
    "build_calibration_report_from_db",
    "compute_metrics",
    "expected_calibration_error",
]

# Positive class for the confusion matrix: "the attack succeeded".
_POSITIVE = "success"


def _is_pos(verdict: str) -> bool:
    return verdict == _POSITIVE


@dataclass(frozen=True)
class LabeledPrediction:
    """One scored calibration example.

    ``score`` is the oracle's P(success) in [0, 1] (the judge score);
    ``predicted`` is its verdict; ``expected`` is the ground-truth label.
    ``dataset`` / ``attack_type`` are strata for per-slice breakdowns.
    """

    expected: str
    predicted: str
    score: float
    dataset: str = ""
    attack_type: str = ""


@dataclass(frozen=True)
class StratumMetrics:
    label: str
    n: int
    tp: int
    fp: int
    tn: int
    fn: int
    accuracy: float
    precision: float
    recall: float
    f1: float


@dataclass(frozen=True)
class CalibrationReport:
    n: int
    overall: StratumMetrics
    ece: float
    reliability: list[dict[str, float]]
    by_dataset: list[StratumMetrics]
    by_attack_type: list[StratumMetrics] = field(default_factory=list)


def compute_metrics(
    preds: Sequence[LabeledPrediction], *, label: str = "overall"
) -> StratumMetrics:
    """Confusion-matrix metrics treating ``success`` as the positive class.

    Precision/recall/F1 are 0.0 when their denominator is empty (no
    positive predictions / no positive labels) rather than undefined, so
    a stratum with no attacks still reports cleanly."""
    tp = fp = tn = fn = 0
    for p in preds:
        pred_pos, exp_pos = _is_pos(p.predicted), _is_pos(p.expected)
        if pred_pos and exp_pos:
            tp += 1
        elif pred_pos and not exp_pos:
            fp += 1
        elif not pred_pos and exp_pos:
            fn += 1
        else:
            tn += 1
    n = tp + fp + tn + fn
    accuracy = (tp + tn) / n if n else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return StratumMetrics(
        label=label,
        n=n,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        accuracy=accuracy,
        precision=precision,
        recall=recall,
        f1=f1,
    )


def expected_calibration_error(
    preds: Sequence[LabeledPrediction],
    *,
    n_bins: int = 10,
) -> tuple[float, list[dict[str, float]]]:
    """ECE + per-bin reliability data.

    Confidence for each example is the oracle's confidence *in its own
    prediction*: ``score`` when it predicted success, else ``1 - score``.
    Examples are binned by confidence; ECE is the size-weighted mean gap
    between bin accuracy and bin confidence. A well-calibrated oracle has
    ECE near 0 (its stated confidence matches how often it's right)."""
    if not preds:
        return 0.0, []
    bins: list[list[tuple[float, bool]]] = [[] for _ in range(n_bins)]
    for p in preds:
        conf = p.score if _is_pos(p.predicted) else (1.0 - p.score)
        conf = max(0.0, min(1.0, conf))
        idx = min(n_bins - 1, int(conf * n_bins))
        bins[idx].append((conf, p.predicted == p.expected))
    total = len(preds)
    ece = 0.0
    reliability: list[dict[str, float]] = []
    for i, b in enumerate(bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        if not b:
            reliability.append({"lo": lo, "hi": hi, "n": 0, "confidence": 0.0, "accuracy": 0.0})
            continue
        conf = sum(c for c, _ in b) / len(b)
        acc = sum(1 for _, ok in b if ok) / len(b)
        ece += (len(b) / total) * abs(acc - conf)
        reliability.append({"lo": lo, "hi": hi, "n": len(b), "confidence": conf, "accuracy": acc})
    return ece, reliability


def _by_key(
    preds: Sequence[LabeledPrediction],
    key: str,
) -> list[StratumMetrics]:
    groups: dict[str, list[LabeledPrediction]] = defaultdict(list)
    for p in preds:
        groups[getattr(p, key) or "?"].append(p)
    return [compute_metrics(v, label=k) for k, v in sorted(groups.items())]


def build_calibration_report(
    preds: Sequence[LabeledPrediction],
    *,
    n_bins: int = 10,
) -> CalibrationReport:
    """Assemble the full report: overall metrics + ECE + per-dataset and
    per-attack-type F1 breakdowns."""
    ece, reliability = expected_calibration_error(preds, n_bins=n_bins)
    return CalibrationReport(
        n=len(preds),
        overall=compute_metrics(preds, label="overall"),
        ece=ece,
        reliability=reliability,
        by_dataset=_by_key(preds, "dataset"),
        by_attack_type=_by_key(preds, "attack_type"),
    )


def build_calibration_report_from_db(
    session: Session,
    *,
    batch: str | None = None,
) -> CalibrationReport:
    """Build the report from stored oracle predictions (most recent batch
    by default). Thin DB glue over :func:`build_calibration_report`; lazy
    storage import keeps ``core`` free of a hard dependency at load."""
    from advtest_ext.storage.repository import calibration_predictions

    rows = calibration_predictions(session, batch=batch)
    preds = [
        LabeledPrediction(expected=e, predicted=p, score=s, dataset=d, attack_type=a)
        for (e, p, s, d, a) in rows
    ]
    return build_calibration_report(preds)
