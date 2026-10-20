"""In-memory archive, fingerprint snapshots, and their persistence.

* :class:`Archive` — the live MAP-Elites grid for one target profile: a Beta
  posterior per cell (keyed by the seven-field cell key), per-cell individuals,
  and the judge-only sink. All observation gating lives in
  :func:`advtest_ext.archive.posterior.apply_observation`.
* :class:`Fingerprint` — an immutable per-cell ``{mean, ess, resolution}``
  snapshot of an archive, taken for drift comparison; round-trips through the
  ``fingerprint_snapshots`` table.

Persistence is additive-tables only (``create_all`` /
``_ensure_additive_columns``); no Alembic.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from advtest_ext.archive.posterior import (
    BetaPosterior,
    Observation,
    Resolution,
    apply_observation,
)
from advtest_ext.archive.signature import Signature
from advtest_ext.core.orch_trace_meta import RunOutcome

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

CellKey = tuple[str, str, str, str, str, str, str]

_CELL_KEY_SEP = "\x1f"
_RESOLUTION_RANK: dict[Resolution, int] = {"coarse": 0, "medium": 1, "native": 2}


def encode_cell_key(key: CellKey) -> str:
    return _CELL_KEY_SEP.join(key)


def decode_cell_key(encoded: str) -> CellKey:
    parts = encoded.split(_CELL_KEY_SEP)
    if len(parts) != 7:
        raise ValueError(f"cell key must have 7 fields; got {len(parts)}")
    return (parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6])


def _best_resolution(current: Resolution, incoming: Resolution) -> Resolution:
    return incoming if _RESOLUTION_RANK[incoming] > _RESOLUTION_RANK[current] else current


@dataclass
class TargetProfileKey:
    """The composite, operator-declared drift unit (three separate facets)."""

    model_label: str
    tenant_tier: str
    surface_config_hash: str

    def as_tuple(self) -> tuple[str, str, str]:
        return (self.model_label, self.tenant_tier, self.surface_config_hash)


@dataclass
class IndividualStat:
    individual_id: str
    wording: str
    n_obs: float = 0.0
    n_success: float = 0.0


@dataclass
class Cell:
    """One MAP-Elites cell: its posterior, best resolution seen, and individuals."""

    key: CellKey
    posterior: BetaPosterior = field(default_factory=BetaPosterior)
    resolution: Resolution = "coarse"
    individuals: dict[tuple[str, str], IndividualStat] = field(default_factory=dict)


@dataclass
class JudgeOnlyRecord:
    cell_key: CellKey
    individual_id: str
    judge_confidence: float
    resolution: Resolution
    trace_id: str | None = None


@dataclass
class Archive:
    """The live grid for one target profile."""

    profile: TargetProfileKey
    cells: dict[CellKey, Cell] = field(default_factory=dict)
    judge_only: list[JudgeOnlyRecord] = field(default_factory=list)

    def cell(self, key: CellKey) -> Cell:
        existing = self.cells.get(key)
        if existing is None:
            existing = Cell(key=key)
            self.cells[key] = existing
        return existing

    def observe(
        self,
        sig: Signature,
        *,
        outcome: RunOutcome,
        signal_verified: bool,
        resolution: Resolution = "native",
        judge_confidence: float = 0.0,
        confidence: float = 1.0,
        trace_id: str | None = None,
    ) -> Observation:
        """Route one scored leaf into its cell. Enforces every gate via
        :func:`apply_observation`; a judge-only success is banked in
        :attr:`judge_only` and never touches the posterior."""
        key = sig.cell_key()
        cell = self.cell(key)
        result = apply_observation(
            cell.posterior,
            outcome=outcome,
            signal_verified=signal_verified,
            resolution=resolution,
            confidence=confidence,
        )
        if result.effect == "judge_only":
            self.judge_only.append(
                JudgeOnlyRecord(
                    cell_key=key,
                    individual_id=sig.individual_id,
                    judge_confidence=judge_confidence,
                    resolution=resolution,
                    trace_id=trace_id,
                )
            )
            return result
        if result.effect == "skipped":
            return result
        # A real Bernoulli observation landed: bump resolution + individual stats.
        cell.resolution = _best_resolution(cell.resolution, resolution)
        ind_key = (sig.individual_id, sig.wording)
        stat = cell.individuals.get(ind_key)
        if stat is None:
            stat = IndividualStat(individual_id=sig.individual_id, wording=sig.wording)
            cell.individuals[ind_key] = stat
        stat.n_obs += 1.0
        if result.effect == "updated_success":
            stat.n_success += 1.0
        return result


# ---------------------------------------------------------------------------
# Fingerprint snapshot
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CellStat:
    """A frozen per-cell readout for drift comparison."""

    cell_key: CellKey
    mean: float
    ess: float
    resolution: Resolution


@dataclass
class Fingerprint:
    """An immutable snapshot of an archive: per-cell ``{mean, ess, resolution}``."""

    profile: TargetProfileKey
    taken_at: datetime
    cells: dict[CellKey, CellStat] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "profile": list(self.profile.as_tuple()),
            "taken_at": self.taken_at.isoformat(),
            "cells": {
                encode_cell_key(key): {
                    "mean": stat.mean,
                    "ess": stat.ess,
                    "resolution": stat.resolution,
                }
                for key, stat in self.cells.items()
            },
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Fingerprint:
        profile_fields = payload["profile"]
        cells: dict[CellKey, CellStat] = {}
        for encoded, stat in payload.get("cells", {}).items():
            key = decode_cell_key(encoded)
            cells[key] = CellStat(
                cell_key=key,
                mean=float(stat["mean"]),
                ess=float(stat["ess"]),
                resolution=stat["resolution"],
            )
        return cls(
            profile=TargetProfileKey(*profile_fields),
            taken_at=datetime.fromisoformat(payload["taken_at"]),
            cells=cells,
        )


def snapshot(archive: Archive, *, taken_at: datetime) -> Fingerprint:
    """Freeze ``archive`` into a :class:`Fingerprint` for drift comparison."""
    cells = {
        key: CellStat(
            cell_key=key,
            mean=cell.posterior.mean(),
            ess=cell.posterior.effective_sample_size(),
            resolution=cell.resolution,
        )
        for key, cell in archive.cells.items()
    }
    return Fingerprint(profile=archive.profile, taken_at=taken_at, cells=cells)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def persist_archive(session: Session, archive: Archive) -> None:
    """Upsert an archive's profile, cells, individuals, and judge-only rows."""
    from advtest_ext.storage.models import (
        JudgeOnlySignal,
        TargetFingerprintCell,
        TargetFingerprintIndividual,
        TargetProfile,
    )

    p = archive.profile
    profile_row = session.get(TargetProfile, p.as_tuple())
    if profile_row is None:
        session.add(
            TargetProfile(
                model_label=p.model_label,
                tenant_tier=p.tenant_tier,
                surface_config_hash=p.surface_config_hash,
            )
        )
    for key, cell in archive.cells.items():
        encoded = encode_cell_key(key)
        row = (
            session.query(TargetFingerprintCell)
            .filter_by(
                model_label=p.model_label,
                tenant_tier=p.tenant_tier,
                surface_config_hash=p.surface_config_hash,
                cell_key=encoded,
            )
            .one_or_none()
        )
        if row is None:
            row = TargetFingerprintCell(
                id=uuid.uuid4().hex,
                model_label=p.model_label,
                tenant_tier=p.tenant_tier,
                surface_config_hash=p.surface_config_hash,
                cell_key=encoded,
                objective_class=key[0],
                framing=key[1],
                turn_order=key[2],
                persona=key[3],
                carrier_placement=key[4],
                escalation_speed=key[5],
                cross_app_hops_bucket=key[6],
            )
            session.add(row)
        row.alpha = cell.posterior.alpha
        row.beta = cell.posterior.beta
        row.n_success = cell.posterior.n_success
        row.n_failure = cell.posterior.n_failure
        row.provenance_resolution = cell.resolution
        if cell.posterior.prev_posterior_ref is not None:
            row.prev_alpha, row.prev_beta = cell.posterior.prev_posterior_ref
        for (ind_id, wording), stat in cell.individuals.items():
            ind_row = (
                session.query(TargetFingerprintIndividual)
                .filter_by(cell_id=row.id, individual_id=ind_id, wording=wording)
                .one_or_none()
            )
            if ind_row is None:
                ind_row = TargetFingerprintIndividual(
                    id=uuid.uuid4().hex,
                    cell_id=row.id,
                    individual_id=ind_id,
                    wording=wording,
                )
                session.add(ind_row)
            ind_row.n_obs = stat.n_obs
            ind_row.n_success = stat.n_success
    for jo in archive.judge_only:
        session.add(
            JudgeOnlySignal(
                id=uuid.uuid4().hex,
                trace_id=jo.trace_id,
                cell_key=encode_cell_key(jo.cell_key),
                individual_id=jo.individual_id,
                judge_confidence=jo.judge_confidence,
                resolution=jo.resolution,
            )
        )
    session.flush()


