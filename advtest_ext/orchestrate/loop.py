"""The scheduled loop: phases, the per-profile lock, and version carry-forward."""

from __future__ import annotations

import inspect
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from advtest_ext.archive.posterior import BetaPosterior, Resolution
from advtest_ext.core.orch_trace_meta import run_outcome
from advtest_ext.drift.calibrate import DriftThresholds, alerts_enabled
from advtest_ext.drift.changepoint import cusum
from advtest_ext.drift.guards import JudgeDrift, judge_drift
from advtest_ext.drift.sentinel import (
    Sentinel,
    SentinelObservation,
    run_sentinel_cycle,
)
from advtest_ext.observe.baseline import (
    diff_observation,
    setup_fixture,
    snapshot_baseline,
    teardown_fixture,
)
from advtest_ext.scoring.coherence import archive_observe, coherent_run_outcome

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

    from advtest_ext.archive.fingerprint import Archive, CellKey, TargetProfileKey
    from advtest_ext.drift.sentinel import DeliverScore, FixtureController
    from advtest_ext.observe.baseline import WorkspaceObserver
    from advtest_ext.observe.model import SideEffectObservation, TenantState
    from advtest_ext.scoring.base import ScoreResult
    from advtest_ext.scoring.objectives import Objective

# Deliver+observe+score one native variant: returns (result, observation, objective).
# The variant must carry a ``.signature``. May be sync or async.
NativeDeliverScore = Callable[[Any], Any]
# variant -> the mutation node dict the materializer stages (``materializer.apply``).
NodeOf = Callable[[Any], "dict[str, Any]"]


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def profile_token(profile: TargetProfileKey) -> str:
    """Stable key for a composite target profile (the lock granularity)."""
    return f"{profile.model_label}\x1f{profile.tenant_tier}\x1f{profile.surface_config_hash}"


class TenantScheduler:
    """Per-``target_profile`` mutual exclusion. Sentinel and native phases on the
    SAME profile serialize through one lock; different profiles are independent."""

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def lock_for(self, profile: TargetProfileKey) -> threading.Lock:
        token = profile_token(profile)
        with self._guard:
            return self._locks.setdefault(token, threading.Lock())

    def try_acquire(self, profile: TargetProfileKey) -> bool:
        """Non-blocking acquire — used by tests to prove exclusion."""
        return self.lock_for(profile).acquire(blocking=False)

    def release(self, profile: TargetProfileKey) -> None:
        self.lock_for(profile).release()

    @contextmanager
    def phase(self, profile: TargetProfileKey) -> Iterator[None]:
        """Hold the profile's tenant for one phase (native OR sentinel)."""
        lock = self.lock_for(profile)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()


# ---------------------------------------------------------------------------
# Native attack phase (Part D → B → C → A)
# ---------------------------------------------------------------------------


@dataclass
class NativePhaseResult:
    observed: int = 0
    updated: int = 0
    requeued: list[Any] = field(default_factory=list)
    invalid_technical: int = 0
    dirty_teardowns: int = 0


def _tenant_residual(observer: WorkspaceObserver, baseline: TenantState) -> bool:
    """True iff the tenant shows a non-empty delta vs ``baseline`` (verify-clean)."""
    after = observer.snapshot()
    return diff_observation(baseline, after).has_effect()


async def run_native_phase(
    scheduler: TenantScheduler,
    profile: TargetProfileKey,
    archive: Archive,
    variants: Sequence[Any],
    *,
    deliver_and_score: NativeDeliverScore,
    fixture: FixtureController,
    observer: WorkspaceObserver,
    materializer: Any | None = None,
    node_of: NodeOf | None = None,
    resolution: Resolution = "native",
) -> NativePhaseResult:
    """Own the tenant-baseline bracket for the native path (the single reset of
    record). Per variant:

        setup_fixture → baseline snapshot → materializer.apply (stage) →
        deliver+observe+score → materializer.cleanup (existing carrier teardown) →
        teardown_fixture (Part B) → VERIFY the tenant is back at baseline.

    A variant that cannot baseline against a clean tenant, or whose delivery
    raises, is classified ``invalid_technical`` via :func:`run_outcome` and
    re-queued — never scored, never updating a posterior. The teardown bracket is
    ``finally``-guaranteed. The whole phase holds the profile lock, so a native
    teardown never overlaps a sentinel baseline on the same tenant."""
    result = NativePhaseResult(observed=len(variants))
    with scheduler.phase(profile):
        for variant in variants:
            # 1. Reset owner = the loop. A tenant that won't come clean can't
            #    baseline: classify invalid_technical via run_outcome() and re-queue.
            setup_fixture(reset=fixture.reset)
            if not fixture.is_clean():
                if run_outcome(None) == "invalid_technical":  # the dirty-tenant rule
                    result.requeued.append(variant)
                    result.invalid_technical += 1
                continue
            baseline = snapshot_baseline(observer)

            materialized: Any = None
            delivered: tuple[ScoreResult, SideEffectObservation, Objective] | None = None
            try:
                if materializer is not None:
                    node = node_of(variant) if node_of is not None else _default_node(variant)
                    materialized = await _maybe_await(materializer.apply(node))
                delivered = await _maybe_await(deliver_and_score(variant))
            except Exception:  # delivery/staging failed → not a scored result
                delivered = None
            finally:
                # 2. Existing carrier teardown, then Part B teardown WRAPS + resets.
                if materializer is not None:
                    await _maybe_await(materializer.cleanup(materialized))
                teardown_fixture(reset=fixture.reset)

            # 3. Verify-clean: the planted carrier is gone and the tenant is at baseline.
            if _tenant_residual(observer, baseline) or not fixture.is_clean():
                result.dirty_teardowns += 1  # next variant's setup_fixture will invalidate it

            if delivered is None:
                result.requeued.append(variant)  # raised → cannot score
                result.invalid_technical += 1
                continue

            score, observation, objective = delivered
            outcome = coherent_run_outcome(score, observation, objective)
            if outcome == "invalid_technical":
                result.requeued.append(variant)
                result.invalid_technical += 1
                continue
            effect = archive_observe(
                archive, variant.signature, score, observation, objective, resolution=resolution
            )
            if effect.effect in ("updated_success", "updated_failure"):
                result.updated += 1
    return result


