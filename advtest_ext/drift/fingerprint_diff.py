"""Population-level fingerprint divergence between two target profiles.

Computes PSI (population stability index) and KL divergence between two
:class:`Fingerprint` snapshots, and — because the profile is a 3-tuple of named
columns — attributes WHICH facet changed (model vs tier vs surface_config). Cells
are only compared at COMPARABLE resolution (Part A provenance): a native cell is
never diffed against a coarse-backfilled prior as if equal; resolutions are
segregated and reported separately.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.archive.fingerprint import CellKey, Fingerprint, TargetProfileKey

_EPS = 1e-6
_DEFAULT_BINS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0001)


@dataclass(frozen=True)
class Divergence:
    resolution: str
    n_matched_cells: int
    psi: float
    kl: float


@dataclass
class FingerprintDiff:
    facets_changed: list[str] = field(default_factory=list)
    by_resolution: dict[str, Divergence] = field(default_factory=dict)


def facet_attribution(old: TargetProfileKey, new: TargetProfileKey) -> list[str]:
    """Which of the 3 profile facets differ (model_label / tenant_tier / surface_config_hash)."""
    changed: list[str] = []
    if old.model_label != new.model_label:
        changed.append("model_label")
    if old.tenant_tier != new.tenant_tier:
        changed.append("tenant_tier")
    if old.surface_config_hash != new.surface_config_hash:
        changed.append("surface_config_hash")
    return changed


def _histogram(values: list[float]) -> list[float]:
    counts = [0.0] * (len(_DEFAULT_BINS) - 1)
    for v in values:
        for b in range(len(_DEFAULT_BINS) - 1):
            if _DEFAULT_BINS[b] <= v < _DEFAULT_BINS[b + 1]:
                counts[b] += 1.0
                break
    total = sum(counts) or 1.0
    return [(c / total) + _EPS for c in counts]


def _psi(old_hist: list[float], new_hist: list[float]) -> float:
    return sum((n - o) * math.log(n / o) for o, n in zip(old_hist, new_hist, strict=True))


def _kl(old_hist: list[float], new_hist: list[float]) -> float:
    return sum(o * math.log(o / n) for o, n in zip(old_hist, new_hist, strict=True))


def _cells_at(fp: Fingerprint, resolution: str) -> dict[CellKey, float]:
    return {key: stat.mean for key, stat in fp.cells.items() if stat.resolution == resolution}


def fingerprint_divergence(
    old: Fingerprint,
    new: Fingerprint,
    *,
    resolutions: tuple[str, ...] = ("native", "medium", "coarse"),
) -> FingerprintDiff:
    """Divergence between two fingerprints, SEGREGATED by resolution.

    Only cells present in both fingerprints AT THE SAME resolution are compared;
    a native cell is never matched to a coarse prior.
    """
    diff = FingerprintDiff(facets_changed=facet_attribution(old.profile, new.profile))
    for resolution in resolutions:
        old_cells = _cells_at(old, resolution)
        new_cells = _cells_at(new, resolution)
        shared = old_cells.keys() & new_cells.keys()
        if not shared:
            continue
        old_hist = _histogram([old_cells[k] for k in shared])
        new_hist = _histogram([new_cells[k] for k in shared])
        diff.by_resolution[resolution] = Divergence(
            resolution=resolution,
            n_matched_cells=len(shared),
            psi=_psi(old_hist, new_hist),
            kl=_kl(old_hist, new_hist),
        )
    return diff


def regressed_cells(
    old: Fingerprint, new: Fingerprint, *, resolution: str = "native", min_increase: float = 0.2
) -> list[CellKey]:
    """Cells whose success mean rose by ``min_increase`` (more vulnerable), same
    resolution only."""
    old_cells = _cells_at(old, resolution)
    new_cells = _cells_at(new, resolution)
    out: list[CellKey] = []
    for key in old_cells.keys() & new_cells.keys():
        if new_cells[key] - old_cells[key] >= min_increase:
            out.append(key)
    return sorted(out)


__all__ = [
    "Divergence",
    "FingerprintDiff",
    "facet_attribution",
    "fingerprint_divergence",
    "regressed_cells",
]
