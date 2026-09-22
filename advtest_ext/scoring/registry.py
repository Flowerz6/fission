"""Scorer registry + the pre-run loader validation.

Routes an ``objective_class`` (from the sub-step-0 table) to its scorer, and — the
load-bearing guard — validates BEFORE a run that every operator objective carries
its required ground-truth keys AND declares the observation channels its oracle
needs. A run that can't be scored honestly is refused, not silently mis-scored.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from advtest_ext.scoring.base import Scorer
from advtest_ext.scoring.impl import ALL_SCORERS
from advtest_ext.scoring.objectives import OBJECTIVES, ObjectiveClass

_SCORERS: dict[str, Scorer] = {s.objective_class: s for s in ALL_SCORERS}


class ScoringConfigError(ValueError):
    """A run's scoring config is invalid — refuse to start."""


def scorer_for(objective_class: str) -> Scorer:
    try:
        return _SCORERS[objective_class]
    except KeyError as exc:
        raise ScoringConfigError(f"no scorer registered for objective_class {objective_class!r}") from exc


@dataclass
class OperatorObjective:
    """One objective an operator declares for a run."""

    objective_class: ObjectiveClass
    ground_truth: Mapping[str, Any] = field(default_factory=dict)
    declared_channels: tuple[str, ...] = ()


def validate_run_objectives(objectives: Sequence[OperatorObjective]) -> None:
    """Refuse the run unless every objective is scorable as declared.

    For each objective: the class must be known, every required ground-truth key
    present, and every required observation channel declared. Raises
    :class:`ScoringConfigError` on the first violation.
    """
    for obj in objectives:
        if obj.objective_class not in OBJECTIVES:
            raise ScoringConfigError(f"unknown objective_class {obj.objective_class!r}")
        spec = OBJECTIVES[obj.objective_class]
        missing_gt = [k for k in spec.required_ground_truth if k not in obj.ground_truth]
        if missing_gt:
            raise ScoringConfigError(
                f"objective {obj.objective_class!r}: missing ground_truth key(s) {missing_gt}"
            )
        undeclared = [c for c in spec.required_channels if c not in obj.declared_channels]
        if undeclared:
            raise ScoringConfigError(
                f"objective {obj.objective_class!r}: required observation channel(s) "
                f"{undeclared} not declared (declared: {list(obj.declared_channels)})"
            )


__all__ = [
    "OperatorObjective",
    "ScoringConfigError",
    "scorer_for",
    "validate_run_objectives",
]