def _default_node(variant: Any) -> dict[str, Any]:
    return {"id": getattr(variant, "id", "n0"), "mutation": getattr(variant, "mutation", {})}


# ---------------------------------------------------------------------------
# Sentinel phase (Part E) — under the same lock, never concurrent with native
# ---------------------------------------------------------------------------


def run_sentinel_phase(
    scheduler: TenantScheduler,
    profile: TargetProfileKey,
    sentinels: Sequence[Sentinel],
    *,
    fixture: FixtureController,
    deliver_and_score: DeliverScore,
) -> list[SentinelObservation]:
    """Run one sentinel cycle under the profile lock (mutually exclusive with
    native runs on this tenant)."""
    with scheduler.phase(profile):
        return run_sentinel_cycle(sentinels, fixture=fixture, deliver_and_score=deliver_and_score)


# ---------------------------------------------------------------------------
# Drift evaluation + persistence (store Part E's output for the dashboard)
# ---------------------------------------------------------------------------


def evaluate_and_record_alerts(
    session: Session,
    profile: TargetProfileKey,
    series_by_cell: Mapping[CellKey, Sequence[bool]],
    *,
    thresholds: DriftThresholds,
    baseline_rate: float = 0.0,
    source_by_cell: Mapping[CellKey, str] | None = None,
    cycle_ts: datetime | None = None,
) -> list[str]:
    """Run CUSUM per cell over the sentinel series and persist the verdict.

    Every alert row records the guards ``source`` label and the detector's
    ``calibrated`` flag, so the dashboard can suppress uncalibrated detectors and
    never render a non-target source as a target regression. Uses only Part E
    functions — no new measurement logic. Returns the ids written."""
    from advtest_ext.archive.fingerprint import encode_cell_key
    from advtest_ext.storage.models import TargetDriftAlert

    ts = cycle_ts or datetime.now(UTC)
    source_by_cell = source_by_cell or {}
    written: list[str] = []
    for cell_key, series in series_by_cell.items():
        cp = cusum(series, baseline_rate=baseline_rate, threshold=thresholds.cusum_h, slack=thresholds.cusum_slack)
        if not cp.detected:
            continue
        row_id = uuid.uuid4().hex
        session.add(
            TargetDriftAlert(
                id=row_id,
                model_label=profile.model_label,
                tenant_tier=profile.tenant_tier,
                surface_config_hash=profile.surface_config_hash,
                cell_key=encode_cell_key(cell_key),
                cycle_ts=ts,
                direction=cp.direction,
                magnitude=cp.statistic,
                source=source_by_cell.get(cell_key, "target"),
                detector_calibrated=alerts_enabled(thresholds),
                note="" if alerts_enabled(thresholds) else "detector not calibrated — alerts suppressed",
            )
        )
        written.append(row_id)
    session.flush()
    return written


def run_judge_gate(gold_before: Mapping[str, str], gold_after: Mapping[str, str]) -> JudgeDrift:
    """Re-score the frozen judge gold set (Part E judge guard)."""
    return judge_drift(gold_before, gold_after)


# ---------------------------------------------------------------------------
# Version change — decayed carry-forward + regression-hunt prioritisation
# ---------------------------------------------------------------------------


@dataclass
class VersionTransition:
    new_profile: TargetProfileKey
    regression_hunt_order: list[CellKey]
    seeded_cells: int


def on_version_change(
    old_archive: Archive,
    new_profile: TargetProfileKey,
    *,
    decay: float = 0.4,
    robust_threshold: float = 0.2,
) -> tuple[Archive, VersionTransition]:
    """Carry the old version's belief forward (decayed, undecayed reference kept)
    and prioritise regression-hunting previously-robust cells before broad
    exploration."""
    from advtest_ext.archive.fingerprint import Archive as ArchiveCls
    from advtest_ext.archive.fingerprint import Cell

    new_archive = ArchiveCls(profile=new_profile)
    robust: list[tuple[float, CellKey]] = []
    for cell_key, cell in old_archive.cells.items():
        seeded = BetaPosterior.seed_from_previous_version(cell.posterior, decay=decay)
        new_archive.cells[cell_key] = Cell(key=cell_key, posterior=seeded, resolution=cell.resolution)
        if cell.posterior.mean() <= robust_threshold:
            robust.append((cell.posterior.mean(), cell_key))
    robust.sort()  # most robust (lowest success) first — most informative to re-probe
    transition = VersionTransition(
        new_profile=new_profile,
        regression_hunt_order=[key for _, key in robust],
        seeded_cells=len(new_archive.cells),
    )
    return new_archive, transition


__all__ = [
    "NativeDeliverScore",
    "NativePhaseResult",
    "TenantScheduler",
    "VersionTransition",
    "evaluate_and_record_alerts",
    "on_version_change",
    "profile_token",
    "run_judge_gate",
    "run_native_phase",
    "run_sentinel_phase",
]
