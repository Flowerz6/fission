"""Score each guide mutation cell by how it actually performed in real runs.

This is the "keep the winners" gate. The cell generator proposes many cells
(some good, some junk); rather than have a human approve each, we run them and
judge them by evidence: a cell that shows up in verified-success trajectories is
a keeper, a cell tried repeatedly with no success is a prune candidate.

Attribution: every TAP trace records the cell it used in
``extra_metadata['tap_crescendo']['mutation']`` (operator_name + feature_value).
A trace is a *verified* win when its verdict is success AND a deterministic
(non-``llm_judge_verdict``) signal fired — the same bar the harvest and the
authenticity check use, so the over-calling LLM oracle can't inflate a cell.

Pure functions plus a DB adapter (:func:`cell_performance_for_runs`), like
``authenticity.py`` — reads persisted traces, runs nothing live.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Literal

CellStatus = Literal["performer", "unverified", "underperformer", "inconclusive", "untried"]


@dataclass
class CellUsage:
    """One trace's use of one cell."""

    operator: str
    value: str
    success: bool  # verdict == success (oracle, may over-call)
    verified: bool  # a deterministic signal fired (ground truth)


@dataclass
class CellStat:
    operator: str
    value: str
    tried: int = 0
    wins_success: int = 0  # appearances in an oracle-success trace
    wins_verified: int = 0  # appearances in a signal-verified trace
    status: CellStatus = "untried"

    def to_dict(self) -> dict[str, Any]:
        return {
            "operator": self.operator,
            "value": self.value,
            "tried": self.tried,
            "wins_success": self.wins_success,
            "wins_verified": self.wins_verified,
            "status": self.status,
        }


@dataclass
class CellPerformanceReport:
    guide_id: str
    n_runs: int
    stats: list[CellStat] = field(default_factory=list)

    @property
    def promote(self) -> list[CellStat]:
        """Cells backed by at least one verified win — keep these."""
        return [s for s in self.stats if s.status == "performer"]

    @property
    def prune(self) -> list[CellStat]:
        """Cells tried enough times with no success — drop candidates."""
        return [s for s in self.stats if s.status == "underperformer"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "guide_id": self.guide_id,
            "n_runs": self.n_runs,
            "stats": [s.to_dict() for s in self.stats],
            "promote": [s.value for s in self.promote],
            "prune": [s.value for s in self.prune],
        }


def _norm(value: str) -> str:
    return " ".join(str(value).lower().split()).strip()


def _classify(stat: CellStat, *, min_attempts_to_prune: int) -> CellStatus:
    if stat.tried == 0:
        return "untried"
    if stat.wins_verified >= 1:
        return "performer"
    if stat.wins_success >= 1:
        return "unverified"  # oracle liked it but no ground-truth signal — treat with suspicion
    if stat.tried >= min_attempts_to_prune:
        return "underperformer"
    return "inconclusive"  # tried, no win, but not enough attempts to judge


def assess_cell_performance(
    guide_id: str,
    usages: list[CellUsage],
    authored: dict[str, list[str]],
    *,
    n_runs: int,
    min_attempts_to_prune: int = 3,
) -> CellPerformanceReport:
    """Tally per-cell usage and classify each authored cell.

    ``authored`` maps operator name -> its cell_values (so untried authored cells
    are still reported). Usages for cells not in ``authored`` are still counted
    (e.g. runtime-only cells), appended after the authored ones.
    """
    by_key: dict[tuple[str, str], CellStat] = {}
    order: list[tuple[str, str]] = []

    def _get(operator: str, value: str) -> CellStat:
        key = (operator, _norm(value))
        if key not in by_key:
            by_key[key] = CellStat(operator=operator, value=value)
            order.append(key)
        return by_key[key]

    # Seed the archive with every authored cell so untried ones surface.
    for operator, values in authored.items():
        for value in values:
            _get(operator, value)

    for u in usages:
        stat = _get(u.operator, u.value)
        stat.tried += 1
        if u.success:
            stat.wins_success += 1
        if u.verified:
            stat.wins_verified += 1

    stats: list[CellStat] = []
    for key in order:
        stat = by_key[key]
        stat.status = _classify(stat, min_attempts_to_prune=min_attempts_to_prune)
        stats.append(stat)
    return CellPerformanceReport(guide_id=guide_id, n_runs=n_runs, stats=stats)


def cell_performance_for_runs(
    session_factory: Any,
    guide: Any,
    run_ids: list[str],
    *,
    min_attempts_to_prune: int = 3,
) -> CellPerformanceReport:
    """Build usages from persisted traces of a guide's runs and assess them.

    Reads each trace's cell from ``extra_metadata['tap_crescendo']['mutation']``
    and its verified status from the deterministic signals on the trace.
    """
    from advtest_ext.storage import list_signals_for_trace, list_traces_for_run, session_scope

    authored = {op.name: list(op.cell_values) for op in guide.mutation_operators}
    usages: list[CellUsage] = []
    with session_scope(session_factory) as s:
        for run_id in run_ids:
            for trace in list_traces_for_run(s, run_id):
                meta = (trace.extra_metadata or {}).get("tap_crescendo", {})
                mutation = meta.get("mutation") or {}
                value = mutation.get("feature_value")
                operator = mutation.get("operator_name")
                if not value or not operator:
                    continue  # control / seed / non-mutated node
                success = (trace.verdict or "").lower() == "success"
                verified = success and any(
                    sig.fired and sig.kind != "llm_judge_verdict"
                    for sig in list_signals_for_trace(s, trace.id)
                )
                usages.append(
                    CellUsage(
                        operator=str(operator),
                        value=str(value),
                        success=success,
                        verified=verified,
                    )
                )
    return assess_cell_performance(
        guide.id,
        usages,
        authored,
        n_runs=len(run_ids),
        min_attempts_to_prune=min_attempts_to_prune,
    )


def prune_from_guide_dict(raw: dict[str, Any], report: CellPerformanceReport) -> dict[str, Any]:
    """Remove underperforming cells from a raw guide dict. Never removes the last
    cell of an operator (an operator must keep at least one value). Mutates and
    returns ``raw``. Only prunes cells classed ``underperformer``."""
    prune_by_op: dict[str, set[str]] = defaultdict(set)
    for stat in report.prune:
        prune_by_op[stat.operator].add(_norm(stat.value))
    for op in raw.get("mutation_operators", []):
        drop = prune_by_op.get(op.get("name"))
        if not drop:
            continue
        cells = op.get("cell_values", [])
        kept = [c for c in cells if _norm(c) not in drop]
        if kept:  # never strip an operator down to nothing
            op["cell_values"] = kept
    return raw


__all__ = [
    "CellPerformanceReport",
    "CellStat",
    "CellStatus",
    "CellUsage",
    "assess_cell_performance",
    "cell_performance_for_runs",
    "prune_from_guide_dict",
]
