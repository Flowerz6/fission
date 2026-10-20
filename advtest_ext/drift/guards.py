"""Impostor-drift guards — rule out the three non-target causes.

A rise in signal-verified success is only TARGET drift if it isn't explained by:

* **Search drift** — the explorer collapsed (coverage / visitation-entropy /
  novelty-acceptance fell), so we simply stopped probing hard. A target-drift
  alert in a cell whose coverage collapsed is SUPPRESSED (relabelled
  ``source='search'``): you can't claim the target improved in cells you stopped
  probing.
* **Judge drift** — the judge's verdicts on a FROZEN gold set shifted, so the
  measurement instrument moved. Judge-only-derived leads are then marked suspect,
  never silently attributed to the target.
* **Tenant drift** — a sentinel ran on a fixture that wasn't cleanly reset; that
  observation is ``invalid_technical``, not drift (enforced in
  :mod:`advtest_ext.drift.sentinel`; :func:`tenant_guard_ok` re-checks it).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.archive.fingerprint import Archive, CellKey
    from advtest_ext.drift.sentinel import SentinelObservation
    from advtest_ext.mutate.novelty import NoveltyDecision

TARGET = "target"
SEARCH = "search"
JUDGE = "judge"


@dataclass
class DriftAlert:
    cell_key: CellKey
    magnitude: float
    direction: str
    source: str = TARGET  # target | search | judge
    note: str = ""


# --- SEARCH DRIFT ------------------------------------------------------------


@dataclass(frozen=True)
class SearchHealth:
    coverage: int
    visitation_entropy: float
    novelty_acceptance_rate: float
    collapsed: bool
    collapsed_cells: frozenset[CellKey]


def _entropy(counts: Sequence[float]) -> float:
    total = sum(counts)
    if total <= 0:
        return 0.0
    probs = [c / total for c in counts if c > 0]
    h = -sum(p * math.log(p) for p in probs)
    n = len([c for c in counts if c > 0])
    return h / math.log(n) if n > 1 else 0.0  # normalised [0,1]


def search_health(
    archive: Archive,
    *,
    prev_coverage: int | None = None,
    novelty_decisions: Sequence[NoveltyDecision] | None = None,
    prev_populated_cells: frozenset[CellKey] | None = None,
    coverage_drop_frac: float = 0.4,
    min_novelty_acceptance: float = 0.1,
) -> SearchHealth:
    """Measure explorer health and flag collapse.

    ``collapsed_cells`` are cells that were populated before but got no fresh
    observation this window (candidate suppression targets)."""
    counts = [
        max(cell.posterior.effective_sample_size() - 2.0, 0.0) for cell in archive.cells.values()
    ]
    coverage = len(archive.cells)
    entropy = _entropy(counts)
    if novelty_decisions:
        admitted = sum(1 for d in novelty_decisions if d.admit)
        nar = admitted / len(novelty_decisions)
    else:
        nar = 1.0
    coverage_collapse = prev_coverage is not None and coverage < prev_coverage * (
        1.0 - coverage_drop_frac
    )
    novelty_collapse = bool(novelty_decisions) and nar <= min_novelty_acceptance
    collapsed = coverage_collapse or novelty_collapse
    now_cells = frozenset(archive.cells.keys())
    collapsed_cells = (prev_populated_cells - now_cells) if prev_populated_cells else frozenset()
    return SearchHealth(
        coverage=coverage,
        visitation_entropy=entropy,
        novelty_acceptance_rate=nar,
        collapsed=collapsed,
        collapsed_cells=collapsed_cells,
    )


def suppress_search_drift(alerts: Sequence[DriftAlert], health: SearchHealth) -> list[DriftAlert]:
    """Relabel target-drift alerts in collapsed cells as ``source='search'`` — you
    cannot claim the target changed where you stopped probing."""
    out: list[DriftAlert] = []
    for alert in alerts:
        if alert.source == TARGET and (
            alert.cell_key in health.collapsed_cells or health.collapsed
        ):
            out.append(
                DriftAlert(
                    cell_key=alert.cell_key,
                    magnitude=alert.magnitude,
                    direction=alert.direction,
                    source=SEARCH,
                    note="suppressed: coincides with search-coverage collapse",
                )
            )
        else:
            out.append(alert)
    return out


# --- JUDGE DRIFT -------------------------------------------------------------


@dataclass(frozen=True)
class JudgeDrift:
    shifted: bool
    shift_rate: float
    changed_ids: tuple[str, ...]


def judge_drift(
    gold_before: Mapping[str, str], gold_after: Mapping[str, str], *, tolerance: float = 0.0
) -> JudgeDrift:
    """Compare judge verdicts on a FROZEN gold set across cycles (fixed inputs)."""
    shared = gold_before.keys() & gold_after.keys()
    changed = tuple(sorted(gid for gid in shared if gold_before[gid] != gold_after[gid]))
    rate = len(changed) / len(shared) if shared else 0.0
    return JudgeDrift(shifted=rate > tolerance, shift_rate=rate, changed_ids=changed)


@dataclass
class SuspectLead:
    cell_key: CellKey
    suspect: bool
    note: str


def mark_leads_suspect(lead_cell_keys: Sequence[CellKey], drift: JudgeDrift) -> list[SuspectLead]:
    """When judge drift fires, annotate judge-only leads as suspect — never
    silently attribute an oracle-only shift to the target."""
    note = (
        f"judge drift {drift.shift_rate:.0%} on frozen gold set — verify before trusting"
        if drift.shifted
        else ""
    )
    return [SuspectLead(cell_key=key, suspect=drift.shifted, note=note) for key in lead_cell_keys]


# --- TENANT DRIFT ------------------------------------------------------------


def tenant_guard_ok(observation: SentinelObservation) -> bool:
    """True iff the sentinel ran on a cleanly-reset fixture (else its outcome is
    ``invalid_technical``, not drift)."""
    return observation.fixture_clean and observation.effective_outcome != "invalid_technical"


__all__ = [
    "JUDGE",
    "SEARCH",
    "TARGET",
    "DriftAlert",
    "JudgeDrift",
    "SearchHealth",
    "SuspectLead",
    "judge_drift",
    "mark_leads_suspect",
    "search_health",
    "suppress_search_drift",
    "tenant_guard_ok",
]
