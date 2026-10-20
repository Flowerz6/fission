"""Two-channel scorer protocol.

Every objective is scored on TWO INDEPENDENT channels:

* a deterministic **oracle** → ``signal_verified`` (the only thing that can move
  a posterior; NEVER set from the judge — Q6);
* an optional LLM **judge** → ``judge_success`` / ``judge_confidence`` (reported
  for calibration, never trusted to signal-verify).

``observation_path`` records which observation produced the evidence
(``ui`` = reply/DOM over the reused session, ``api`` = read-only accelerator,
``sink`` = exfil-sink reader, ``none`` = no evidence read).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Mapping

    from advtest_ext.observe.model import SideEffectObservation
    from advtest_ext.scoring.objectives import Objective

ObservationPath = Literal["ui", "api", "sink", "none"]


@dataclass
class ScoreResult:
    """The output of a scorer — two independent channels plus provenance."""

    signal_verified: bool
    """Deterministic oracle fired. The ONLY channel that may update a posterior."""
    judge_success: bool | None
    judge_confidence: float
    leak_fraction: float
    """For graded-leak objectives (fraction of ground truth revealed); 0.0 else."""
    refusal_style: str | None
    evidence: str
    observation_path: ObservationPath


# The LLM judge is a plain callable so tests can script it; it returns
# ``(judge_success, confidence)`` and NEVER sets ``signal_verified``.
class Judge(Protocol):
    def __call__(
        self, reply: str, objective: Objective, ground_truth: Mapping[str, Any]
    ) -> tuple[bool | None, float]: ...


@dataclass
class ScoringContext:
    """Per-run scoring inputs: the operator ground truth and an optional judge."""

    ground_truth: Mapping[str, Any] = field(default_factory=dict)
    judge: Judge | None = None

    def run_judge(self, reply: str, objective: Objective) -> tuple[bool | None, float]:
        if self.judge is None:
            return None, 0.0
        return self.judge(reply, objective, self.ground_truth)


class Scorer(Protocol):
    """Scores one reply+observation against one objective."""

    objective_class: str

    def score(
        self,
        reply: str,
        observation: SideEffectObservation,
        objective: Objective,
        ctx: ScoringContext,
    ) -> ScoreResult: ...


__all__ = [
    "Judge",
    "ObservationPath",
    "ScoreResult",
    "Scorer",
    "ScoringContext",
]
