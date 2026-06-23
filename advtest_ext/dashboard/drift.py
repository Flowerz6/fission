"""Drift aggregation for the dashboard Drift view.

Two kinds of drift, both read from ``Trace.extra_metadata['drift']`` (a
list of event dicts the attack + adapter layers record):

* **Attacker-goal drift** (``scope='attacker_goal'``) — the attacker LLM
  wandered off the injection mechanism mid-conversation. The per-turn
  intent check flags ``off_mechanism`` turns and ``pruned`` a node after
  consecutive drift. This is the differentiated, TAP-off-topic-prune story.
* **Platform/UI drift** (``scope='platform_ui'``) — the target's surface
  moved (panel didn't mount, suggestion modal blocked the composer) and
  the Replayer absorbed it in-Run (``recovered`` / ``dialog_cleared``).

Pure over the session; no I/O beyond SQLAlchemy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.storage.repository import list_runs

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session


@dataclass
class DriftEvent:
    """One drift event, flattened with its run/trace provenance."""

    run_id: str
    trace_id: str
    scope: str  # 'attacker_goal' | 'platform_ui'
    kind: str  # off_mechanism | pruned | recovered | dialog_cleared | ...
    detail: str
    node_id: str = ""
    turn: int | None = None
    started_at: Any = None


@dataclass
class DriftSummary:
    total: int = 0
    attacker_goal: int = 0
    platform_ui: int = 0
    pruned: int = 0  # nodes abandoned for goal drift
    recovered: int = 0  # platform drift absorbed in-Run
    by_kind: dict[str, int] = field(default_factory=dict)
    events: list[DriftEvent] = field(default_factory=list)
    runs_with_drift: int = 0


def _iter_drift(meta: Any) -> list[dict[str, Any]]:
    if not isinstance(meta, dict):
        return []
    ev = meta.get("drift")
    return [e for e in ev if isinstance(e, dict)] if isinstance(ev, list) else []


def build_drift_summary(session: Session, *, run_limit: int = 200) -> DriftSummary:
    """Aggregate every drift event across recent runs, newest first."""
    summary = DriftSummary()
    runs_seen: set[str] = set()
    for run in list_runs(session, limit=run_limit):
        for t in getattr(run, "traces", []) or []:
            for ev in _iter_drift(getattr(t, "extra_metadata", None)):
                scope = ev.get("scope", "")
                kind = ev.get("kind", "")
                tc = (
                    (t.extra_metadata or {}).get("tap_crescendo", {})
                    if isinstance(t.extra_metadata, dict)
                    else {}
                )
                de = DriftEvent(
                    run_id=run.id,
                    trace_id=t.id,
                    scope=scope
                    or (
                        "attacker_goal"
                        if kind in ("off_mechanism", "pruned", "goal_drift")
                        else "platform_ui"
                    ),
                    kind=kind,
                    detail=ev.get("detail", ""),
                    node_id=tc.get("node_id", "") if isinstance(tc, dict) else "",
                    turn=ev.get("turn"),
                    started_at=getattr(run, "started_at", None),
                )
                summary.events.append(de)
                summary.total += 1
                summary.by_kind[kind] = summary.by_kind.get(kind, 0) + 1
                if de.scope == "attacker_goal":
                    summary.attacker_goal += 1
                    if kind == "pruned":
                        summary.pruned += 1
                else:
                    summary.platform_ui += 1
                    if kind in (
                        "recovered",
                        "dialog_cleared",
                        "selector_fallback",
                        "capture_retry",
                    ):
                        summary.recovered += 1
                runs_seen.add(run.id)
    summary.runs_with_drift = len(runs_seen)
    # Newest events first for the table.
    summary.events.sort(key=lambda e: str(e.started_at) if e.started_at else "", reverse=True)
    return summary


__all__ = ["DriftEvent", "DriftSummary", "build_drift_summary"]
