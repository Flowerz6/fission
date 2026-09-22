"""Part F addendum — the native-path tenant-baseline bracket (verify-clean)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from advtest_ext.archive.fingerprint import Archive, TargetProfileKey
from advtest_ext.archive.signature import BASELINE, Signature
from advtest_ext.observe.mock import state as obs_state
from advtest_ext.observe.model import FileCreated, TenantState
from advtest_ext.orchestrate.loop import (
    TenantScheduler,
    run_native_phase,
    run_sentinel_phase,
)
from advtest_ext.scoring.base import ScoreResult
from advtest_ext.scoring.objectives import objective_for

PROFILE = TargetProfileKey("gemini-1", "enterprise", "cfg")


def _sig(**axes: str) -> Signature:
    base = dict(framing=BASELINE, turn_order=BASELINE, persona=BASELINE, carrier_placement=BASELINE, escalation_speed=BASELINE)
    base.update(axes)
    return Signature(objective_class="canary_exfil", cross_app_hops_bucket="0", individual_id="h", **base)


@dataclass
class Variant:
    id: str
    signature: Signature


# --- Mock tenant / fixture / observer / materializer (the stage/cleanup path) ---


class MockTenant:
    """Planted artifacts; a failed carrier cleanup becomes 'stuck' (reset can't clear it)."""

    def __init__(self) -> None:
        self.docs: set[str] = set()
        self.stuck: set[str] = set()

    def plant(self, aid: str) -> None:
        self.docs.add(aid)

    def cleanup(self, aid: str, *, fail: bool) -> None:
        if fail:
            self.stuck.add(aid)  # cleanup_bound_carrier failed → residual
        else:
            self.docs.discard(aid)
            self.stuck.discard(aid)

    def reset(self) -> None:
        self.docs = set(self.stuck)  # reset restores clean state except genuinely stuck residue

    def is_clean(self) -> bool:
        return not self.docs

    def snapshot(self) -> TenantState:
        st = TenantState.empty()
        for aid in self.docs:
            st.add(FileCreated(file_id=aid))
        return st


class MockFixture:
    def __init__(self, tenant: MockTenant, *, scheduler: TenantScheduler | None = None) -> None:
        self.tenant = tenant
        self.reset_calls = 0
        self._scheduler = scheduler

    def reset(self) -> None:
        # If a scheduler is wired, prove the reset runs UNDER the profile lock.
        if self._scheduler is not None:
            got = self._scheduler.try_acquire(PROFILE)
            if got:
                self._scheduler.release(PROFILE)
                raise AssertionError("fixture reset ran without the profile lock held")
        self.reset_calls += 1
        self.tenant.reset()

    def is_clean(self) -> bool:
        return self.tenant.is_clean()


class MockObserver:
    def __init__(self, tenant: MockTenant) -> None:
        self.tenant = tenant

    def snapshot(self) -> TenantState:
        return self.tenant.snapshot()


class MockMaterializer:
    def __init__(self, tenant: MockTenant, *, cleanup_fails: bool = False) -> None:
        self.tenant = tenant
        self.cleanup_fails = cleanup_fails
        self.applied: list[str] = []

    async def apply(self, node: dict[str, Any]) -> Any:
        aid = f"doc-{node['id']}"
        self.tenant.plant(aid)
        self.applied.append(aid)
        return SimpleNamespace(bindings={"document_id": aid}, node_id=node["id"])

    async def cleanup(self, record: Any) -> None:
        if record is not None:
            self.tenant.cleanup(record.bindings["document_id"], fail=self.cleanup_fails)


def _deliver(sv: bool):  # type: ignore[no-untyped-def]
    def deliver(_v: Variant) -> tuple:
        result = ScoreResult(signal_verified=sv, judge_success=None, judge_confidence=0.0, leak_fraction=1.0 if sv else 0.0, refusal_style=None, evidence="", observation_path="ui")
        return result, obs_state(), objective_for("canary_exfil")
    return deliver


# --- Acceptance 1: bracket is finally-guaranteed even when scoring raises ------


def test_teardown_runs_even_when_scoring_raises() -> None:
    tenant = MockTenant()
    fixture = MockFixture(tenant)
    mat = MockMaterializer(tenant)
    archive = Archive(profile=PROFILE)

    def deliver(_v: Variant) -> tuple:
        raise RuntimeError("delivery blew up mid-score")

    out = asyncio.run(run_native_phase(
        TenantScheduler(), PROFILE, archive, [Variant("v1", _sig(framing="authority"))],
        deliver_and_score=deliver, fixture=fixture, observer=MockObserver(tenant), materializer=mat,
    ))
    assert fixture.reset_calls == 2  # setup + teardown ran despite the raise
    assert out.invalid_technical == 1 and len(out.requeued) == 1
    assert tenant.is_clean()  # materializer.cleanup ran in finally → carrier torn down


# --- Acceptance 2: failing carrier teardown → NEXT run invalid_technical ------


def test_failing_teardown_invalidates_next_run() -> None:
    tenant = MockTenant()
    fixture = MockFixture(tenant)
    mat = MockMaterializer(tenant, cleanup_fails=True)  # carrier teardown FAILS
    archive = Archive(profile=PROFILE)
    a = Variant("A", _sig(framing="authority"))
    b = Variant("B", _sig(framing="urgency"))

    out = asyncio.run(run_native_phase(
        TenantScheduler(), PROFILE, archive, [a, b],
        deliver_and_score=_deliver(sv=True), fixture=fixture, observer=MockObserver(tenant), materializer=mat,
    ))
    assert out.dirty_teardowns >= 1  # verify-clean caught A's stuck carrier
    assert b in out.requeued and out.invalid_technical >= 1  # B couldn't baseline
    # B's cell never updated (invalid_technical): still the uniform prior.
    b_cell = archive.cell(b.signature.cell_key())
    assert (b_cell.posterior.alpha, b_cell.posterior.beta) == (1.0, 1.0)


# --- Acceptance 3: clean teardown → empty delta, next baseline clean ---------


def test_clean_teardown_leaves_empty_delta() -> None:
    tenant = MockTenant()
    fixture = MockFixture(tenant)
    mat = MockMaterializer(tenant, cleanup_fails=False)
    archive = Archive(profile=PROFILE)
    variants = [Variant("A", _sig(framing="authority")), Variant("B", _sig(framing="urgency"))]

    out = asyncio.run(run_native_phase(
        TenantScheduler(), PROFILE, archive, variants,
        deliver_and_score=_deliver(sv=True), fixture=fixture, observer=MockObserver(tenant), materializer=mat,
    ))
    assert out.dirty_teardowns == 0  # every teardown verified clean
    assert out.updated == 2 and not out.requeued  # both baselined clean and scored
    assert tenant.is_clean()


# --- Acceptance 4: native + sentinel share ONE reset-of-record ---------------


def test_native_and_sentinel_share_one_reset_mechanism() -> None:
    from advtest_ext.drift.sentinel import Sentinel

    tenant = MockTenant()
    fixture = MockFixture(tenant)
    scheduler = TenantScheduler()
    archive = Archive(profile=PROFILE)

    asyncio.run(run_native_phase(
        scheduler, PROFILE, archive, [Variant("A", _sig(framing="authority"))],
        deliver_and_score=_deliver(sv=False), fixture=fixture, observer=MockObserver(tenant), materializer=MockMaterializer(tenant),
    ))
    after_native = fixture.reset_calls
    assert after_native >= 2  # native used setup_fixture + teardown_fixture

    run_sentinel_phase(
        scheduler, PROFILE, [Sentinel(cell_key=_sig(framing="authority").cell_key(), content_hash="h")],
        fixture=fixture, deliver_and_score=lambda _s: (True, "valid_success"),
    )
    # The SAME fixture's reset (Part B hooks) advanced again — one mechanism, no third path.
    assert fixture.reset_calls > after_native


# --- Acceptance 5: carrier re-materializes; semantic axis leaves doc body -----


def test_carrier_rematerializes_semantic_axis_does_not() -> None:
    from advtest_ext.guides.models import (
        Guide,
        GuideMutationOperator,
        GuideProvenance,
        GuideStep,
        GuideSuccess,
        GuideTarget,
    )
    from advtest_ext.mutate.instantiate import AxisPatch, instantiate

    guide = Guide(
        id="g", objective="summarise", target=GuideTarget(app="google_docs"),
        success=GuideSuccess(signals=[{"kind": "canary_appears", "name": "c", "canary": "ZZ"}]),
        provenance=GuideProvenance(source="t"), carrier_payload="PAYLOAD body text",
        invariants=["keep"], setup_steps=[GuideStep(id="s1", intent="plant", app="google_docs", operation="open_document", params={})],
        mutation_operators=[
            GuideMutationOperator(name="reframe", dimension="framing", description="f", mutable_fields=["objective"], cell_values=["urgency"]),
            GuideMutationOperator(name="encode", dimension="carrier_placement", description="c", mutable_fields=["carrier_payload"], cell_values=["rot13"]),
        ],
    )
    parent = _sig()

    # carrier_placement → the planted carrier body is re-materialized (changes).
    carrier = instantiate(guide, parent, "carrier_placement", "rot13")
    assert carrier.guide.carrier_payload != guide.carrier_payload

    # semantic axis (framing) → touches the conversational field, NOT the doc body.
    def strat(g: Any, op: Any, fv: str) -> AxisPatch:
        return AxisPatch(artifact_patch={"objective": f"[{fv}] {g.objective}"}, claimed_realized_axes={"framing": fv})

    semantic = instantiate(guide, parent, "framing", "urgency", strategist=strat)
    assert semantic.guide.carrier_payload == guide.carrier_payload  # doc body unchanged


# --- Acceptance 6: mutual exclusion — bracket runs under the profile lock -----


def test_bracket_runs_under_profile_lock() -> None:
    tenant = MockTenant()
    scheduler = TenantScheduler()
    # This fixture's reset raises if the profile lock is NOT held when it runs.
    fixture = MockFixture(tenant, scheduler=scheduler)
    archive = Archive(profile=PROFILE)

    # Completes without AssertionError → every setup/teardown reset ran under the lock.
    asyncio.run(run_native_phase(
        scheduler, PROFILE, archive, [Variant("A", _sig(framing="authority"))],
        deliver_and_score=_deliver(sv=False), fixture=fixture, observer=MockObserver(tenant), materializer=MockMaterializer(tenant),
    ))
    assert fixture.reset_calls >= 2
    # A different profile's lock is independent (could run concurrently).
    other = TargetProfileKey("gemini-1", "enterprise", "OTHER")
    assert scheduler.try_acquire(other) is True
    scheduler.release(other)
