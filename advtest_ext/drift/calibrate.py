"""False-drift calibration.

Against a KNOWN-STABLE target (no real change), run N cycles and measure the
false-alarm rate of the change-point + divergence detectors. Thresholds are
config, tuned to hold a target false-drift rate. **A detector whose false-alarm
rate isn't calibrated must not raise alerts** — :func:`alerts_enabled` gates on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from advtest_ext.drift.changepoint import cusum


@dataclass
class DriftThresholds:
    """Tunable detector thresholds; ``calibrated`` gates whether alerts may fire."""

    cusum_h: float = 5.0
    cusum_slack: float = 0.05
    posterior_z: float = 3.0
    psi_alert: float = 0.2
    calibrated: bool = False


@dataclass(frozen=True)
class FalseDriftReport:
    false_alarm_rate: float
    target_rate: float
    n_series: int
    within_budget: bool
    thresholds: DriftThresholds


def _false_alarm_rate(stable_series: Sequence[Sequence[bool]], *, baseline_rate: float, h: float, slack: float) -> float:
    if not stable_series:
        return 0.0
    alarms = sum(1 for series in stable_series if cusum(series, baseline_rate=baseline_rate, threshold=h, slack=slack).detected)
    return alarms / len(stable_series)


def calibrate_false_drift(
    stable_series: Sequence[Sequence[bool]],
    *,
    baseline_rate: float,
    thresholds: DriftThresholds | None = None,
    target_rate: float = 0.05,
    max_h: float = 40.0,
) -> tuple[DriftThresholds, FalseDriftReport]:
    """Raise ``cusum_h`` until the false-alarm rate on stable data is within budget.

    Returns the (possibly tuned) thresholds — with ``calibrated=True`` iff the
    budget was met — and the report.
    """
    thresholds = thresholds or DriftThresholds()
    h = thresholds.cusum_h
    rate = _false_alarm_rate(stable_series, baseline_rate=baseline_rate, h=h, slack=thresholds.cusum_slack)
    while rate > target_rate and h < max_h:
        h += 1.0
        rate = _false_alarm_rate(stable_series, baseline_rate=baseline_rate, h=h, slack=thresholds.cusum_slack)
    within = rate <= target_rate
    tuned = DriftThresholds(
        cusum_h=h, cusum_slack=thresholds.cusum_slack, posterior_z=thresholds.posterior_z,
        psi_alert=thresholds.psi_alert, calibrated=within,
    )
    report = FalseDriftReport(
        false_alarm_rate=rate, target_rate=target_rate, n_series=len(stable_series),
        within_budget=within, thresholds=tuned,
    )
    return tuned, report


def alerts_enabled(thresholds: DriftThresholds) -> bool:
    """Alerts may fire only from a calibrated detector."""
    return thresholds.calibrated


__all__ = [
    "DriftThresholds",
    "FalseDriftReport",
    "alerts_enabled",
    "calibrate_false_drift",
]
