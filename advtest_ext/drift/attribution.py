"""Axis-level attribution — the deliverable.

When cells drift, join Part D's ``boundary_models`` effect coefficients to report
WHICH descriptor axis the regression rides on (e.g. "previously-robust cells with
pretext=authority, hop>=1 now signal-verify"). Produces a version-diff regression
report. Respects Part D's low-confidence / entangled flags: it does NOT assert an
axis regression on an entangled (escalation_speed/turn_order) or thin-ESS
coefficient without saying so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from advtest_ext.archive.fingerprint import decode_cell_key
from advtest_ext.archive.signature import BASELINE, KEY_MUTATION_AXES

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.archive.fingerprint import CellKey, TargetProfileKey
    from advtest_ext.mutate.effects import AxisEffect, BoundaryModel


@dataclass(frozen=True)
class AxisRegression:
    axis: str
    feature_value: str
    success_rate: float
    ci_low: float
    ci_high: float
    asserted: bool  # False when we refuse to assert (entangled / thin / no coefficient)
    caveat: str | None


@dataclass
class VersionDiffReport:
    profile_old: TargetProfileKey
    profile_new: TargetProfileKey
    regressed_cells: list[CellKey] = field(default_factory=list)
    axis_patterns: list[AxisRegression] = field(default_factory=list)

    def asserted_axes(self) -> list[AxisRegression]:
        return [a for a in self.axis_patterns if a.asserted]


def _effect_for(boundary: BoundaryModel, axis: str, value: str) -> AxisEffect | None:
    for effect in boundary.main_effects:
        if effect.axis == axis and effect.feature_value == value:
            return effect
    return None


def attribute_drift(
    profile_old: TargetProfileKey,
    profile_new: TargetProfileKey,
    regressed_cells: list[CellKey],
    boundary: BoundaryModel,
) -> VersionDiffReport:
    """Attribute regressed cells to the descriptor axes carrying the regression."""
    report = VersionDiffReport(
        profile_old=profile_old, profile_new=profile_new, regressed_cells=list(regressed_cells)
    )
    seen: set[tuple[str, str]] = set()
    for cell_key in regressed_cells:
        fields = decode_cell_key(cell_key) if isinstance(cell_key, str) else cell_key
        # cell_key layout: (objective_class, framing, turn_order, persona, carrier_placement, escalation_speed, hops)
        axis_values = dict(zip(KEY_MUTATION_AXES, fields[1:6], strict=True))
        for axis, value in axis_values.items():
            if value == BASELINE or (axis, value) in seen:
                continue
            seen.add((axis, value))
            effect = _effect_for(boundary, axis, value)
            if effect is None:
                report.axis_patterns.append(
                    AxisRegression(
                        axis=axis,
                        feature_value=value,
                        success_rate=0.0,
                        ci_low=0.0,
                        ci_high=0.0,
                        asserted=False,
                        caveat="no boundary coefficient for this axis value",
                    )
                )
                continue
            caveat = None
            asserted = True
            if effect.entangled:
                asserted = False
                caveat = "entangled (escalation_speed/turn_order) — not asserted without planned-pair data"
            elif effect.low_confidence:
                asserted = False
                caveat = "low-confidence coefficient (thin ESS or self-report basis) — not asserted"
            report.axis_patterns.append(
                AxisRegression(
                    axis=axis,
                    feature_value=value,
                    success_rate=effect.success_rate,
                    ci_low=effect.ci_low,
                    ci_high=effect.ci_high,
                    asserted=asserted,
                    caveat=caveat,
                )
            )
    return report


__all__ = ["AxisRegression", "VersionDiffReport", "attribute_drift"]
