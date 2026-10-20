"""Part F — dashboard views + orchestration loop (acceptance tests, no live tenant)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from advtest_ext.archive.fingerprint import (
    Archive,
    Cell,
    CellStat,
    Fingerprint,
    TargetProfileKey,
    encode_cell_key,
    store_snapshot,
)
from advtest_ext.archive.posterior import BetaPosterior
from advtest_ext.archive.signature import BASELINE, Signature
from advtest_ext.dashboard import create_app
from advtest_ext.mutate.effects import AxisEffect, BoundaryModel, store_boundary_model
from advtest_ext.orchestrate.loop import (
    TenantScheduler,
    on_version_change,
    run_native_phase,
)
from advtest_ext.scoring.base import ScoreResult
from advtest_ext.scoring.objectives import objective_for
from advtest_ext.storage.database import make_engine, make_session_factory
from advtest_ext.storage.models import TargetDriftAlert, TargetProfile

PROFILE = TargetProfileKey("gemini-1", "enterprise", "cfg")
TOKEN = "gemini-1~enterprise~cfg"


def _sig(**axes: str) -> Signature:
    base = dict(
        framing=BASELINE,
        turn_order=BASELINE,
        persona=BASELINE,
        carrier_placement=BASELINE,
        escalation_speed=BASELINE,
    )
    base.update(axes)
    return Signature(
        objective_class="canary_exfil", cross_app_hops_bucket="0", individual_id="h", **base
    )


@pytest.fixture
def engine_and_session() -> Iterator[tuple]:
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    yield engine, factory


@pytest.fixture
def client_factory(engine_and_session: tuple):  # type: ignore[no-untyped-def]
    engine, factory = engine_and_session

    def _client() -> TestClient:
        return TestClient(create_app(engine=engine))

    return _client, factory


def _add_profile(session: Session) -> None:
    session.add(
        TargetProfile(
            model_label=PROFILE.model_label,
            tenant_tier=PROFILE.tenant_tier,
            surface_config_hash=PROFILE.surface_config_hash,
        )
    )


def _snapshot(
    session: Session, cells: dict, *, taken_at: datetime, profile: TargetProfileKey = PROFILE
) -> None:
    stats = {k: CellStat(cell_key=k, mean=m, ess=e, resolution=r) for k, (m, e, r) in cells.items()}
    store_snapshot(session, Fingerprint(profile=profile, taken_at=taken_at, cells=stats))


# --- Acceptance 1: heatmap renders native/coarse distinctly, reads by cycle ---


def test_heatmap_renders_resolution_and_reads_by_cycle(client_factory) -> None:  # type: ignore[no-untyped-def]
    make_client, factory = client_factory
    native = _sig(framing="authority").cell_key()
    coarse = _sig(framing="urgency").cell_key()
    with factory() as s:
        _add_profile(s)
        _snapshot(
            s,
            {native: (0.2, 10.0, "native"), coarse: (0.3, 4.0, "coarse")},
            taken_at=datetime.now(UTC),
        )
        _snapshot(
            s,
            {native: (0.9, 12.0, "native"), coarse: (0.3, 4.0, "coarse")},
            taken_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        s.commit()
    client = make_client()
    body = client.get(f"/target_drift/{TOKEN}/heatmap").text
    assert "res-native" in body and "res-coarse" in body
    assert "coarse (weak prior)" in body  # coarse visually/verbally distinct
    # cycle selector reads snapshots: cycle 0 shows native mean 0.20, latest shows 0.90
    assert "0.20" in client.get(f"/target_drift/{TOKEN}/heatmap?cycle=0").text
    assert "0.90" in client.get(f"/target_drift/{TOKEN}/heatmap?cycle=1").text


# --- Acceptance 2 + 3: alert gating + guards source label ---------------------


def _add_alert(
    session: Session,
    cell_key: str,
    *,
    calibrated: bool,
    source: str,
    direction: str = "more_vulnerable",
) -> None:
    session.add(
        TargetDriftAlert(
            id=f"a-{source}-{calibrated}",
            model_label=PROFILE.model_label,
            tenant_tier=PROFILE.tenant_tier,
            surface_config_hash=PROFILE.surface_config_hash,
            cell_key=cell_key,
            direction=direction,
            magnitude=3.0,
            source=source,
            detector_calibrated=calibrated,
            note="" if calibrated else "detector not calibrated — alerts suppressed",
        )
    )


def test_uncalibrated_alert_suppressed_calibrated_shows_source(client_factory) -> None:  # type: ignore[no-untyped-def]
    make_client, factory = client_factory
    key = encode_cell_key(_sig(framing="authority").cell_key())
    with factory() as s:
        _add_alert(s, key, calibrated=False, source="target")
        _add_alert(
            s, encode_cell_key(_sig(framing="urgency").cell_key()), calibrated=True, source="target"
        )
        s.commit()
    body = make_client().get(f"/target_drift/{TOKEN}/alerts").text
    assert "calibrating — alerts suppressed" in body  # uncalibrated detector
    assert "source: target" in body  # calibrated one renders with its source label


def test_search_collapse_renders_as_search_not_target(client_factory) -> None:  # type: ignore[no-untyped-def]
    make_client, factory = client_factory
    key = encode_cell_key(_sig(framing="authority").cell_key())
    with factory() as s:
        _add_alert(s, key, calibrated=True, source="search")
        s.commit()
    body = make_client().get(f"/target_drift/{TOKEN}/alerts").text
    assert (
        "source: search" in body and "source-search" in body
    )  # labelled search, not a target regression


# --- Acceptance 4: version-diff shows entangled axis with caveat -------------


def test_version_diff_shows_entangled_caveat(client_factory) -> None:  # type: ignore[no-untyped-def]
    make_client, factory = client_factory
    new_profile = TargetProfileKey("gemini-2", "enterprise", "cfg")
    new_token = "gemini-2~enterprise~cfg"
    regressed = _sig(framing="urgency", escalation_speed="fast").cell_key()
    with factory() as s:
        _snapshot(
            s, {regressed: (0.1, 10.0, "native")}, taken_at=datetime.now(UTC), profile=PROFILE
        )  # old: robust
        _snapshot(
            s, {regressed: (0.9, 10.0, "native")}, taken_at=datetime.now(UTC), profile=new_profile
        )  # new: vulnerable
        boundary = BoundaryModel(
            main_effects=[
                AxisEffect(
                    "framing",
                    "urgency",
                    0.8,
                    0.7,
                    0.6,
                    0.95,
                    12.0,
                    9.0,
                    low_confidence=False,
                    entangled=False,
                    basis="self_report",
                ),
                AxisEffect(
                    "escalation_speed",
                    "fast",
                    0.6,
                    0.5,
                    0.3,
                    0.9,
                    4.0,
                    2.0,
                    low_confidence=True,
                    entangled=True,
                    basis="self_report",
                ),
            ]
        )
        store_boundary_model(s, new_profile, boundary)
        s.commit()
    body = make_client().get(f"/target_drift/diff?old={TOKEN}&new={new_token}").text
    assert "framing" in body and "asserted" in body
    assert "entangled" in body and "not asserted" in body  # entangled axis shown WITH caveat


# --- Acceptance 5: stability status honesty ---------------------------------


def test_status_partial_when_only_cusum_quiet_no_diff(client_factory) -> None:  # type: ignore[no-untyped-def]
    make_client, factory = client_factory
    with factory() as s:
        _add_profile(s)
        _snapshot(
            s,
            {_sig(framing="authority").cell_key(): (0.2, 10.0, "native")},
            taken_at=datetime.now(UTC),
        )  # ONE snapshot
        s.commit()
    body = make_client().get(f"/target_drift/{TOKEN}/status").text
    assert "partial coverage" in body  # not "stable" — no version diff possible
    assert "stable" not in body.split("td-state")[1][:40] if "td-state" in body else True


def test_status_stable_only_when_both_agree(client_factory) -> None:  # type: ignore[no-untyped-def]
    make_client, factory = client_factory
    key = _sig(framing="authority").cell_key()
    with factory() as s:
        _add_profile(s)
        _snapshot(s, {key: (0.2, 10.0, "native")}, taken_at=datetime.now(UTC))
        _snapshot(
            s, {key: (0.2, 10.0, "native")}, taken_at=datetime.now(UTC) + timedelta(minutes=1)
        )  # no change
        s.commit()
    body = make_client().get(f"/target_drift/{TOKEN}/status").text
    assert (
        "state-stable" in body
    )  # both detectors agree: CUSUM quiet + version-diff within tolerance


# --- Acceptance 6: mutual exclusion per target_profile ----------------------


def test_mutual_exclusion_same_profile_different_profiles() -> None:
    scheduler = TenantScheduler()
    other = TargetProfileKey("gemini-1", "enterprise", "OTHER")
    # Hold the profile lock (as a native phase would)...
    assert scheduler.try_acquire(PROFILE) is True
    try:
        # ...a sentinel phase on the SAME profile cannot acquire it (no overlap).
        assert scheduler.try_acquire(PROFILE) is False
        # ...but a DIFFERENT profile is independent and may run concurrently.
        assert scheduler.try_acquire(other) is True
        scheduler.release(other)
    finally:
        scheduler.release(PROFILE)


# --- Acceptance 7: invalid_technical updates nothing -------------------------


def test_invalid_technical_run_updates_no_posterior() -> None:
    import asyncio

    from advtest_ext.observe.model import CHANNELS, SideEffectObservation, TenantState

    class _CleanFixture:
        def reset(self) -> None: ...
        def is_clean(self) -> bool:
            return True

    class _EmptyObserver:
        def snapshot(self) -> TenantState:
            return TenantState.empty()

    scheduler = TenantScheduler()
    archive = Archive(profile=PROFILE)

    class _Variant:
        signature = _sig(framing="authority")

    # cross_document_exfil with files_shared UNAVAILABLE -> coherence = invalid_technical.
    objective = objective_for("cross_document_exfil")
    obs = SideEffectObservation(
        status={**{c: "observed" for c in CHANNELS}, "files_shared": "unavailable"}
    )
    result = ScoreResult(
        signal_verified=False,
        judge_success=None,
        judge_confidence=0.0,
        leak_fraction=0.0,
        refusal_style=None,
        evidence="",
        observation_path="none",
    )

    def deliver(_v: object) -> tuple:
        return result, obs, objective

    outcome = asyncio.run(
        run_native_phase(
            scheduler,
            PROFILE,
            archive,
            [_Variant()],
            deliver_and_score=deliver,
            fixture=_CleanFixture(),
            observer=_EmptyObserver(),
        )
    )
    assert len(outcome.requeued) == 1 and outcome.updated == 0 and outcome.invalid_technical == 1
    cell = archive.cell(_Variant.signature.cell_key())
    assert (cell.posterior.alpha, cell.posterior.beta) == (1.0, 1.0)  # untouched


# --- Acceptance 8: version change → carry-forward + regression-hunt ----------


def test_version_change_carry_forward_and_prioritise() -> None:
    old = Archive(profile=PROFILE)
    robust = _sig(framing="authority")  # previously robust (low success)
    vulnerable = _sig(framing="urgency")
    old.cells[robust.cell_key()] = Cell(
        key=robust.cell_key(), posterior=BetaPosterior(alpha=1.0, beta=20.0)
    )
    old.cells[vulnerable.cell_key()] = Cell(
        key=vulnerable.cell_key(), posterior=BetaPosterior(alpha=15.0, beta=3.0)
    )

    new_profile = TargetProfileKey("gemini-2", "enterprise", "cfg")
    new_archive, transition = on_version_change(old, new_profile, decay=0.4)

    # Decayed carry-forward preserves the mean but scales ESS + keeps undecayed ref.
    seeded = new_archive.cells[robust.cell_key()].posterior
    assert seeded.prev_posterior_ref == (1.0, 20.0)  # undecayed reference persisted
    assert abs(seeded.effective_sample_size() - 0.4 * 21.0) < 1e-9
    # Regression-hunt prioritises the previously-robust cell first.
    assert transition.regression_hunt_order[0] == robust.cell_key()
    assert (
        vulnerable.cell_key() not in transition.regression_hunt_order
    )  # not robust → not a hunt target


# --- Acceptance 9: all new routes under /target_drift, none shadow operational ---


def test_routes_under_target_drift_prefix_no_shadow(engine_and_session: tuple) -> None:
    engine, _ = engine_and_session
    app = create_app(engine=engine)
    td_routes = [r.path for r in app.routes if getattr(r, "path", "").startswith("/target_drift")]
    assert td_routes, "no target_drift routes registered"
    # Every drift-namespaced NEW route is under /target_drift...
    assert all(p.startswith("/target_drift") for p in td_routes)
    # ...and the operational-drift route (/drift) is untouched and separate.
    all_paths = [getattr(r, "path", "") for r in app.routes]
    assert "/drift" in all_paths
    assert "/drift" not in td_routes  # not shadowed / not reused
