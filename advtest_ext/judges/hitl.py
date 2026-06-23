"""HITL (human-in-the-loop) queue for traces the ensemble can't agree on.

When :class:`~advtest_ext.judges.ensemble.JudgeEnsemble` flags
``hitl_required=True``, the orchestrator persists the trace +
ensemble result here. A human operator (eventually a dashboard form;
for now the test surface and any tooling consuming the directory)
resolves each pending item with a final verdict + notes.

On-disk layout::

    queue_dir/
      pending/
        <trace-id>.json   # trace + ensemble result + reason for HITL
      resolved/
        <trace-id>.json   # ditto + human verdict + notes + resolved_at

File-per-trace, JSON-only. No DB; the file format IS the contract,
which keeps the dashboard layer (Prompt 9) free to read directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from advtest_ext.core.models import AttackTrace, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.judges.ensemble import EnsembleResult


@dataclass
class HITLItem:
    """One queue entry: the trace + ensemble result + (when resolved)
    the human's verdict and notes."""

    trace: AttackTrace
    ensemble_aggregate_score: float
    ensemble_reasoning: str
    disagreement_spread: float
    enqueued_at: datetime
    reason: str = ""
    resolved_at: datetime | None = None
    human_verdict: Verdict | None = None
    human_notes: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "trace": json.loads(self.trace.model_dump_json()),
            "ensemble_aggregate_score": self.ensemble_aggregate_score,
            "ensemble_reasoning": self.ensemble_reasoning,
            "disagreement_spread": self.disagreement_spread,
            "enqueued_at": self.enqueued_at.isoformat(),
            "reason": self.reason,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "human_verdict": self.human_verdict,
            "human_notes": self.human_notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> HITLItem:
        return cls(
            trace=AttackTrace.model_validate(data["trace"]),
            ensemble_aggregate_score=float(data["ensemble_aggregate_score"]),  # type: ignore[arg-type]
            ensemble_reasoning=str(data.get("ensemble_reasoning") or ""),
            disagreement_spread=float(data["disagreement_spread"]),  # type: ignore[arg-type]
            enqueued_at=datetime.fromisoformat(str(data["enqueued_at"])),
            reason=str(data.get("reason") or ""),
            resolved_at=(
                datetime.fromisoformat(str(data["resolved_at"]))
                if data.get("resolved_at")
                else None
            ),
            human_verdict=data.get("human_verdict"),  # type: ignore[arg-type]
            human_notes=str(data.get("human_notes") or ""),
        )


@dataclass
class HITLQueue:
    """File-backed pending/resolved queue."""

    root: Path
    _pending_dir: Path = field(init=False)
    _resolved_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self._pending_dir = self.root / "pending"
        self._resolved_dir = self.root / "resolved"
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        self._resolved_dir.mkdir(parents=True, exist_ok=True)

    def enqueue(
        self,
        *,
        trace: AttackTrace,
        ensemble_result: EnsembleResult,
        reason: str = "",
    ) -> Path:
        """Persist ``trace`` + ``ensemble_result`` to the pending dir.
        Returns the path of the written file."""
        item = HITLItem(
            trace=trace,
            ensemble_aggregate_score=ensemble_result.aggregate_score,
            ensemble_reasoning=ensemble_result.reasoning,
            disagreement_spread=ensemble_result.disagreement_spread,
            enqueued_at=datetime.now(UTC),
            reason=reason or ensemble_result.reasoning,
        )
        path = self._pending_dir / f"{trace.id}.json"
        path.write_text(json.dumps(item.to_dict(), indent=2, default=str))
        return path

    def list_pending(self) -> list[HITLItem]:
        """List currently-pending items, sorted by enqueue time."""
        items: list[HITLItem] = []
        for p in sorted(self._pending_dir.glob("*.json")):
            items.append(HITLItem.from_dict(json.loads(p.read_text())))
        return items

    def list_resolved(self) -> list[HITLItem]:
        items: list[HITLItem] = []
        for p in sorted(self._resolved_dir.glob("*.json")):
            items.append(HITLItem.from_dict(json.loads(p.read_text())))
        return items

    def resolve(
        self,
        *,
        trace_id: str,
        human_verdict: Verdict,
        human_notes: str = "",
    ) -> Path:
        """Move a pending item to the resolved dir with the human's
        verdict + notes attached. Returns the resolved file path."""
        pending_path = self._pending_dir / f"{trace_id}.json"
        if not pending_path.exists():
            raise KeyError(f"no pending HITL item for trace {trace_id!r}")
        item = HITLItem.from_dict(json.loads(pending_path.read_text()))
        item.resolved_at = datetime.now(UTC)
        item.human_verdict = human_verdict
        item.human_notes = human_notes
        resolved_path = self._resolved_dir / f"{trace_id}.json"
        resolved_path.write_text(json.dumps(item.to_dict(), indent=2, default=str))
        pending_path.unlink()
        return resolved_path

    def __len__(self) -> int:
        return sum(1 for _ in self._pending_dir.glob("*.json"))


__all__ = ["HITLItem", "HITLQueue"]