def store_snapshot(session: Session, fingerprint: Fingerprint) -> str:
    """Persist a :class:`Fingerprint` to ``fingerprint_snapshots``; return its id."""
    from advtest_ext.storage.models import FingerprintSnapshot

    snap_id = uuid.uuid4().hex
    p = fingerprint.profile
    session.add(
        FingerprintSnapshot(
            id=snap_id,
            model_label=p.model_label,
            tenant_tier=p.tenant_tier,
            surface_config_hash=p.surface_config_hash,
            taken_at=fingerprint.taken_at,
            payload=fingerprint.to_payload(),
        )
    )
    session.flush()
    return snap_id


def load_snapshot(session: Session, snapshot_id: str) -> Fingerprint:
    """Reload a stored :class:`Fingerprint` by id."""
    from advtest_ext.storage.models import FingerprintSnapshot

    row = session.get(FingerprintSnapshot, snapshot_id)
    if row is None:
        raise KeyError(f"no fingerprint snapshot {snapshot_id!r}")
    return Fingerprint.from_payload(dict(row.payload))


__all__ = [
    "Archive",
    "Cell",
    "CellKey",
    "CellStat",
    "Fingerprint",
    "IndividualStat",
    "JudgeOnlyRecord",
    "TargetProfileKey",
    "decode_cell_key",
    "encode_cell_key",
    "load_snapshot",
    "persist_archive",
    "snapshot",
    "store_snapshot",
]
