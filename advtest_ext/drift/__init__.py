"""Target-version vulnerability drift (Part E).

Detects that the TARGET became more (or less) vulnerable, WHERE, and on WHICH
axis — while ruling out search drift, judge drift, and tenant-state drift.

**Namespace:** everything here is TARGET-VERSION drift and uses a
``target_drift`` / ``posterior_drift`` prefix on tables and metrics. It is
DISTINCT from the operational drift in ``dashboard/drift.py`` and
``Trace.extra_metadata['drift']`` and never shadows those symbols.

Modules: :mod:`sentinel` (frozen control group), :mod:`changepoint` (sequential
detection + the Q4 undecayed-prior arithmetic), :mod:`fingerprint_diff`
(population divergence, resolution-segregated), :mod:`guards` (search/judge/tenant
impostor-drift guards), :mod:`attribution` (axis-level regression report), and
:mod:`calibrate` (false-drift calibration).
"""

from __future__ import annotations

from advtest_ext.drift.attribution import AxisRegression, VersionDiffReport, attribute_drift
from advtest_ext.drift.calibrate import (
    DriftThresholds,
    FalseDriftReport,
    alerts_enabled,
    calibrate_false_drift,
)
from advtest_ext.drift.changepoint import (
    ChangePoint,
    PosteriorDrift,
    cusum,
    posterior_drift,
    posterior_drift_for_cell,
)
from advtest_ext.drift.fingerprint_diff import (
    Divergence,
    FingerprintDiff,
    facet_attribution,
    fingerprint_divergence,
    regressed_cells,
)
from advtest_ext.drift.guards import (
    DriftAlert,
    JudgeDrift,
    SearchHealth,
    judge_drift,
    mark_leads_suspect,
    search_health,
    suppress_search_drift,
    tenant_guard_ok,
)
from advtest_ext.drift.sentinel import (
    Sentinel,
    SentinelObservation,
    run_sentinel_cycle,
    select_sentinels,
    sentinel_series,
)

__all__ = [
    "AxisRegression",
    "ChangePoint",
    "Divergence",
    "DriftAlert",
    "DriftThresholds",
    "FalseDriftReport",
    "FingerprintDiff",
    "JudgeDrift",
    "PosteriorDrift",
    "SearchHealth",
    "Sentinel",
    "SentinelObservation",
    "VersionDiffReport",
    "alerts_enabled",
    "attribute_drift",
    "calibrate_false_drift",
    "cusum",
    "facet_attribution",
    "fingerprint_divergence",
    "judge_drift",
    "mark_leads_suspect",
    "posterior_drift",
    "posterior_drift_for_cell",
    "regressed_cells",
    "run_sentinel_cycle",
    "search_health",
    "select_sentinels",
    "sentinel_series",
    "suppress_search_drift",
    "tenant_guard_ok",
]
