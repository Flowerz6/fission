"""Frozen sentinel panel — the control group.

One canonical, never-mutated attack (a fixed ``content_hash``) per populated
cell, chosen once and frozen. Each cycle: reset the fixture, deliver (UI-only),
observe (Part B), score (Part C), reset again — PER SENTINEL, so accumulated
tenant state can never read as drift. Because the sentinel never changes, any
movement in its outcomes is attributable to the TARGET, not search/judge/tenant.
Only signal-verified outcomes on non-``invalid_technical`` runs count.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

from advtest_ext.observe.baseline import setup_fixture, teardown_fixture

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from advtest_ext.archive.fingerprint import Archive, CellKey, TargetProfileKey

# (signal_verified, run_outcome) for one delivered+scored sentinel.
DeliverScore = Callable[["Sentinel"], tuple[bool, str]]


class FixtureController(Protocol):
    """A resettable test fixture. ``reset`` matches Part B's reset-hook shape;
    ``is_clean`` reports whether the reset actually produced a clean tenant."""

    def reset(self) -> None: ...
    def is_clean(self) -> bool: ...


@dataclass(frozen=True)
class Sentinel:
    cell_key: CellKey
    content_hash: str


def select_sentinels(
    archive: Archive, *, existing: dict[CellKey, str] | None = None
) -> list[Sentinel]:
    """Pick one frozen canonical attack per populated cell (stable across cycles).

    A cell's sentinel is preserved from ``existing`` when present; otherwise the
    lexicographically smallest ``content_hash`` is chosen deterministically so the
    selection is stable if re-run.
    """
    existing = existing or {}
    sentinels: list[Sentinel] = []
    for cell_key, cell in archive.cells.items():
        if not cell.individuals:
            continue
        content_hash = existing.get(cell_key) or min(
            ind.individual_id for ind in cell.individuals.values()
        )
        sentinels.append(Sentinel(cell_key=cell_key, content_hash=content_hash))
    return sorted(sentinels, key=lambda s: s.cell_key)


@dataclass
class SentinelObservation:
    cell_key: CellKey
    content_hash: str
    signal_verified: bool
    run_outcome: str
    fixture_clean: bool

    @property
    def effective_outcome(self) -> str:
        """Tenant guard: a sentinel that ran on a dirty fixture is a technical
        non-measurement, NOT drift."""
        return self.run_outcome if self.fixture_clean else "invalid_technical"

    @property
    def counts(self) -> bool:
        """A valid, signal-verified measurement (the only kind drift may use)."""
        return (
            self.fixture_clean
            and self.effective_outcome != "invalid_technical"
            and self.signal_verified
        )


def run_sentinel_cycle(
    sentinels: Sequence[Sentinel],
    *,
    fixture: FixtureController,
    deliver_and_score: DeliverScore,
) -> list[SentinelObservation]:
    """Run one sentinel cycle, resetting the fixture PER sentinel."""
    observations: list[SentinelObservation] = []
    for sentinel in sentinels:
        setup_fixture(reset=fixture.reset)  # Part B reset hook — per sentinel
        clean = fixture.is_clean()
        if clean:
            signal_verified, run_outcome = deliver_and_score(sentinel)
        else:
            signal_verified, run_outcome = False, "invalid_technical"  # tenant guard
        observations.append(
            SentinelObservation(
                cell_key=sentinel.cell_key,
                content_hash=sentinel.content_hash,
                signal_verified=signal_verified,
                run_outcome=run_outcome,
                fixture_clean=clean,
            )
        )
        teardown_fixture(reset=fixture.reset)
    return observations


def sentinel_series(
    cycles: Iterable[Sequence[SentinelObservation]], cell_key: CellKey
) -> list[bool]:
    """The per-cell signal-verified series across cycles (valid measurements only)."""
    series: list[bool] = []
    for cycle in cycles:
        for obs in cycle:
            if obs.cell_key == cell_key and obs.effective_outcome != "invalid_technical":
                series.append(obs.signal_verified)
    return series


# ---------------------------------------------------------------------------
# Persistence (target_drift namespace)
# ---------------------------------------------------------------------------


def store_sentinels(
    session: Session, profile: TargetProfileKey, sentinels: Sequence[Sentinel]
) -> None:
    from advtest_ext.archive.fingerprint import encode_cell_key
    from advtest_ext.storage.models import TargetDriftSentinel

    for sentinel in sentinels:
        session.add(
            TargetDriftSentinel(
                id=uuid.uuid4().hex,
                model_label=profile.model_label,
                tenant_tier=profile.tenant_tier,
                surface_config_hash=profile.surface_config_hash,
                cell_key=encode_cell_key(sentinel.cell_key),
                content_hash=sentinel.content_hash,
            )
        )
    session.flush()


def store_sentinel_observations(
    session: Session,
    profile: TargetProfileKey,
    observations: Sequence[SentinelObservation],
    *,
    cycle_ts: datetime | None = None,
) -> None:
    from advtest_ext.archive.fingerprint import encode_cell_key
    from advtest_ext.storage.models import TargetDriftSentinelObservation

    ts = cycle_ts or datetime.now(UTC)
    for obs in observations:
        session.add(
            TargetDriftSentinelObservation(
                id=uuid.uuid4().hex,
                model_label=profile.model_label,
                tenant_tier=profile.tenant_tier,
                surface_config_hash=profile.surface_config_hash,
                cell_key=encode_cell_key(obs.cell_key),
                content_hash=obs.content_hash,
                cycle_ts=ts,
                signal_verified=obs.signal_verified,
                run_outcome=obs.effective_outcome,
                fixture_clean=obs.fixture_clean,
            )
        )
    session.flush()


__all__ = [
    "DeliverScore",
    "FixtureController",
    "Sentinel",
    "SentinelObservation",
    "run_sentinel_cycle",
    "select_sentinels",
    "sentinel_series",
    "store_sentinel_observations",
    "store_sentinels",
]
