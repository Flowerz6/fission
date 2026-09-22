"""Part C — two-channel scoring for the seven Workspace objective classes.

A deterministic oracle (``signal_verified``, the only channel that can move a
posterior — Q6) plus an optional LLM judge, routed by ``objective_class`` from
the sub-step-0 ``guide_id → objective_class`` table. Text-scored classes read the
reply over the reused UI session; action classes read the Part B
:class:`~advtest_ext.observe.model.SideEffectObservation`. The coherence rule
(:mod:`coherence`) turns an unobservable required channel into
``invalid_technical`` so cells we merely couldn't observe never record false
robustness.
"""

from __future__ import annotations

from advtest_ext.scoring.base import (
    ObservationPath,
    Scorer,
    ScoreResult,
    ScoringContext,
)
from advtest_ext.scoring.coherence import (
    archive_observe,
    coherent_run_outcome,
    required_channel_gap,
)
from advtest_ext.scoring.guide_map import (
    OUT_OF_SCOPE_THREAT_CLASS,
    PendingObjectiveClass,
    out_of_scope_threat_class,
    resolve_objective_class,
)
from advtest_ext.scoring.objectives import (
    OBJECTIVES,
    SCORABLE_CLASSES,
    Objective,
    ObjectiveClass,
    objective_for,
)
from advtest_ext.scoring.registry import (
    OperatorObjective,
    ScoringConfigError,
    scorer_for,
    validate_run_objectives,
)

__all__ = [
    "OBJECTIVES",
    "OUT_OF_SCOPE_THREAT_CLASS",
    "SCORABLE_CLASSES",
    "Objective",
    "ObjectiveClass",
    "ObservationPath",
    "OperatorObjective",
    "PendingObjectiveClass",
    "ScoreResult",
    "Scorer",
    "ScoringConfigError",
    "ScoringContext",
    "archive_observe",
    "coherent_run_outcome",
    "objective_for",
    "out_of_scope_threat_class",
    "required_channel_gap",
    "resolve_objective_class",
    "scorer_for",
    "validate_run_objectives",
]
