"""Mutation-as-experiment engine (Part D).

Produces NON-PARAPHRASE variants by construction (typed axis-value selection,
not free-text authoring), does factorial credit assignment over the descriptor
axes, and drives selection from the target's own behaviour. The engine SELECTS
typed axis values and delegates rendering to the existing guide machinery
(``GuideMutationMaterializer`` / ``cell_generator`` / ``_tip_patch``), so operator
fragments stay the ONLY payload source.

Layers:

* :mod:`instantiate` — deterministic single-axis instantiation + verification
  (the Part-D flag). Reuses the materializer's isolation invariant and Part A's
  ``signature`` over a synthetic node to certify exactly-one-axis moves.
* :mod:`engine` — the mutation plan (one-axis variants + planned pairs).
* :mod:`novelty` — the anti-paraphrase gate (cell + within-cell individual).
* :mod:`diagnose` — reasoning-driven hypotheses (a HYPOTHESIS SOURCE only;
  confirmed only by a signal-verified shift) + judge-only leads.
* :mod:`crossover` — compose descriptor values across two cells.
* :mod:`effects` — main effects + interactions on signal-verified success, the
  target's boundary model.
"""

from __future__ import annotations

from advtest_ext.mutate.crossover import CrossoverResult, crossover
from advtest_ext.mutate.diagnose import Hypothesis, JudgeOnlyLead, diagnose, judge_only_leads
from advtest_ext.mutate.effects import (
    AxisEffect,
    BoundaryModel,
    Interaction,
    VariantOutcome,
    estimate_effects,
    store_boundary_model,
)
from advtest_ext.mutate.engine import (
    MutationPlan,
    PlannedVariant,
    RealizedVariant,
    plan_mutations,
    realize_variant,
)
from advtest_ext.mutate.instantiate import (
    CARRIER_AXIS,
    SEMANTIC_AXES,
    AxisPatch,
    Instantiation,
    PatchStrategist,
    instantiate,
)
from advtest_ext.mutate.novelty import NoveltyDecision, cell_admissible, individual_admissible

__all__ = [
    "CARRIER_AXIS",
    "SEMANTIC_AXES",
    "AxisEffect",
    "AxisPatch",
    "BoundaryModel",
    "CrossoverResult",
    "Hypothesis",
    "Instantiation",
    "Interaction",
    "JudgeOnlyLead",
    "MutationPlan",
    "NoveltyDecision",
    "PatchStrategist",
    "PlannedVariant",
    "RealizedVariant",
    "VariantOutcome",
    "cell_admissible",
    "crossover",
    "diagnose",
    "estimate_effects",
    "individual_admissible",
    "instantiate",
    "judge_only_leads",
    "plan_mutations",
    "realize_variant",
    "store_boundary_model",
]
