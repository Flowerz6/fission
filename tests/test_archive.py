"""MAP-Elites vulnerability-fingerprint archive — acceptance + unit tests.

Covers the six acceptance tests from the Part-A spec plus the sub-step-0
``run_outcome`` classifier. Offline: builds fake traces / an in-memory DB; the
one real-data invariant test copies the repo ``advtest.db`` and skips if absent.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from advtest_ext.archive.fingerprint import (
    Archive,
    Fingerprint,
    TargetProfileKey,
    load_snapshot,
    persist_archive,
    snapshot,
    store_snapshot,
)
from advtest_ext.archive.posterior import BetaPosterior, apply_observation
from advtest_ext.archive.signature import (
    BASELINE,
    build_node_index,
    coarse_projection,
    count_cross_app_transitions,
    hops_bucket,
    signature,
)
from advtest_ext.core.orch_trace_meta import run_outcome, run_outcome_for_trace
from advtest_ext.storage.database import make_engine
from advtest_ext.storage.models import JudgeOnlySignal, Run, Trace

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeTrace:
    """A minimal stand-in for a persisted Trace (attribute access only)."""

    def __init__(
        self,
        *,
        trace_id: str,
        node_id: str | None = None,
        parent_id: str | None = None,
        dimension: str | None = None,
        feature_value: str | None = None,
        turn: int = 1,
        behavior_id: str = "sheets_skill_leak",
        verdict: str | None = "refused",
        technique_id: str | None = None,
        payload_id: str = "p",
    ) -> None:
        self.id = trace_id
        self.behavior_id = behavior_id
        self.verdict = verdict
        self.depth = turn
        self.technique_id = technique_id
        self.payload_id = payload_id
        mutation = {"dimension": dimension, "feature_value": feature_value} if dimension else {}
        self.extra_metadata = {
            "tap_crescendo": {
                "node_id": node_id,
                "parent_id": parent_id,
                "turn": turn,
                "mutation": mutation,
            }
        }
        self.signals: list[Any] = []
        self.judge_scores: list[Any] = []


GMAIL_TO_DOCS = {"phases": [{"target_name": "gmail"}, {"target_name": "google_docs_multiturn"}]}
SINGLE_APP = {"phases": [{"target_name": "gmail"}]}


# ---------------------------------------------------------------------------
# Sub-step 0 — run_outcome()
# ---------------------------------------------------------------------------


def test_run_outcome_trichotomy() -> None:
    assert run_outcome("success") == "valid_success"
    assert run_outcome("refused") == "valid_refusal"
    assert run_outcome("partial") == "valid_refusal"
    # invalid_technical dominates everything.
    assert run_outcome("error") == "invalid_technical"
    assert run_outcome("success", error_kind="bot_detected") == "invalid_technical"
    assert run_outcome("success", error_kind="selector_drift") == "invalid_technical"
    assert run_outcome("success", judge_error=True) == "invalid_technical"
    assert run_outcome(None) == "invalid_technical"
    # A non-technical attack_error (compose failure) is NOT auto-invalid here.
    assert run_outcome("refused", error_kind="attack_error") == "valid_refusal"


def test_run_outcome_for_trace_reads_metadata() -> None:
    ok = FakeTrace(trace_id="t", verdict="success")
    assert run_outcome_for_trace(ok) == "valid_success"
    judge_err = FakeTrace(trace_id="t", verdict="success")
    judge_err.extra_metadata["tap_crescendo"]["judge_error"] = True
    assert run_outcome_for_trace(judge_err) == "invalid_technical"
    bot = FakeTrace(trace_id="t", verdict="success")
    bot.extra_metadata["error_kind"] = "bot_detected"
    assert run_outcome_for_trace(bot) == "invalid_technical"


# ---------------------------------------------------------------------------
# Acceptance 1 — signature reads mutation; wording ~ individual, carrier ~ cell
# ---------------------------------------------------------------------------


def _linear_tree() -> list[FakeTrace]:
    # n0 (root) -> n1 (persona=cfo) -> n2 (carrier_placement=footer)
    return [
        FakeTrace(trace_id="t0", node_id="n0", parent_id=None, turn=1),
        FakeTrace(
            trace_id="t1",
            node_id="n1",
            parent_id="n0",
            dimension="persona",
            feature_value="cfo",
            turn=1,
        ),
        FakeTrace(
            trace_id="t2",
            node_id="n2",
            parent_id="n1",
            dimension="carrier_placement",
            feature_value="footer",
            turn=1,
        ),
        # a second turn of the leaf that only changes wording
        FakeTrace(
            trace_id="t2w",
            node_id="n2",
            parent_id="n1",
            dimension="carrier_placement",
            feature_value="footer",
            turn=2,
            verdict="success",
        ),
    ]


def test_signature_reads_mutation_and_walks_ancestry() -> None:
    traces = _linear_tree()
    index = build_node_index(traces)
    leaf = signature(traces[2], node_index=index, run_config=SINGLE_APP)
    # Ancestry recovered both the leaf's own carrier and the parent's persona.
    assert leaf.carrier_placement == "footer"
    assert leaf.persona == "cfo"
    assert leaf.framing == BASELINE  # never set on the path
    assert leaf.recovered_axes == 2


def test_wording_shares_cell_but_distinct_individual() -> None:
    traces = _linear_tree()
    index = build_node_index(traces)
    a = signature(traces[2], node_index=index, run_config=SINGLE_APP)  # leaf turn 1
    # A wording-only variant: same cumulative non-wording vector.
    b_meta = FakeTrace(
        trace_id="tw",
        node_id="n2",
        parent_id="n1",
        dimension="carrier_placement",
        feature_value="footer",
        turn=9,
    )
    b_meta.extra_metadata["tap_crescendo"]["mutation"] = {
        "dimension": "carrier_placement",
        "feature_value": "footer",
    }
    b = signature(b_meta, node_index=index, run_config=SINGLE_APP)
    assert a.cell_key() == b.cell_key()  # wording never enters the key

    arch = Archive(profile=TargetProfileKey("m", "t", "s"))
    arch.observe(a, outcome="valid_refusal", signal_verified=False)
    b_diff_wording = signature(b_meta, node_index=index, run_config=SINGLE_APP)
    object.__setattr__(b_diff_wording, "wording", "reworded")
    object.__setattr__(b_diff_wording, "individual_id", "other")
    arch.observe(b_diff_wording, outcome="valid_refusal", signal_verified=False)
    cell = arch.cell(a.cell_key())
    assert len(cell.individuals) == 2  # same cell, two individuals


def test_changing_carrier_placement_changes_cell() -> None:
    traces = _linear_tree()
    index = build_node_index(traces)
    base = signature(traces[2], node_index=index, run_config=SINGLE_APP)
    # Same tree but leaf mutates a different carrier value.
    alt = [
        FakeTrace(trace_id="t0", node_id="n0", parent_id=None, turn=1),
        FakeTrace(
            trace_id="t1",
            node_id="n1",
            parent_id="n0",
            dimension="persona",
            feature_value="cfo",
            turn=1,
        ),
        FakeTrace(
            trace_id="t2",
            node_id="n2",
            parent_id="n1",
            dimension="carrier_placement",
            feature_value="comment",
            turn=1,
        ),
    ]
    alt_sig = signature(alt[2], node_index=build_node_index(alt), run_config=SINGLE_APP)
    assert base.cell_key() != alt_sig.cell_key()


def test_coarse_projection() -> None:
    traces = _linear_tree()
    sig = signature(traces[2], node_index=build_node_index(traces), run_config=SINGLE_APP)
    assert coarse_projection(sig) == (sig.objective_class, "footer")
    root = signature(traces[0], node_index=build_node_index(traces), run_config=SINGLE_APP)
    assert coarse_projection(root) == (root.objective_class, None)  # baseline carrier → None


# ---------------------------------------------------------------------------
# Acceptance 2 — cross_app_hops from phases, NOT traces.depth
# ---------------------------------------------------------------------------


def test_cross_app_hops_from_phases() -> None:
    assert hops_bucket(count_cross_app_transitions(GMAIL_TO_DOCS)) == "1"
    assert hops_bucket(count_cross_app_transitions(SINGLE_APP)) == "0"
    assert hops_bucket(count_cross_app_transitions({})) == "0"
    three = {
        "phases": [
            {"target_name": "gmail"},
            {"target_name": "google_docs"},
            {"target_name": "google_sheets"},
        ]
    }
    assert hops_bucket(count_cross_app_transitions(three)) == "2+"


def test_cross_app_hops_ignores_depth() -> None:
    # A high per-turn depth on a single-app run must NOT inflate hops.
    deep = FakeTrace(trace_id="t", node_id="n5", parent_id=None, turn=42)
    sig = signature(deep, run_config=SINGLE_APP)
    assert sig.cross_app_hops_bucket == "0"
    # The Gmail→Docs synthetic run exercises the ≥1 code path.
    sig2 = signature(deep, run_config=GMAIL_TO_DOCS)
    assert sig2.cross_app_hops_bucket == "1"


# ---------------------------------------------------------------------------
# Acceptance 3 — one deterministic success channel
# ---------------------------------------------------------------------------


def test_signal_verified_success_moves_mean_and_shrinks_ci() -> None:
    p = BetaPosterior()
    before_mean = p.mean()
    before_ci = p.credible_interval()
    apply_observation(p, outcome="valid_success", signal_verified=True)
    assert (p.alpha, p.beta) == (2.0, 1.0)
    after_ci = p.credible_interval()
    assert p.mean() > before_mean
    assert (after_ci.high - after_ci.low) < (before_ci.high - before_ci.low)


def test_judge_only_success_leaves_posterior_untouched_and_is_banked() -> None:
    arch = Archive(profile=TargetProfileKey("m", "t", "s"))
    sig = signature(
        FakeTrace(
            trace_id="t",
            node_id="n1",
            parent_id=None,
            dimension="carrier_placement",
            feature_value="footer",
        ),
        node_index=build_node_index(
            [
                FakeTrace(
                    trace_id="t",
                    node_id="n1",
                    parent_id=None,
                    dimension="carrier_placement",
                    feature_value="footer",
                )
            ]
        ),
        run_config=SINGLE_APP,
    )
    result = arch.observe(sig, outcome="valid_success", signal_verified=False, judge_confidence=0.9)
    assert result.effect == "judge_only"
    cell = arch.cell(sig.cell_key())
    assert (cell.posterior.alpha, cell.posterior.beta) == (1.0, 1.0)  # untouched
    assert len(arch.judge_only) == 1 and arch.judge_only[0].judge_confidence == 0.9

    # And it persists to the judge_only_signals table, not the posterior.
    engine = make_engine("sqlite:///:memory:")
    with Session(engine) as session:
        persist_archive(session, arch)
        session.commit()
        assert session.query(JudgeOnlySignal).count() == 1


def test_invalid_technical_alters_no_posterior() -> None:
    p = BetaPosterior()
    r = apply_observation(p, outcome="invalid_technical", signal_verified=True)
    assert r.effect == "skipped"
    assert (p.alpha, p.beta) == (1.0, 1.0)


# ---------------------------------------------------------------------------
# Acceptance 5 — carry-forward seeding
# ---------------------------------------------------------------------------


def test_seed_from_previous_version_scales_ess_and_keeps_undecayed_ref() -> None:
    prev = BetaPosterior(alpha=8.0, beta=2.0)
    seeded = BetaPosterior.seed_from_previous_version(prev, decay=0.4)
    assert seeded.effective_sample_size() == pytest.approx(0.4 * prev.effective_sample_size())
    assert seeded.mean() == pytest.approx(prev.mean())  # mean preserved
    assert seeded.prev_posterior_ref == (8.0, 2.0)  # undecayed belief kept for drift
    # Fresh weight-1 evidence can overturn the decayed prior.
    for _ in range(6):
        seeded.update(success_signal_verified=True)
    assert seeded.mean() > prev.mean()


# ---------------------------------------------------------------------------
# Acceptance 6 — snapshot round-trips (payload + DB)
# ---------------------------------------------------------------------------


def _small_archive() -> Archive:
    arch = Archive(profile=TargetProfileKey("gemini-x", "enterprise", "cfg0"))
    traces = _linear_tree()
    index = build_node_index(traces)
    sig = signature(traces[2], node_index=index, run_config=GMAIL_TO_DOCS)
    arch.observe(sig, outcome="valid_success", signal_verified=True)
    arch.observe(sig, outcome="valid_refusal", signal_verified=False)
    return arch


def test_snapshot_round_trips_in_memory() -> None:
    fp = snapshot(_small_archive(), taken_at=datetime.now(UTC))
    assert Fingerprint.from_payload(fp.to_payload()).to_payload() == fp.to_payload()


def test_snapshot_round_trips_through_db() -> None:
    arch = _small_archive()
    fp = snapshot(arch, taken_at=datetime.now(UTC))
    engine = make_engine("sqlite:///:memory:")
    with Session(engine) as session:
        persist_archive(session, arch)
        snap_id = store_snapshot(session, fp)
        session.commit()
        reloaded = load_snapshot(session, snap_id)
    assert reloaded.to_payload() == fp.to_payload()


# ---------------------------------------------------------------------------
# Acceptance 4 — backfill Coarse/Medium/dropped histogram
# ---------------------------------------------------------------------------


def _insert_run_with_leaf(
    session: Session, *, run_id: str, guide_id: str | None, leaf_chain: list[dict[str, Any]]
) -> None:
    """Insert a Run and a single root→leaf tap_crescendo path as Trace rows."""
    session.add(
        Run(
            id=run_id,
            target_name="gmail_multiturn",
            strategy="mcts",
            guide_id=guide_id,
            config={},
            summary={},
        )
    )
    for node in leaf_chain:
        session.add(
            Trace(
                id=f"{run_id}-{node['node_id']}",
                run_id=run_id,
                behavior_id="sheets_skill_leak",
                attack_name="tap_crescendo",
                technique_id=node.get("technique_id"),
                target_name="gmail_multiturn",
                payload_id="p",
                payload_text="x",
                raw_request="q",
                raw_response="a",
                raw_response_data={},
                verdict="refused",
                depth=1,
                extra_metadata={
                    "tap_crescendo": {
                        "node_id": node["node_id"],
                        "parent_id": node.get("parent_id"),
                        "turn": 1,
                        "mutation": (
                            {"dimension": node["dimension"], "feature_value": node["feature_value"]}
                            if node.get("dimension")
                            else {}
                        ),
                    }
                },
            )
        )


def test_backfill_bins_coarse_and_medium_and_drops() -> None:
    from advtest_ext.archive.backfill import backfill_from_history

    engine = make_engine("sqlite:///:memory:")
    with Session(engine) as session:
        # Coarse: leaf recovers objective + carrier only.
        _insert_run_with_leaf(
            session,
            run_id="r-coarse",
            guide_id="g",
            leaf_chain=[
                {"node_id": "n0", "parent_id": None},
                {
                    "node_id": "n1",
                    "parent_id": "n0",
                    "dimension": "carrier_placement",
                    "feature_value": "footer",
                },
            ],
        )
        # Medium: leaf recovers carrier + framing + persona (≥2 more → ≥4 descriptors).
        _insert_run_with_leaf(
            session,
            run_id="r-medium",
            guide_id="g",
            leaf_chain=[
                {"node_id": "n0", "parent_id": None},
                {
                    "node_id": "n1",
                    "parent_id": "n0",
                    "dimension": "carrier_placement",
                    "feature_value": "footer",
                },
                {
                    "node_id": "n2",
                    "parent_id": "n1",
                    "dimension": "framing",
                    "feature_value": "urgency",
                },
                {
                    "node_id": "n3",
                    "parent_id": "n2",
                    "dimension": "persona",
                    "feature_value": "cfo",
                },
            ],
        )
        # Dropped (null carrier): leaf mutates only framing, no technique_id.
        _insert_run_with_leaf(
            session,
            run_id="r-drop",
            guide_id="g",
            leaf_chain=[
                {"node_id": "n0", "parent_id": None},
                {
                    "node_id": "n1",
                    "parent_id": "n0",
                    "dimension": "framing",
                    "feature_value": "urgency",
                },
            ],
        )
        # Forward-only skip: a run with no guide_id.
        _insert_run_with_leaf(
            session,
            run_id="r-noguide",
            guide_id=None,
            leaf_chain=[{"node_id": "n0", "parent_id": None}],
        )
        session.commit()

        _arch, report = backfill_from_history(
            session,
            profile=TargetProfileKey("hist", "ent", "h0"),
            guides_by_id={},
            behavior_categories={},
        )

    assert report.coarse == 1
    assert report.medium == 1
    assert report.dropped_null_carrier == 1
    assert report.dropped_missing_objective_class == 0
    assert report.forward_only_skipped == 1  # the one guideless run's trace


@pytest.mark.skipif(not Path("advtest.db").exists(), reason="no repo advtest.db to backfill")
def test_backfill_real_db_invariants(tmp_path: Path) -> None:
    """The contract-predicted invariants over the real historical DB: the skip
    count equals traces in guideless runs, and ≈0 rows drop for a missing
    objective_class (behavior_id is NOT NULL). The Coarse/Medium split is
    data-determined and NOT asserted."""
    import yaml

    from advtest_ext.archive.backfill import backfill_from_history
    from advtest_ext.guides.corpus import load_guides

    copy = tmp_path / "advtest_copy.db"
    shutil.copy("advtest.db", copy)
    engine = make_engine(f"sqlite:///{copy}")
    categories = {
        b["id"]: b["category"]
        for b in yaml.safe_load(Path("advtest_ext/core/seed_behaviors.yaml").read_text())
    }
    guides_by_id = {g.id: g for g in load_guides("guides")}
    with Session(engine) as session:
        # Expected skip = traces belonging to runs with no guide_id.
        from advtest_ext.storage.models import Run as RunModel
        from advtest_ext.storage.models import Trace as TraceModel

        guideless_runs = {
            r.id
            for r in session.query(RunModel)
            .filter((RunModel.guide_id.is_(None)) | (RunModel.guide_id == ""))
            .all()
        }
        expected_skip = (
            session.query(TraceModel).filter(TraceModel.run_id.in_(guideless_runs)).count()
        )

        _arch, report = backfill_from_history(
            session,
            profile=TargetProfileKey("gemini-hist", "enterprise", "h0"),
            guides_by_id=guides_by_id,
            behavior_categories=categories,
        )

    assert report.forward_only_skipped == expected_skip
    assert report.dropped_missing_objective_class == 0
