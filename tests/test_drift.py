"""Part E — target-version drift: acceptance + unit tests (mock, no live tenant)."""

from __future__ import annotations

from datetime import UTC, datetime

from advtest_ext.archive.fingerprint import (
    Archive,
    CellStat,
    Fingerprint,
    IndividualStat,
    TargetProfileKey,
    encode_cell_key,
)
from advtest_ext.archive.posterior import BetaPosterior
from advtest_ext.archive.signature import BASELINE, Signature
from advtest_ext.drift.attribution import attribute_drift
from advtest_ext.drift.calibrate import DriftThresholds, alerts_enabled, calibrate_false_drift
from advtest_ext.drift.changepoint import cusum, posterior_drift, posterior_drift_for_cell
from advtest_ext.drift.fingerprint_diff import fingerprint_divergence, regressed_cells
from advtest_ext.drift.guards import (
    DriftAlert,
    judge_drift,
    mark_leads_suspect,
    search_health,
    suppress_search_drift,
    tenant_guard_ok,
)
from advtest_ext.drift.sentinel import (
    Sentinel,
    run_sentinel_cycle,
    select_sentinels,
    sentinel_series,
)
from advtest_ext.mutate.effects import AxisEffect, BoundaryModel
from advtest_ext.mutate.novelty import NoveltyDecision


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


class _Fixture:
    def __init__(self, *, clean: bool = True) -> None:
        self._clean = clean
        self.reset_calls = 0

    def reset(self) -> None:
        self.reset_calls += 1

    def is_clean(self) -> bool:
        return self._clean


# --- Acceptance 1: sentinel resets per sentinel; change-point near K ---------


def test_sentinel_resets_and_changepoint_detects_flip() -> None:
    cell = _sig(framing="authority")
    sentinels = [Sentinel(cell_key=cell.cell_key(), content_hash="h1")]
    fixture = _Fixture(clean=True)
    flip_cycle = 6

    def run_cycle(cycle_index: int) -> list:
        def deliver(_s: Sentinel) -> tuple[bool, str]:
            # target flips to vulnerable at flip_cycle
            return (
                cycle_index >= flip_cycle,
                "valid_success" if cycle_index >= flip_cycle else "valid_refusal",
            )

        return run_sentinel_cycle(sentinels, fixture=fixture, deliver_and_score=deliver)

    cycles = [run_cycle(i) for i in range(12)]
    # fixture reset ran per sentinel per cycle (setup + teardown = 2 each).
    assert fixture.reset_calls == 12 * len(sentinels) * 2

    series = sentinel_series(cycles, cell.cell_key())
    cp = cusum(series, baseline_rate=0.0, threshold=2.0, slack=0.1)
    assert cp.detected and cp.direction == "more_vulnerable"
    assert flip_cycle <= cp.index <= flip_cycle + 3  # detected near K


# --- Acceptance 2: Q4 — undecayed prior, decayed under-reports ---------------


def test_q4_uses_undecayed_prior_decayed_underreports() -> None:
    # Old belief: strongly robust (low success). 2/40.
    prev = BetaPosterior(alpha=2.0, beta=40.0)
    decayed = BetaPosterior.seed_from_previous_version(prev, decay=0.1)  # same mean, wide CI
    # Fresh evidence: now clearly vulnerable.
    new_alpha, new_beta = 8.0, 2.0

    undecayed_drift = posterior_drift(
        new_alpha=new_alpha, new_beta=new_beta, prior_alpha=prev.alpha, prior_beta=prev.beta
    )
    decayed_drift = posterior_drift(
        new_alpha=new_alpha, new_beta=new_beta, prior_alpha=decayed.alpha, prior_beta=decayed.beta
    )

    # Same mean shift, but the decayed prior's inflated variance DEFLATES the z-score.
    assert abs(undecayed_drift.magnitude - decayed_drift.magnitude) < 1e-9
    assert decayed_drift.z < undecayed_drift.z  # decayed UNDER-reports
    assert undecayed_drift.direction == "more_vulnerable"

    # The cell helper reads ONLY the undecayed prev_alpha/prev_beta.
    class _Cell:
        prev_alpha, prev_beta = prev.alpha, prev.beta
        alpha, beta = decayed.alpha, decayed.beta  # the decayed seed — must be ignored

    from_cell = posterior_drift_for_cell(_Cell(), new_alpha=new_alpha, new_beta=new_beta)
    assert from_cell.z == undecayed_drift.z  # used undecayed, not the decayed live posterior


# --- Acceptance 3: search-coverage collapse suppresses target alert ----------


def test_search_collapse_suppresses_target_alert() -> None:
    archive = Archive(profile=TargetProfileKey("m", "t", "s"))
    kept = _sig(framing="authority")
    archive.observe(kept, outcome="valid_refusal", signal_verified=False)
    dropped = _sig(framing="urgency")  # was populated last window, gone now

    health = search_health(
        archive,
        prev_coverage=10,
        prev_populated_cells=frozenset({kept.cell_key(), dropped.cell_key()}),
    )
    assert health.collapsed  # coverage fell 10 -> 1
    assert dropped.cell_key() in health.collapsed_cells

    alerts = [DriftAlert(cell_key=dropped.cell_key(), magnitude=0.5, direction="more_vulnerable")]
    suppressed = suppress_search_drift(alerts, health)
    assert suppressed[0].source == "search"  # not a target alert


# --- Acceptance 4: judge drift marks leads suspect, no target alert ----------


