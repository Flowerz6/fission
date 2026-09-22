"""Credit assignment — main effects + pairwise interactions on signal-verified success.

Estimates per-axis MAIN EFFECTS and pairwise INTERACTIONS from CLEAN one-axis
results and planned pairs, over signal-verified outcomes only (Q6). The result is
the target's **boundary model** — the basis the later drift layer uses to detect
the boundary MOVING.

Honesty rules baked in:

* Only ``instantiation_clean`` AND ``run_outcome != 'invalid_technical'`` outcomes
  enter an estimate. ``invalid_technical`` variants are RE-QUEUED, never scored as
  failures — they must not depress a rate or an effect.
* A ``LOW-CONFIDENCE`` flag fires on thin ESS **or** a self-report-only basis
  (semantic axes are certified clean only by the strategist's self-report, which
  systematically under-reports bleed — so a clean-but-self-reported effect is not
  high-confidence even at decent ESS).
* ``escalation_speed`` and ``turn_order`` main effects are flagged ENTANGLED until
  enough planned-pair data exists; an entangled coefficient is never reported flat.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.archive.posterior import BetaPosterior
from advtest_ext.mutate.engine import PRIORITY_PAIR

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.orm import Session

    from advtest_ext.archive.fingerprint import TargetProfileKey
    from advtest_ext.archive.signature import Signature
    from advtest_ext.core.orch_trace_meta import RunOutcome

_DETERMINISTIC = "deterministic"
_SELF_REPORT = "self_report"
# Axes whose main effects are entangled in multi-turn crescendo until paired.
ENTANGLED_AXES: frozenset[str] = frozenset(PRIORITY_PAIR)
_MIN_ESS = 6.0  # below this, an effect is low-confidence on sample-size grounds
_MIN_PAIR_FOR_DISENTANGLE = 4  # planned-pair outcomes needed before entanglement lifts


@dataclass(frozen=True)
class VariantOutcome:
    """One realized+scored variant, tagged with everything credit assignment needs."""

    signature: Signature
    axes: dict[str, str]  # axis -> feature_value moved from baseline (1 for one-axis, 2 for pair)
    signal_verified: bool
    run_outcome: RunOutcome
    instantiation_clean: bool
    clean_basis: str  # "deterministic" | "self_report"

    @property
    def is_pair(self) -> bool:
        return len(self.axes) == 2


@dataclass(frozen=True)
class AxisEffect:
    axis: str
    feature_value: str
    success_rate: float
    delta_vs_baseline: float | None
    ci_low: float
    ci_high: float
    ess: float
    n_signal_verified: float
    low_confidence: bool
    entangled: bool
    basis: str


@dataclass(frozen=True)
class Interaction:
    axes: tuple[str, str]
    values: tuple[str, str]
    observed_rate: float
    additive_prediction: float | None
    interaction: float | None
    ess: float
    low_confidence: bool


@dataclass
class BoundaryModel:
    """The persisted per-axis boundary the drift layer compares against."""

    main_effects: list[AxisEffect] = field(default_factory=list)
    interactions: list[Interaction] = field(default_factory=list)
    requeued: int = 0
    baseline_rate: float | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "baseline_rate": self.baseline_rate,
            "requeued": self.requeued,
            "main_effects": [
                {
                    "axis": e.axis, "feature_value": e.feature_value, "success_rate": e.success_rate,
                    "delta_vs_baseline": e.delta_vs_baseline, "ci": [e.ci_low, e.ci_high], "ess": e.ess,
                    "n_signal_verified": e.n_signal_verified, "low_confidence": e.low_confidence,
                    "entangled": e.entangled, "basis": e.basis,
                }
                for e in self.main_effects
            ],
            "interactions": [
                {
                    "axes": list(i.axes), "values": list(i.values), "observed_rate": i.observed_rate,
                    "additive_prediction": i.additive_prediction, "interaction": i.interaction,
                    "ess": i.ess, "low_confidence": i.low_confidence,
                }
                for i in self.interactions
            ],
        }


def _posterior(successes: float, total: float) -> BetaPosterior:
    post = BetaPosterior()
    for _ in range(int(successes)):
        post.update(success_signal_verified=True)
    for _ in range(int(total - successes)):
        post.update(success_signal_verified=False)
    return post


def _rate(outcomes: Sequence[VariantOutcome]) -> tuple[float, float, BetaPosterior]:
    successes = sum(1.0 for o in outcomes if o.signal_verified)
    post = _posterior(successes, float(len(outcomes)))
    return successes, float(len(outcomes)), post


def estimate_effects(
    outcomes: Iterable[VariantOutcome],
    *,
    baseline_rate: float | None = None,
) -> tuple[BoundaryModel, list[VariantOutcome]]:
    """Estimate main effects + interactions. Returns ``(boundary_model, requeued)``.

    ``requeued`` are the ``invalid_technical`` outcomes — re-run them; they never
    enter an estimate.
    """
    outcomes = list(outcomes)
    requeued = [o for o in outcomes if o.run_outcome == "invalid_technical"]
    usable = [o for o in outcomes if o.run_outcome != "invalid_technical" and o.instantiation_clean]

    one_axis = [o for o in usable if not o.is_pair]
    pairs = [o for o in usable if o.is_pair]
    pair_count = len(pairs)

    model = BoundaryModel(requeued=len(requeued), baseline_rate=baseline_rate)

    # --- main effects (one-axis, clean) ---
    groups: dict[tuple[str, str], list[VariantOutcome]] = {}
    for outcome in one_axis:
        (axis, value), = outcome.axes.items()
        groups.setdefault((axis, value), []).append(outcome)

    for (axis, value), group in sorted(groups.items()):
        successes, _total, post = _rate(group)
        ci = post.credible_interval()
        basis = _DETERMINISTIC if all(o.clean_basis == _DETERMINISTIC for o in group) else _SELF_REPORT
        entangled = axis in ENTANGLED_AXES and pair_count < _MIN_PAIR_FOR_DISENTANGLE
        low_conf = post.effective_sample_size() < _MIN_ESS or basis == _SELF_REPORT or entangled
        model.main_effects.append(
            AxisEffect(
                axis=axis, feature_value=value, success_rate=post.mean(),
                delta_vs_baseline=(post.mean() - baseline_rate) if baseline_rate is not None else None,
                ci_low=ci.low, ci_high=ci.high, ess=post.effective_sample_size(),
                n_signal_verified=successes, low_confidence=low_conf, entangled=entangled, basis=basis,
            )
        )

    # --- pairwise interactions (planned pairs; PRIORITY_PAIR first) ---
    pair_groups: dict[tuple[tuple[str, str], tuple[str, str]], list[VariantOutcome]] = {}
    for outcome in pairs:
        axes = tuple(sorted(outcome.axes))
        values = tuple(outcome.axes[a] for a in axes)
        pair_groups.setdefault((axes, values), []).append(outcome)  # type: ignore[arg-type]

    main_by_axis_value = {(e.axis, e.feature_value): e.success_rate for e in model.main_effects}
    for (axes, values), group in sorted(pair_groups.items()):
        successes, _total, post = _rate(group)
        # Additive prediction from the two main effects, if both are known.
        m0 = main_by_axis_value.get((axes[0], values[0]))
        m1 = main_by_axis_value.get((axes[1], values[1]))
        additive = None if (m0 is None or m1 is None) else min(1.0, m0 + m1 - (baseline_rate or 0.0))
        interaction_val = None if additive is None else post.mean() - additive
        model.interactions.append(
            Interaction(
                axes=axes, values=values, observed_rate=post.mean(),
                additive_prediction=additive, interaction=interaction_val,
                ess=post.effective_sample_size(),
                low_confidence=post.effective_sample_size() < _MIN_ESS,
            )
        )

    return model, requeued


def store_boundary_model(session: Session, profile: TargetProfileKey, model: BoundaryModel) -> str:
    """Persist a boundary model for a target profile; return its id."""
    from advtest_ext.storage.models import BoundaryModelRow

    row_id = uuid.uuid4().hex
    session.add(
        BoundaryModelRow(
            id=row_id,
            model_label=profile.model_label,
            tenant_tier=profile.tenant_tier,
            surface_config_hash=profile.surface_config_hash,
            payload=model.to_payload(),
        )
    )
    session.flush()
    return row_id


def load_boundary_model_payload(session: Session, row_id: str) -> dict[str, Any]:
    from advtest_ext.storage.models import BoundaryModelRow

    row = session.get(BoundaryModelRow, row_id)
    if row is None:
        raise KeyError(f"no boundary model {row_id!r}")
    return dict(row.payload)


__all__ = [
    "ENTANGLED_AXES",
    "AxisEffect",
    "BoundaryModel",
    "Interaction",
    "VariantOutcome",
    "estimate_effects",
    "load_boundary_model_payload",
    "store_boundary_model",
]
