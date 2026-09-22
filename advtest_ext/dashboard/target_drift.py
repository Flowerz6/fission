"""Target-version drift dashboard views (Part F1).

Pure presentation of what Parts A-E already persist, under the ``/target_drift``
route prefix — DISTINCT from the operational-drift routes/templates in
``dashboard/drift.py`` (never shadowed). Reads: fingerprint snapshots (heatmap),
the persisted per-cycle drift verdict (alert stream, gated on the detector's
calibration flag), and version-diff (snapshots + boundary_models via Part E's
functions). No new measurement logic.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from advtest_ext.archive.fingerprint import Fingerprint, TargetProfileKey, decode_cell_key
from advtest_ext.drift.attribution import attribute_drift
from advtest_ext.drift.calibrate import DriftThresholds
from advtest_ext.drift.fingerprint_diff import fingerprint_divergence, regressed_cells
from advtest_ext.mutate.effects import AxisEffect, BoundaryModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

    from fastapi.templating import Jinja2Templates
    from sqlalchemy.orm import Session

_SEP = "~"


def _encode_profile(model_label: str, tenant_tier: str, surface_config_hash: str) -> str:
    return _SEP.join((model_label, tenant_tier, surface_config_hash))


def _decode_profile(token: str) -> TargetProfileKey:
    parts = token.split(_SEP)
    if len(parts) != 3:
        raise HTTPException(404, f"malformed target_profile token {token!r}")
    return TargetProfileKey(parts[0], parts[1], parts[2])


def _snapshots(session: Session, profile: TargetProfileKey) -> list[Any]:
    from advtest_ext.storage.models import FingerprintSnapshot

    return list(
        session.query(FingerprintSnapshot)
        .filter_by(
            model_label=profile.model_label,
            tenant_tier=profile.tenant_tier,
            surface_config_hash=profile.surface_config_hash,
        )
        .order_by(FingerprintSnapshot.taken_at)
        .all()
    )


def _boundary(session: Session, profile: TargetProfileKey) -> BoundaryModel | None:
    from advtest_ext.storage.models import BoundaryModelRow

    row = (
        session.query(BoundaryModelRow)
        .filter_by(
            model_label=profile.model_label,
            tenant_tier=profile.tenant_tier,
            surface_config_hash=profile.surface_config_hash,
        )
        .order_by(BoundaryModelRow.created_at.desc())
        .first()
    )
    if row is None:
        return None
    return _boundary_from_payload(dict(row.payload))


def _boundary_from_payload(payload: dict[str, Any]) -> BoundaryModel:
    model = BoundaryModel(baseline_rate=payload.get("baseline_rate"), requeued=int(payload.get("requeued", 0)))
    for e in payload.get("main_effects", []):
        ci = e.get("ci", [0.0, 0.0])
        model.main_effects.append(
            AxisEffect(
                axis=e["axis"], feature_value=e["feature_value"], success_rate=e["success_rate"],
                delta_vs_baseline=e.get("delta_vs_baseline"), ci_low=ci[0], ci_high=ci[1],
                ess=e["ess"], n_signal_verified=e["n_signal_verified"], low_confidence=e["low_confidence"],
                entangled=e["entangled"], basis=e["basis"],
            )
        )
    return model


def build_target_drift_router(
    templates: Jinja2Templates, get_session: Callable[..., Any]
) -> APIRouter:
    """Build the ``/target_drift`` router closing over the app's templates + DB dep."""
    router = APIRouter(prefix="/target_drift", tags=["target_drift"])

    @router.get("", response_class=HTMLResponse)
    async def index(request: Request, session: Session = Depends(get_session)) -> Response:  # noqa: B008
        from advtest_ext.storage.models import TargetProfile

        profiles = [
            {
                "token": _encode_profile(p.model_label, p.tenant_tier, p.surface_config_hash),
                "model_label": p.model_label, "tenant_tier": p.tenant_tier, "surface_config_hash": p.surface_config_hash,
            }
            for p in session.query(TargetProfile).all()
        ]
        return templates.TemplateResponse(request, "target_drift/index.html", {"profiles": profiles})

    @router.get("/{token}/heatmap", response_class=HTMLResponse)
    async def heatmap(
        request: Request, token: str, cycle: int | None = None, session: Session = Depends(get_session)  # noqa: B008
    ) -> Response:
        profile = _decode_profile(token)
        snaps = _snapshots(session, profile)
        if not snaps:
            raise HTTPException(404, "no fingerprint snapshots for this target_profile")
        idx = cycle if cycle is not None and 0 <= cycle < len(snaps) else len(snaps) - 1
        fp = Fingerprint.from_payload(dict(snaps[idx].payload))
        cells = [
            {
                "cell_key": key, "mean": stat.mean, "ess": stat.ess, "resolution": stat.resolution,
                "coarse": stat.resolution != "native",
            }
            for key, stat in sorted(fp.cells.items())
        ]
        ctx = {
            "token": token, "profile": profile, "cells": cells,
            "cycle": idx, "n_cycles": len(snaps),
            "cycle_labels": [s.taken_at.isoformat() for s in snaps],
        }
        return templates.TemplateResponse(request, "target_drift/heatmap.html", ctx)

    @router.get("/{token}/alerts", response_class=HTMLResponse)
    async def alerts(request: Request, token: str, session: Session = Depends(get_session)) -> Response:  # noqa: B008
        from advtest_ext.storage.models import TargetDriftAlert

        profile = _decode_profile(token)
        rows = list(
            session.query(TargetDriftAlert)
            .filter_by(
                model_label=profile.model_label, tenant_tier=profile.tenant_tier,
                surface_config_hash=profile.surface_config_hash,
            )
            .order_by(TargetDriftAlert.cycle_ts.desc())
            .limit(200)
            .all()
        )
        alert_views = [
            {
                "cell_key": r.cell_key, "direction": r.direction, "magnitude": round(r.magnitude, 2),
                "source": r.source, "calibrated": bool(r.detector_calibrated), "note": r.note,
            }
            for r in rows
        ]
        return templates.TemplateResponse(
            request, "target_drift/alerts.html", {"token": token, "alerts": alert_views}
        )

    @router.get("/{token}/status", response_class=HTMLResponse)
    async def status(request: Request, token: str, session: Session = Depends(get_session)) -> Response:  # noqa: B008
        from advtest_ext.storage.models import TargetDriftAlert

        profile = _decode_profile(token)
        snaps = _snapshots(session, profile)
        calibrated_target_alerts = (
            session.query(TargetDriftAlert)
            .filter_by(
                model_label=profile.model_label, tenant_tier=profile.tenant_tier,
                surface_config_hash=profile.surface_config_hash, detector_calibrated=True, source="target",
            )
            .count()
        )
        thresholds = DriftThresholds()
        version_diff_available = len(snaps) >= 2
        version_diff_within_tolerance: bool | None = None
        if version_diff_available:
            old_fp = Fingerprint.from_payload(dict(snaps[-2].payload))
            new_fp = Fingerprint.from_payload(dict(snaps[-1].payload))
            diff = fingerprint_divergence(old_fp, new_fp)
            worst_psi = max((d.psi for d in diff.by_resolution.values()), default=0.0)
            version_diff_within_tolerance = worst_psi <= thresholds.psi_alert

        cusum_quiet = calibrated_target_alerts == 0
        if cusum_quiet and version_diff_available and version_diff_within_tolerance:
            state, detail = "stable", "no sustained shift (CUSUM) AND version-diff within tolerance"
        elif not cusum_quiet:
            state, detail = "sustained_drift", "CUSUM detected a sustained shift"
        elif version_diff_available and version_diff_within_tolerance is False:
            state, detail = "gradual_drift", "version-diff over tolerance (gradual drift CUSUM smooths over)"
        else:
            state, detail = "partial_coverage", "CUSUM quiet but no version-diff computed — NOT stable"

        return templates.TemplateResponse(
            request, "target_drift/status.html",
            {"token": token, "state": state, "detail": detail,
             "cusum_quiet": cusum_quiet, "version_diff_available": version_diff_available},
        )

    @router.get("/diff", response_class=HTMLResponse)
    async def version_diff(
        request: Request, old: str, new: str, session: Session = Depends(get_session)  # noqa: B008
    ) -> Response:
        old_profile, new_profile = _decode_profile(old), _decode_profile(new)
        old_snaps, new_snaps = _snapshots(session, old_profile), _snapshots(session, new_profile)
        if not old_snaps or not new_snaps:
            raise HTTPException(404, "both target_profiles need at least one snapshot")
        old_fp = Fingerprint.from_payload(dict(old_snaps[-1].payload))
        new_fp = Fingerprint.from_payload(dict(new_snaps[-1].payload))
        diff = fingerprint_divergence(old_fp, new_fp)
        regressed = regressed_cells(old_fp, new_fp, resolution="native")
        boundary = _boundary(session, new_profile) or BoundaryModel()
        report = attribute_drift(old_profile, new_profile, regressed, boundary)
        axis_rows = [
            {
                "axis": a.axis, "feature_value": a.feature_value, "success_rate": round(a.success_rate, 3),
                "ci": [round(a.ci_low, 3), round(a.ci_high, 3)], "asserted": a.asserted, "caveat": a.caveat,
            }
            for a in report.axis_patterns
        ]
        divergences = [
            {"resolution": d.resolution, "psi": round(d.psi, 4), "kl": round(d.kl, 4), "n": d.n_matched_cells}
            for d in diff.by_resolution.values()
        ]
        ctx = {
            "old": old, "new": new, "facets_changed": diff.facets_changed,
            "regressed": [decode_cell_key(k) if isinstance(k, str) else k for k in regressed],
            "axis_rows": axis_rows, "divergences": divergences,
        }
        return templates.TemplateResponse(request, "target_drift/diff.html", ctx)

    return router


__all__ = ["build_target_drift_router"]