def test_judge_drift_marks_leads_suspect() -> None:
    before = {"g1": "refused", "g2": "success", "g3": "refused"}
    after = {
        "g1": "success",
        "g2": "success",
        "g3": "success",
    }  # 2/3 verdicts moved on FIXED inputs
    drift = judge_drift(before, after)
    assert drift.shifted and drift.shift_rate > 0.5

    leads = mark_leads_suspect([_sig(framing="authority").cell_key()], drift)
    assert leads[0].suspect is True and "judge drift" in leads[0].note


# --- Acceptance 5: dirty fixture → invalid_technical, not drift ---------------


def test_dirty_fixture_is_invalid_technical() -> None:
    cell = _sig(framing="authority")
    sentinels = [Sentinel(cell_key=cell.cell_key(), content_hash="h1")]
    fixture = _Fixture(clean=False)  # reset never produces a clean tenant

    def deliver(_s: Sentinel) -> tuple[bool, str]:
        return True, "valid_success"  # would look like drift if trusted

    obs = run_sentinel_cycle(sentinels, fixture=fixture, deliver_and_score=deliver)[0]
    assert obs.effective_outcome == "invalid_technical"
    assert tenant_guard_ok(obs) is False
    assert obs.counts is False  # never enters a drift series


# --- Acceptance 6: axis attribution names the axis; refuses entangled/thin ----


def test_axis_attribution_names_axis_and_refuses_entangled() -> None:
    old = TargetProfileKey("gemini-1", "ent", "cfg")
    new = TargetProfileKey("gemini-2", "ent", "cfg")
    regressed = [encode_cell_key(_sig(framing="authority", escalation_speed="fast").cell_key())]
    boundary = BoundaryModel(
        main_effects=[
            AxisEffect(
                "framing",
                "authority",
                0.7,
                0.6,
                0.5,
                0.9,
                12.0,
                8.0,
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
                5.0,
                3.0,
                low_confidence=True,
                entangled=True,
                basis="self_report",
            ),
        ]
    )
    report = attribute_drift(old, new, regressed, boundary)
    asserted = {a.axis for a in report.asserted_axes()}
    assert "framing" in asserted  # named
    esc = next(a for a in report.axis_patterns if a.axis == "escalation_speed")
    assert esc.asserted is False and esc.caveat and "entangled" in esc.caveat  # refused


# --- Acceptance 7: fingerprint divergence segregates by resolution -----------


def _fp(profile: TargetProfileKey, cells: dict) -> Fingerprint:
    stats = {
        key: CellStat(cell_key=key, mean=mean, ess=10.0, resolution=res)
        for key, (mean, res) in cells.items()
    }
    return Fingerprint(profile=profile, taken_at=datetime.now(UTC), cells=stats)


def test_fingerprint_divergence_segregates_resolution() -> None:
    prof = TargetProfileKey("m", "t", "s")
    native_key = _sig(framing="authority").cell_key()
    coarse_key = _sig(framing="urgency").cell_key()
    old = _fp(prof, {native_key: (0.1, "native"), coarse_key: (0.1, "coarse")})
    new = _fp(prof, {native_key: (0.9, "native"), coarse_key: (0.15, "coarse")})

    diff = fingerprint_divergence(old, new)
    assert "native" in diff.by_resolution and "coarse" in diff.by_resolution
    # native moved hard, coarse barely — segregated, not blended.
    assert diff.by_resolution["native"].psi > diff.by_resolution["coarse"].psi
    # regressed_cells only returns the native cell at native resolution.
    assert regressed_cells(old, new, resolution="native") == [native_key]
    assert regressed_cells(old, new, resolution="coarse") == []


# --- Acceptance 8: false-drift calibration on a stable mock ------------------


def test_false_drift_calibration_holds_budget() -> None:
    # A stable mock: each cell's sentinel series is noisy around a fixed rate, no change.
    import random

    rng = random.Random(7)
    baseline = 0.3
    stable_series = [[rng.random() < baseline for _ in range(24)] for _ in range(40)]

    thresholds, report = calibrate_false_drift(
        stable_series, baseline_rate=baseline, target_rate=0.05
    )
    assert report.false_alarm_rate <= 0.05
    assert report.within_budget and thresholds.calibrated
    assert alerts_enabled(thresholds) is True
    # An un-calibrated threshold set must NOT enable alerts.
    assert alerts_enabled(DriftThresholds()) is False


# --- extra: sentinel selection is frozen/stable -----------------------------


def test_sentinel_selection_is_stable() -> None:
    archive = Archive(profile=TargetProfileKey("m", "t", "s"))
    key = _sig(framing="authority").cell_key()
    cell = archive.cell(key)
    cell.individuals[("hb", "w")] = IndividualStat(individual_id="hb", wording="w")
    cell.individuals[("ha", "w")] = IndividualStat(individual_id="ha", wording="w")
    first = select_sentinels(archive)
    assert first[0].content_hash == "ha"  # deterministic (min)
    # A prior selection is preserved even if a smaller id appears later.
    preserved = select_sentinels(archive, existing={key: "hb"})
    assert preserved[0].content_hash == "hb"


def test_search_health_novelty_collapse() -> None:
    archive = Archive(profile=TargetProfileKey("m", "t", "s"))
    archive.observe(_sig(framing="authority"), outcome="valid_refusal", signal_verified=False)
    decisions = [NoveltyDecision(admit=False, reason="dup") for _ in range(9)] + [
        NoveltyDecision(admit=True, reason="new")
    ]
    health = search_health(archive, novelty_decisions=decisions)
    assert health.novelty_acceptance_rate == 0.1
    assert health.collapsed  # novelty acceptance below floor
