"""Cross-run aggregates for the dashboard landing page.

Pure functions over the storage session — no I/O beyond SQLAlchemy — so
the overview metrics are unit-testable without a live server. The landing
page (``/``) is the first thing a reviewer sees; it must answer, in one
screen: how much testing has run, what it found (by severity), how
effective the attacks are (ASR), and whether the platform is detecting
drift. Everything here feeds that page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.storage.repository import list_findings, list_runs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

_SEVERITY_ORDER = ["critical", "high", "med", "low"]


@dataclass
class OverviewStats:
    """Headline numbers for the landing dashboard."""

    total_runs: int = 0
    total_traces: int = 0
    total_findings: int = 0
    overall_asr: float = 0.0
    findings_by_severity: dict[str, int] = field(default_factory=dict)
    drift_events: int = 0
    drift_pruned: int = 0  # attacker-goal drift (off-mechanism prunes)
    drift_recovered: int = 0  # platform/UI drift recovered in-Run
    asr_trend: list[dict[str, Any]] = field(default_factory=list)  # per-run, time-ordered
    attack_breakdown: list[dict[str, Any]] = field(default_factory=list)
    recent_runs: list[Any] = field(default_factory=list)


def _run_asr(run: Any) -> float:
    """Best-effort ASR for a run from its cached summary, else 0."""
    summ = run.summary or {}
    try:
        return float(summ.get("overall_asr", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def run_has_success(run: Any) -> bool:
    """Whether a run has a non-zero attack success rate.

    Zero-ASR runs remain durable and directly addressable, but are omitted
    from the presentation-facing run history and overview.
    """
    return _run_asr(run) > 0.0


def _drift_counts(traces: list[Any]) -> tuple[int, int]:
    """Count drift events recorded in trace metadata.

    Returns ``(pruned, recovered)``:
      * pruned    -- attacker-goal drift: a node flagged off-mechanism.
      * recovered -- platform/UI drift the Replayer absorbed in-Run.
    Both are read from ``extra_metadata['drift']`` (a list of event
    dicts) so the overview works for any attack that records them.
    """
    pruned = recovered = 0
    for t in traces:
        meta = getattr(t, "extra_metadata", None)
        if not isinstance(meta, dict):
            continue
        events = meta.get("drift")
        if not isinstance(events, list):
            continue
        for ev in events:
            if not isinstance(ev, dict):
                continue
            kind = ev.get("kind", "")
            if kind in ("off_mechanism", "pruned", "goal_drift"):
                pruned += 1
            elif kind in ("recovered", "selector_fallback", "dialog_cleared", "capture_retry"):
                recovered += 1
    return pruned, recovered


def build_overview(session: Session, *, run_limit: int = 200) -> OverviewStats:
    """Aggregate cross-run metrics for the landing page."""
    runs = [run for run in list_runs(session, limit=run_limit) if run_has_success(run)]
    findings = list_findings(session, limit=10_000)

    stats = OverviewStats()
    stats.total_runs = len(runs)
    stats.recent_runs = runs[:8]

    # Severity histogram.
    sev: dict[str, int] = {k: 0 for k in _SEVERITY_ORDER}
    for f in findings:
        key = (getattr(f, "severity", "") or "med").lower()
        sev[key] = sev.get(key, 0) + 1
    stats.findings_by_severity = sev
    stats.total_findings = len(findings)

    # Per-run ASR trend + traces + attack breakdown + drift, in one pass.
    attack_tot: dict[str, list[int]] = {}  # name -> [successes, total]
    asr_sum = 0.0
    asr_n = 0
    for run in reversed(runs):  # oldest -> newest for the trend line
        traces = list(getattr(run, "traces", []) or [])
        stats.total_traces += len(traces)
        asr = _run_asr(run)
        if traces:  # only runs that actually executed contribute to mean ASR
            asr_sum += asr
            asr_n += 1
        stats.asr_trend.append(
            {
                "run_id": run.id,
                "started_at": getattr(run, "started_at", None),
                "asr": asr,
                "traces": len(traces),
            }
        )
        pr, rc = _drift_counts(traces)
        stats.drift_pruned += pr
        stats.drift_recovered += rc
        for t in traces:
            name = getattr(t, "attack_name", "") or "unknown"
            slot = attack_tot.setdefault(name, [0, 0])
            slot[1] += 1
            if getattr(t, "verdict", "") == "success":
                slot[0] += 1

    stats.drift_events = stats.drift_pruned + stats.drift_recovered
    stats.overall_asr = (asr_sum / asr_n) if asr_n else 0.0

    stats.attack_breakdown = sorted(
        (
            {
                "attack": name,
                "successes": s,
                "total": tot,
                "asr": (s / tot) if tot else 0.0,
            }
            for name, (s, tot) in attack_tot.items()
        ),
        key=lambda d: d["total"],
        reverse=True,
    )
    return stats


__all__ = ["OverviewStats", "build_overview", "run_has_success"]
