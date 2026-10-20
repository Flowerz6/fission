"""Tests for ``advtest_ext.dashboard``: every page renders 200 with
seeded data, the heatmap shows up where it should, the HTMX
filter on /findings returns just the table partial, and the PDF
route returns a 501 with an install hint when WeasyPrint isn't on
the path.

We seed an in-memory SQLite via the storage repository helpers so
the test fixtures stay pinned to the actual orchestrator-side write
path — if the orchestrator's columns drift, these tests notice.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from advtest_ext.core.models import (
    AttackTrace,
    JudgeScore,
    Payload,
    SummaryStats,
)
from advtest_ext.dashboard import create_app
from advtest_ext.storage import (
    enqueue_hitl,
    finalize_run,
    insert_trace,
    make_engine,
    make_session_factory,
    promote_finding,
    session_scope,
    upsert_run,
)

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


def _seed_run(factory: sessionmaker[Session]) -> None:
    """Seed one run with two traces (one success, one refused), one
    finding, and one HITL item — enough surface for every page to
    render non-trivially."""
    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id="run-seed",
            target_name="StubDocumentTarget",
            strategy="grid",
        )
        for i, verdict in enumerate(["success", "refused"]):
            trace = AttackTrace(
                id=f"t{i}",
                run_id="run-seed",
                behavior_id=f"exfil_test_{i}",
                payload=Payload(
                    id=f"p{i}",
                    text="payload",
                    modality="text",
                    generator_name="static_payload",
                ),
                target_name="StubDocumentTarget",
                raw_request="please summarise",
                raw_response="ok" if verdict == "refused" else "BAD",
                judge_scores=[
                    JudgeScore(
                        judge_name="heuristic",
                        score=0.9 if verdict == "success" else 0.1,
                        flagged=verdict == "success",
                        reasoning="x",
                        judge_version="0.1",
                    )
                ],
                verdict=verdict,  # type: ignore[arg-type]
            )
            insert_trace(s, run_id="run-seed", trace=trace)
        promote_finding(
            s,
            run_id="run-seed",
            trace_id="t0",
            severity="high",
            title="Static payload landed",
            description="Static payload was treated as instruction.",
        )
        enqueue_hitl(
            s,
            run_id="run-seed",
            trace_id="t1",
            reason="judge spread 0.6",
            disagreement=0.6,
        )
        finalize_run(
            s,
            run_id="run-seed",
            summary=SummaryStats(
                total_traces=2,
                successful_traces=1,
                overall_asr=0.5,
            ),
        )


@pytest.fixture
def client() -> Iterator[TestClient]:
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    _seed_run(factory)
    app = create_app(engine=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def empty_client() -> Iterator[TestClient]:
    """Dashboard with NO seeded data — locks the empty-state copy."""
    engine = make_engine("sqlite:///:memory:")
    app = create_app(engine=engine)
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_blind_env() -> Iterator[None]:
    """Keep the blind-review env knob from leaking across tests."""
    import os

    yield
    os.environ.pop("ADVTEST_HITL_BLIND_PCT", None)


def test_healthz_liveness(empty_client: TestClient) -> None:
    resp = empty_client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_readyz_reports_db_ok(empty_client: TestClient) -> None:
    resp = empty_client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["db"] == "ok"


def test_dashboard_auth_token_gate() -> None:
    engine = make_engine("sqlite:///:memory:")
    app = create_app(engine=engine, auth_token="s3cr3t")
    with TestClient(app) as c:
        # A normal route is gated: no header / wrong token → 401.
        assert c.get("/runs").status_code == 401
        assert c.get("/runs", headers={"Authorization": "Bearer nope"}).status_code == 401
        # Correct bearer token → allowed.
        assert c.get("/runs", headers={"Authorization": "Bearer s3cr3t"}).status_code == 200
        # Probes stay open so the orchestrator / LB can reach them unauthenticated.
        assert c.get("/healthz").status_code == 200
        assert c.get("/readyz").status_code == 200


def test_dashboard_no_auth_by_default(empty_client: TestClient) -> None:
    # No token configured (fixture) → routes are open (dev/CI default).
    assert empty_client.get("/runs").status_code == 200


# ----------------------------------------------------------------------
# Page-level smoke tests: every required URL returns 200
# ----------------------------------------------------------------------


def test_runs_index_renders(client: TestClient) -> None:
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert "run-seed" in resp.text
    assert "<h1>Runs</h1>" in resp.text


def test_zero_asr_runs_hidden_from_overview_and_success_filter() -> None:
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    _seed_run(factory)
    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id="run-zero-asr",
            target_name="StubDocumentTarget",
            strategy="grid",
        )
        finalize_run(
            s,
            run_id="run-zero-asr",
            summary=SummaryStats(
                total_traces=4,
                successful_traces=0,
                overall_asr=0.0,
            ),
        )

    app = create_app(engine=engine)
    with TestClient(app) as c:
        runs_body = c.get("/runs").text
        runs_success_body = c.get("/runs?only=success").text
        overview_body = c.get("/").text

    # The run history shows EVERY run by default — a run is visible the moment it
    # exists, including a finished no-success run.
    assert "run-seed" in runs_body
    assert "run-zero-asr" in runs_body
    # ...but ?only=success narrows to runs with at least one green.
    assert "run-seed" in runs_success_body
    assert "run-zero-asr" not in runs_success_body
    # The presentation-facing overview omits zero-ASR runs.
    assert "run-seed" in overview_body
    assert "run-zero-asr" not in overview_body


def test_root_serves_overview_command_center(client: TestClient) -> None:
    """``/`` now serves the Overview command center (not the runs list).
    It links to the latest run without promoting its ASR as a global KPI."""
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert '<h1 class="sr-only">Overview</h1>' in body
    assert "Attack success rate · latest run" not in body
    assert "run-seed" in body  # latest-run context line links to it
    assert "Layered oracle" in body  # oracle pipeline section


def test_overview_empty_state(empty_client: TestClient) -> None:
    resp = empty_client.get("/")
    assert resp.status_code == 200
    assert "No runs to summarise" in resp.text


def test_runs_show_renders_findings_without_empty_charts(client: TestClient) -> None:
    resp = client.get("/runs/run-seed")
    assert resp.status_code == 200
    body = resp.text
    # Missing technique metadata does not produce an empty comparison.
    assert "Heatmap, technique" not in body
    assert "Full transcript" in body
    # Behaviour ids bucket by prefix; "exfil" should land as a column.
    assert "exfil" in body
    # Top-findings card surfaces the seeded trace details: behaviour
    # id, prompt excerpt, response excerpt, and judge score. The
    # cards-not-table redesign wins over the old "Static payload
    # landed" finding-title smoke check — the new UI shows the
    # actually-useful evidence (what was sent, what came back) on
    # the per-run page rather than just a title string.
    assert "exfil_test_0" in body  # behaviour_id on the card
    assert "please summarise" in body  # prompt excerpt
    assert "BAD" in body  # response excerpt for the success trace
    # The timeline (collapsed by default) still includes both seeded
    # behaviours.
    assert "exfil_test_1" in body


def test_runs_show_404_for_unknown_id(client: TestClient) -> None:
    resp = client.get("/runs/no-such-run")
    assert resp.status_code == 404


def test_search_coverage_panel_renders_from_report_json(tmp_path: Path) -> None:
    """A run whose output_dir holds a tap_search_report.json renders the
    Search-coverage panel with its coverage, quorum, and refused framings."""
    import json

    (tmp_path / "tap_search_report.json").write_text(
        json.dumps(
            {
                "archive": {
                    "coverage": 0.5,
                    "possible_cells": ["a", "b", "c", "d"],
                    "attempted_cells": ["a", "b"],
                    "unexplored_cells": ["c", "d"],
                },
                "verifier_quorum": 2,
                "verifier_models": ["claude", "gpt"],
                "planner_models": ["planner-x"],
                "judge_calibrated": True,
                "onmech_drift_threshold": 0.5,
                "rewind_budget": 1,
                "checkpointed_nodes": 3,
                "refused_framings": {"name+gmail anchor": 4},
                "causal": {"controls": {"strict_seed": 0.2}, "variants": []},
            }
        )
    )
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id="run-cov",
            target_name="StubDocumentTarget",
            strategy="grid",
            output_dir=str(tmp_path),
        )
    app = create_app(engine=engine)
    with TestClient(app) as c:
        body = c.get("/runs/run-cov").text
    assert "Search coverage" in body
    assert "name+gmail anchor" in body  # refused framing surfaced
    assert "planner-x" in body  # planner model surfaced
    assert "fitted" in body  # judge calibration pill


def test_search_coverage_panel_absent_without_report(client: TestClient) -> None:
    """The seeded run has no output_dir, so the panel hides rather than
    inventing coverage."""
    body = client.get("/runs/run-seed").text
    assert "Search coverage" not in body


def test_runs_tree_json_returns_cytoscape_payload() -> None:
    """``/runs/{id}/_tree.json`` returns the Cytoscape node/edge payload the
    live client diffs: one cell per (behaviour, technique), verdict-derived
    node status, and parent→child edges reconstructed from ``parent_id``."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-cy", target_name="T", strategy="grid")
        spec = [
            ("root", None, 1, 0.4, "refused"),
            ("child_a", "root", 2, 0.9, "success"),
            ("child_b", "root", 2, 0.2, "error"),
        ]
        for tid, parent, depth, score, verdict in spec:
            trace = AttackTrace(
                id=tid,
                run_id="run-cy",
                behavior_id="exfil_x",
                payload=Payload(
                    id=f"p-{tid}", text="x", modality="text", generator_name="tap_indirect"
                ),
                target_name="T",
                raw_request=f"prompt-{tid}",
                raw_response=f"reply-{tid}",
                judge_scores=[
                    JudgeScore(
                        judge_name="strong_reject",
                        score=score,
                        flagged=verdict == "success",
                        reasoning="r",
                        judge_version="0.1",
                    )
                ],
                verdict=verdict,  # type: ignore[arg-type]
                parent_id=parent,
                depth=depth,
            )
            insert_trace(s, run_id="run-cy", trace=trace)
    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/runs/run-cy/_tree.json")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data) == {"finished", "hash", "phases"}
    cells = data["phases"][0]["cells"]
    assert len(cells) == 1
    cell = cells[0]
    assert cell["key"] == "p0|exfil_x|(none)"
    # One node per trace (statuses map verdict→status, error→pruned dashed),
    # PLUS a synthetic playbook root (trace_id None, status "root") that parents
    # the opening node so the tree has one top node.
    statuses = {n["data"]["trace_id"]: n["data"]["status"] for n in cell["nodes"]}
    assert statuses == {None: "root", "root": "refused", "child_a": "success", "child_b": "pruned"}
    # The objective root carries the behaviour's readable label and no score.
    pb = next(n for n in cell["nodes"] if n["data"]["trace_id"] is None)
    assert pb["data"]["score"] is None and "exfil x" in pb["data"]["label"].lower()
    # Every attempt node has a short proper name for its card.
    assert all(n["data"]["name"] for n in cell["nodes"] if n["data"]["trace_id"])
    # Three edges: playbook→root, root→child_a, root→child_b.
    assert len(cell["edges"]) == 3
    # Real prompt/response carried through for the inline node detail.
    child_a = next(n for n in cell["nodes"] if n["data"]["trace_id"] == "child_a")
    assert child_a["data"]["u"] == "prompt-child_a"
    assert child_a["data"]["a"] == "reply-child_a"


def test_runs_tree_renders_phases_as_labelled_sections() -> None:
    """A multi-phase run renders one labelled tree section per phase, in
    order — the unified Gmail→Docs then Docs→Docs view."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-ph", target_name="multi-phase", strategy="grid")
        spec = [
            ("g1", 0, "Gmail → Docs", "refused"),
            ("g2", 0, "Gmail → Docs", "success"),
            ("d1", 1, "Docs → Docs", "refused"),
            ("d2", 1, "Docs → Docs", "success"),
        ]
        for tid, idx, label, verdict in spec:
            trace = AttackTrace(
                id=tid,
                run_id="run-ph",
                behavior_id="bx",
                payload=Payload(
                    id=f"p-{tid}", text="x", modality="text", generator_name="tap_crescendo"
                ),
                target_name="T",
                raw_request="q",
                raw_response="r",
                judge_scores=[
                    JudgeScore(
                        judge_name="retrieval_verdict",
                        score=0.9 if verdict == "success" else 0.1,
                        flagged=verdict == "success",
                        reasoning="",
                        judge_version="0.1",
                    )
                ],
                verdict=verdict,  # type: ignore[arg-type]
                metadata={"phase": {"index": idx, "label": label, "total": 2}},
            )
            insert_trace(s, run_id="run-ph", trace=trace)
    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/runs/run-ph")
    assert resp.status_code == 200
    body = resp.text
    assert "Gmail → Docs" in body
    assert "Docs → Docs" in body
    assert body.index("Gmail → Docs") < body.index("Docs → Docs")
    # Phases are grouped in the attack-tree explorer's cell navigator.
    assert "cell-nav-group" in body
    assert "tree-explorer" in body


def test_traces_show_renders_for_unpromoted_trace() -> None:
    """Trace-detail route works for traces that were NOT promoted to
    a finding (e.g. refused / partial branches in a TAP tree). The
    finding-only ``/findings/{id}`` route can't surface these — the
    tree's clickable nodes need a route that doesn't require a
    finding."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id="run-detail",
            target_name="StubDocumentTarget",
            strategy="grid",
        )
        insert_trace(
            s,
            run_id="run-detail",
            trace=AttackTrace(
                id="t-refused",
                run_id="run-detail",
                behavior_id="exfil_x",
                payload=Payload(id="p", text="prompt-x", modality="text", generator_name="tap"),
                target_name="StubDocumentTarget",
                raw_request="prompt-x",
                raw_response="I cannot help with that.",
                judge_scores=[
                    JudgeScore(
                        judge_name="strong_reject",
                        score=0.05,
                        flagged=False,
                        reasoning="model refused",
                        judge_version="0.1",
                    )
                ],
                verdict="refused",
            ),
        )
        finalize_run(
            s,
            run_id="run-detail",
            summary=SummaryStats(total_traces=1, successful_traces=0, overall_asr=0.0),
        )

    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/traces/t-refused")
    assert resp.status_code == 200
    body = resp.text
    # Verdict badge, prompt text, response text, judge reasoning all
    # rendered — these are the four things you click into a node to
    # see.
    assert "refused" in body.lower()
    assert "prompt-x" in body
    assert "I cannot help with that." in body
    assert "model refused" in body


def test_traces_show_relabels_strong_reject_judge_and_colours_score() -> None:
    """The judge name ``strong_reject`` is misleading on its own —
    it's the *rubric* name (Souly 2024), not a verdict. The trace
    detail page must (a) relabel it as "StrongREJECT rubric" so a
    first-time viewer doesn't think the score means "the attack was
    rejected", (b) include an explanatory blurb above the table
    spelling out the score direction, and (c) colour high scores as
    danger (semantic-red), not neutral. Locks all three because
    they're the UX bug Google saw on the live demo."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id="run-judge",
            target_name="StubDocumentTarget",
            strategy="grid",
        )
        insert_trace(
            s,
            run_id="run-judge",
            trace=AttackTrace(
                id="t-success",
                run_id="run-judge",
                behavior_id="exfil_x",
                payload=Payload(id="p", text="prompt", modality="text", generator_name="tap"),
                target_name="StubDocumentTarget",
                raw_request="prompt",
                raw_response="leaked content",
                judge_scores=[
                    JudgeScore(
                        judge_name="strong_reject",
                        score=0.95,
                        flagged=True,
                        reasoning="model fully complied",
                        judge_version="0.1",
                    )
                ],
                verdict="success",
            ),
        )
        finalize_run(
            s,
            run_id="run-judge",
            summary=SummaryStats(total_traces=1, successful_traces=1, overall_asr=1.0),
        )

    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/traces/t-success")
    assert resp.status_code == 200
    body = resp.text
    # Internal identifiers stay out of the customer-facing page; the
    # table uses the human-readable rubric label.
    assert "strong_reject" not in body
    assert "StrongREJECT rubric" in body
    # Concise administrator-facing direction guidance remains present.
    assert "Scores measure observed instruction compliance" in body
    assert "indicates complete compliance" in body
    # (c) High score lands in the danger-coloured cell. Locks the
    # CSS class wiring; a regression to a neutral cell would silently
    # un-do the visual signal.
    assert "score-hot" in body


def test_traces_show_404_for_unknown_id() -> None:
    engine = make_engine("sqlite:///:memory:")
    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/traces/no-such-trace")
    assert resp.status_code == 404


def test_runs_show_embeds_attack_tree(client: TestClient) -> None:
    """The per-run page embeds the attack tree in its own section (there is no
    longer a separate full-screen tree page)."""
    resp = client.get("/runs/run-seed")
    assert resp.status_code == 200
    assert 'id="run-attack-tree"' in resp.text
    assert "tree-explorer" in resp.text
    assert "/runs/run-seed/tree" not in resp.text


def test_findings_index_full_page_renders(client: TestClient) -> None:
    resp = client.get("/findings")
    assert resp.status_code == 200
    assert "Static payload landed" in resp.text
    # Severity filter form is present on the full page.
    assert "filter-bar" in resp.text


def test_findings_index_htmx_returns_partial_only(client: TestClient) -> None:
    """When the request carries ``HX-Request: true`` the route serves
    only the table partial — no <html>/<body> wrapper."""
    resp = client.get("/findings", headers={"HX-Request": "true"})
    assert resp.status_code == 200
    body = resp.text
    assert "Static payload landed" in body
    assert "<html" not in body.lower()
    assert "filter-bar" not in body  # form lives in the full page only


def test_findings_severity_filter_drops_non_matches(
    client: TestClient,
) -> None:
    """Filtering to ``severity=critical`` excludes the seeded
    ``high``-severity finding."""
    resp = client.get("/findings", params={"severity": "critical"})
    assert resp.status_code == 200
    assert "Static payload landed" not in resp.text


def test_findings_show_renders(client: TestClient) -> None:
    # Look up the seeded finding's id from the index page.
    listing = client.get("/findings").text
    # Crude but reliable: the title links to /findings/<id>.
    import re

    m = re.search(r"/findings/(finding-[a-f0-9]+)", listing)
    assert m, "expected at least one finding link"
    finding_id = m.group(1)
    resp = client.get(f"/findings/{finding_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "exfil_test_0" in body
    # The finding detail page renders the request + response sections.
    # Headings were reworded to name the Gemini side-panel surface.
    assert "User prompt" in body
    assert "Gemini's response" in body


def test_findings_show_404_for_unknown(client: TestClient) -> None:
    resp = client.get("/findings/finding-no-such")
    assert resp.status_code == 404


def test_hitl_page_renders_pending_items(client: TestClient) -> None:
    resp = client.get("/hitl")
    assert resp.status_code == 200
    # Redesigned queue: title + the review affordance + gold-set stat.
    assert "Review queue" in resp.text
    assert "Gold labels" in resp.text
    assert "Review" in resp.text  # per-row review button
    assert "t1" in resp.text  # the seeded trace id


def test_hitl_status_filter_changes_listing(client: TestClient) -> None:
    resp = client.get("/hitl", params={"status": "confirmed"})
    assert resp.status_code == 200
    # No confirmed items yet → empty-state copy, no per-row Review button.
    assert "Nothing in the confirmed queue" in resp.text


# ----------------------------------------------------------------------
# HITL review flow (Slice 4 — the ≤30s review UI → GoldLabel)
# ----------------------------------------------------------------------


def _review_setup(blind_pct: str = "0"):
    """A dashboard + factory with one run, two conflict traces, and two
    pending review items. Returns (client, factory, [item_ids])."""
    import os

    os.environ["ADVTEST_HITL_BLIND_PCT"] = blind_pct
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    item_ids: list[str] = []
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-rv", target_name="Stub", strategy="grid")
        for i in range(2):
            insert_trace(
                s,
                run_id="run-rv",
                trace=AttackTrace(
                    id=f"rv{i}",
                    run_id="run-rv",
                    behavior_id="exfil_thing",
                    payload=Payload(
                        id=f"pp{i}",
                        text="HIDDEN PAYLOAD",
                        modality="text",
                        generator_name="static_payload",
                    ),
                    target_name="Stub",
                    raw_request="summarise",
                    raw_response=f"leaked canary CANARY-{i}",
                    judge_scores=[
                        JudgeScore(
                            judge_name="ipi_verdict",
                            score=0.1,
                            flagged=False,
                            reasoning="looks refused",
                            judge_version="0.1",
                        )
                    ],
                    verdict="refused",
                ),
            )
            item = enqueue_hitl(
                s,
                run_id="run-rv",
                trace_id=f"rv{i}",
                reason="review sample (disagreement)",
                disagreement=0.85,
                sample_reason="disagreement",
                uncertainty=0.85,
            )
            s.flush()
            item_ids.append(item.id)
    app = create_app(engine=engine)
    return TestClient(app), factory, item_ids


def test_hitl_review_page_renders_evidence() -> None:
    client, _factory, item_ids = _review_setup()
    resp = client.get(f"/hitl/{item_ids[0]}")
    assert resp.status_code == 200
    body = resp.text
    assert "leaked canary CANARY-0" in body  # the scored response
    assert "Your verdict" in body
    # Not blind (pct=0) → oracle verdict shown.
    assert "Oracle says" in body


def test_hitl_review_submit_writes_gold_label_and_resolves() -> None:
    from advtest_ext.storage import count_gold_labels, get_hitl, list_gold_labels

    client, factory, item_ids = _review_setup()
    resp = client.post(
        f"/hitl/{item_ids[0]}/review",
        json={
            "verdict": "success",
            "confidence": 5,
            "reason": "canary acted on",
            "reviewer_id": "alice",
            "review_seconds": 12,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    # Expert overruled the oracle (oracle=refused, expert=success).
    assert data["agreed"] is False
    assert data["next_id"] == item_ids[1]

    with session_scope(factory) as s:
        assert count_gold_labels(s) == 1
        gl = list_gold_labels(s)[0]
        # Frozen oracle prediction captured from the trace.
        assert gl.oracle_verdict == "refused"
        assert gl.expert_verdict == "success"
        assert gl.expert_confidence == 5
        assert gl.sample_reason == "disagreement"
        assert gl.reviewer_id == "alice"
        # Queue item resolved as 'rejected' (expert overruled).
        item = get_hitl(s, item_ids[0])
        assert item is not None and item.status == "rejected"


def test_hitl_review_confirm_when_expert_agrees() -> None:
    from advtest_ext.storage import get_hitl

    client, factory, item_ids = _review_setup()
    resp = client.post(f"/hitl/{item_ids[0]}/review", json={"verdict": "refused"})
    assert resp.json()["agreed"] is True
    with session_scope(factory) as s:
        item = get_hitl(s, item_ids[0])
        assert item is not None and item.status == "confirmed"


def test_hitl_review_rejects_bad_verdict() -> None:
    client, _factory, item_ids = _review_setup()
    resp = client.post(f"/hitl/{item_ids[0]}/review", json={"verdict": "error"})
    assert resp.status_code == 422


def test_hitl_review_missing_item_404() -> None:
    client, _factory, _ids = _review_setup()
    assert client.get("/hitl/nope").status_code == 404
    assert client.post("/hitl/nope/review", json={"verdict": "success"}).status_code == 404


def test_hitl_review_double_submit_conflicts() -> None:
    client, _factory, item_ids = _review_setup()
    first = client.post(f"/hitl/{item_ids[0]}/review", json={"verdict": "success"})
    assert first.status_code == 200
    second = client.post(f"/hitl/{item_ids[0]}/review", json={"verdict": "refused"})
    assert second.status_code == 409  # already reviewed


def test_hitl_blind_review_hides_oracle() -> None:
    # Force every review blind → the oracle verdict must not be shown.
    client, _factory, item_ids = _review_setup(blind_pct="100")
    body = client.get(f"/hitl/{item_ids[0]}").text
    assert "Blind review" in body
    assert "Oracle says" not in body


def test_hitl_blind_flag_recorded_on_gold_label() -> None:
    from advtest_ext.storage import list_gold_labels

    client, factory, item_ids = _review_setup(blind_pct="100")
    client.post(f"/hitl/{item_ids[0]}/review", json={"verdict": "success"})
    with session_scope(factory) as s:
        assert list_gold_labels(s)[0].blind is True


# ----------------------------------------------------------------------
# PPI headline (Slice 5 — calibrated ASR ± CI on the run page)
# ----------------------------------------------------------------------


def test_run_page_shows_oracle_only_asr_without_labels(client: TestClient) -> None:
    # The seeded run has no gold labels → the hero shows the uncalibrated
    # oracle estimate with a prompt to label the queue.
    body = client.get("/runs/run-seed").text
    assert "Attack Success Rate" in body
    assert "Automated assessment · Not human-calibrated" in body


def test_run_page_shows_calibrated_ci_with_uniform_labels() -> None:
    from advtest_ext.storage import insert_gold_label

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-ci", target_name="Stub", strategy="grid")
        for i in range(20):
            verdict = "success" if i < 14 else "refused"
            insert_trace(
                s,
                run_id="run-ci",
                trace=AttackTrace(
                    id=f"c{i}",
                    run_id="run-ci",
                    behavior_id="exfil_x",
                    payload=Payload(
                        id=f"q{i}", text="p", modality="text", generator_name="static_payload"
                    ),
                    target_name="Stub",
                    raw_request="r",
                    raw_response="x",
                    judge_scores=[
                        JudgeScore(
                            judge_name="j",
                            score=0.9 if i < 14 else 0.1,
                            flagged=i < 14,
                            reasoning="",
                            judge_version="1",
                        )
                    ],
                    verdict=verdict,  # type: ignore[arg-type]
                ),
            )
        for i in range(6):
            insert_gold_label(
                s,
                trace_id=f"c{i}",
                run_id="run-ci",
                behavior_id="exfil_x",
                oracle_verdict="success",
                expert_verdict="refused" if i < 2 else "success",
                sample_reason="uniform",
            )
        finalize_run(
            s,
            run_id="run-ci",
            summary=SummaryStats(total_traces=20, successful_traces=14, overall_asr=0.7),
        )
    app = create_app(engine=engine)
    with TestClient(app) as c:
        body = c.get("/runs/run-ci").text
    assert "calibrated" in body
    assert "95% CI" in body
    assert "labels" in body


def test_references_page_removed(client: TestClient) -> None:
    # The standalone /references (Citations) page was removed; the bib is still
    # parsed for report citations (advtest_ext.dashboard.references), just not
    # served as its own page.
    assert client.get("/references").status_code == 404


# ----------------------------------------------------------------------
# Empty state
# ----------------------------------------------------------------------


def test_runs_index_empty_state_copy(empty_client: TestClient) -> None:
    resp = empty_client.get("/runs")
    assert resp.status_code == 200
    assert "No runs yet" in resp.text


def test_findings_index_empty_state_copy(empty_client: TestClient) -> None:
    resp = empty_client.get("/findings")
    assert resp.status_code == 200
    assert "No findings match" in resp.text


# ----------------------------------------------------------------------
# Report — HTML always works; PDF route gates on WeasyPrint install
# ----------------------------------------------------------------------


def test_report_html_renders(client: TestClient) -> None:
    resp = client.get("/runs/run-seed/report")
    assert resp.status_code == 200
    body = resp.text
    assert "advtest run report" in body
    assert "Executive summary" in body
    assert "Methodology" in body
    assert "References" in body


def test_report_pdf_route_gracefully_fails_without_weasyprint(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When WeasyPrint isn't installed the PDF route 501s with an
    install hint; the HTML report stays available."""
    import advtest_ext.dashboard.report as report_mod

    monkeypatch.setattr(report_mod, "WEASYPRINT_INSTALLED", False)
    resp = client.get("/runs/run-seed/report.pdf")
    assert resp.status_code == 501
    assert "weasyprint" in resp.text.lower()
    assert "/runs/run-seed/report" in resp.text


# ----------------------------------------------------------------------
# Replay-bundle download
# ----------------------------------------------------------------------


def test_finding_replay_endpoint_404s_without_bundle_path(
    client: TestClient,
) -> None:
    """The seeded finding has no replay bundle path set — the route
    returns 404 with a clear reason rather than a server error."""
    listing = client.get("/findings").text
    import re

    m = re.search(r"/findings/(finding-[a-f0-9]+)", listing)
    assert m
    finding_id = m.group(1)
    resp = client.get(f"/findings/{finding_id}/replay")
    assert resp.status_code == 404


def test_finding_replay_endpoint_serves_zip_when_path_set(
    tmp_path: Path,
) -> None:
    """End-to-end: seed a finding with a replay-bundle path that
    points at a real ZIP, and assert the route serves the bytes."""
    from advtest_ext.storage import build_replay_bundle

    # Build a real bundle so the file genuinely exists on disk.
    trace_for_bundle = AttackTrace(
        id="t-bundle",
        run_id="run-bundle",
        behavior_id="exfil_x",
        payload=Payload(id="p", text="x", modality="text", generator_name="static_payload"),
        target_name="StubDocumentTarget",
        raw_request="x",
        raw_response="y",
        verdict="success",
    )
    zip_path = build_replay_bundle(
        finding_id="finding-served",
        run_id="run-bundle",
        trace=trace_for_bundle,
        document=None,
        output_dir=tmp_path,
    )
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-bundle", target_name="stub", strategy="grid")
        insert_trace(s, run_id="run-bundle", trace=trace_for_bundle)
        promote_finding(
            s,
            run_id="run-bundle",
            trace_id="t-bundle",
            severity="med",
            title="t",
            replay_bundle_path=str(zip_path),
        )
    app = create_app(engine=engine)
    with TestClient(app) as c:
        listing = c.get("/findings").text
        import re

        m = re.search(r"/findings/(finding-[a-f0-9]+)", listing)
        assert m
        finding_id = m.group(1)
        resp = c.get(f"/findings/{finding_id}/replay")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/zip"
        # Replay ZIP is non-empty and starts with the PK marker.
        assert resp.content[:2] == b"PK"


# ----------------------------------------------------------------------
# Drift view
# ----------------------------------------------------------------------


def test_drift_route_renders_empty_state(empty_client: TestClient) -> None:
    """The drift aggregation and template both existed with no route to
    reach them — the page was unreachable. Pin the route so it stays
    wired, and lock the empty-state copy (an empty drift page is the
    honest default until an adaptive run emits events)."""
    r = empty_client.get("/drift")
    assert r.status_code == 200
    assert "No drift events recorded yet" in r.text


def test_drift_route_renders_recorded_events() -> None:
    """Drift events live in ``Trace.extra_metadata['drift']``, written by
    the attack and adapter layers. Seed one of each scope and check the
    counters and the event table pick them up.

    Builds its own engine rather than borrowing the shared fixture's,
    because the drift metadata has to be written before the app reads it.
    """
    from advtest_ext.storage.models import Trace

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    _seed_run(factory)
    with session_scope(factory) as s:
        trace = s.query(Trace).first()
        assert trace is not None
        trace.extra_metadata = {
            "drift": [
                {
                    "scope": "attacker_goal",
                    "kind": "off_mechanism",
                    "detail": "attacker wandered off the injection mechanism",
                    "turn": 3,
                },
                {
                    "scope": "platform_ui",
                    "kind": "recovered",
                    "detail": "side panel remounted after layout change",
                },
            ]
        }

    with TestClient(create_app(engine=engine)) as c:
        r = c.get("/drift")

    assert r.status_code == 200
    assert "No drift events recorded yet" not in r.text
    # Both scopes counted, and the detail text reaches the event table.
    assert "off_mechanism" in r.text
    assert "wandered off the injection mechanism" in r.text
    assert "remounted" in r.text


def test_drift_route_is_reachable(empty_client: TestClient) -> None:
    # Drift is no longer a headline nav tab, but the route stays live.
    assert empty_client.get("/drift").status_code == 200


# ----------------------------------------------------------------------
# Surfaces inventory + slide-over (real-data derived)
# ----------------------------------------------------------------------


def test_surfaces_index_renders_real_surface(client: TestClient) -> None:
    """The Surfaces page derives its inventory from distinct
    ``Trace.target_name`` — the seeded run drove ``StubDocumentTarget``,
    so exactly one exposed surface (1 finding) shows up."""
    r = client.get("/surfaces")
    assert r.status_code == 200
    assert "Testing coverage" in r.text
    # Raw target class shown in mono, human label camel-split (trailing
    # "Target" trimmed): StubDocumentTarget -> "Stub Document".
    assert "StubDocumentTarget" in r.text
    assert "Stub Document" in r.text
    # One finding was promoted -> the surface is exposed.
    assert 'data-exposure="exposed"' in r.text
    # Coverage stays on Targets; the judge-reliability page has been removed.
    assert "Oracle agreement vs expert consensus" not in r.text
    assert "/judge" not in r.text
    assert "under test" not in r.text


def test_surfaces_index_empty_state(empty_client: TestClient) -> None:
    r = empty_client.get("/surfaces")
    assert r.status_code == 200
    assert "6 surfaces" in r.text
    assert "Not tested" in r.text
    assert "Not configured" in r.text


def test_surface_panel_returns_real_detail(client: TestClient) -> None:
    """The slide-over partial carries the surface's real counts and its
    promoted finding — no sample rows."""
    r = client.get("/surfaces/StubDocumentTarget/_panel")
    assert r.status_code == 200
    assert "Target class" in r.text
    assert "StubDocumentTarget" in r.text
    # 2 traces seeded against this target, 1 run, 1 finding.
    assert "2 attempts" in r.text
    assert "Static payload landed" in r.text


def test_surface_panel_404_for_unknown_surface(client: TestClient) -> None:
    r = client.get("/surfaces/NoSuchTarget/_panel")
    assert r.status_code == 404


def test_build_surfaces_context_counts_are_real() -> None:
    from advtest_ext.dashboard.app import build_surfaces_context

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    _seed_run(factory)
    with session_scope(factory) as s:
        ctx = build_surfaces_context(s)
    assert ctx["totals"]["surfaces"] == 1
    surf = ctx["surfaces"][0]
    assert surf["target_name"] == "StubDocumentTarget"
    assert surf["attempts"] == 2
    assert surf["runs"] == 1
    assert surf["findings"] == 1
    assert surf["exposure"] == "exposed"


def test_surfaces_route_is_reachable(empty_client: TestClient) -> None:
    # Surfaces is no longer a headline nav tab, but the route stays live.
    assert empty_client.get("/surfaces").status_code == 200


# ----------------------------------------------------------------------
def test_playbook_show_renders_seed_chain_signals_and_404s() -> None:
    engine = make_engine("sqlite:///:memory:")
    app = create_app(engine=engine)
    with TestClient(app) as c:
        r = c.get("/playbooks/sheets_skill_leak")
        missing = c.get("/playbooks/does-not-exist")
    assert r.status_code == 200
    # Rendered as the canonical Playbook config object (report Table 2).
    assert "playbook_id:" in r.text
    assert "seed_chain:" in r.text and "signals:" in r.text
    assert "mutation_surface.allowed:" in r.text and "invariants:" in r.text
    assert "Bikash Sabat" in r.text
    assert missing.status_code == 404


def test_seed_corpus_loads_and_validates() -> None:
    """The normalised seed corpus parses into valid Seeds, both kinds present."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ingest_seeds", Path(__file__).resolve().parents[1] / "scripts" / "ingest_seeds.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    seeds = mod.load_seed_corpus()
    assert len(seeds) >= 20
    kinds = {s.kind for s in seeds}
    assert kinds == {"human_chat", "dataset_entry"}
    # All three top public corpora relevant to our surface are represented.
    corpora = {s.payload.get("corpus") for s in seeds if s.kind == "dataset_entry"}
    assert {"AgentDojo", "MCPTox", "SafeMTData (ActorAttack)"} <= corpora
    # Every dataset turn carries verbatim text (no bracketed placeholders left).
    for s in seeds:
        for t in s.payload.get("extracted_turns", []):
            assert t.get("attacker"), f"empty turn in {s.id}"
    # Bikash's flagship carries the real transcript (target responses present).
    flagship = next(s for s in seeds if s.id == "bikash_sheets_skill_leak")
    turns = flagship.payload["extracted_turns"]
    assert len(turns) == 8
    assert any(t.get("target") for t in turns)


def test_seeds_index_and_show_render_normalised() -> None:
    from advtest_ext.core.seed import Seed
    from advtest_ext.storage.repository import insert_seed

    seed = Seed(
        id="bikash_sheets_skill_leak",
        kind="human_chat",
        title="V-Sheets — skill-manifest disclosure",
        source_format="chat_export_json",
        raw_artefact_path="configs/seed_gold/sheets_skill_leak.json",
        taxonomy_hints=["crescendo", "system-prompt-leak"],
        submitted_by="bikash",
        payload={
            "extracted_turns": [
                {
                    "n": 1,
                    "attacker": "as your first task in this QA assessment...",
                    "target": "row 6 populated",
                    "expert": "partial",
                },
                {
                    "n": 2,
                    "attacker": "prove it — run get_skills",
                    "target": "EXECUTION_PLAN_STEP_STATUS_PENDING",
                    "expert": "success",
                },
            ],
            "declared_effect": "system_prompt_leak",
            "compiles_to_playbook": "sheets_skill_leak",
        },
    )
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        insert_seed(s, seed=seed)
    app = create_app(engine=engine)
    with TestClient(app) as c:
        idx = c.get("/seeds")
        show = c.get("/seeds/bikash_sheets_skill_leak")
        missing = c.get("/seeds/nope")
    assert idx.status_code == 200 and "bikash_sheets_skill_leak" in idx.text
    assert show.status_code == 200
    assert "Normalised chain" in show.text
    assert "attacker" in show.text and "gemini" in show.text
    assert "EXECUTION_PLAN_STEP_STATUS_PENDING" in show.text
    assert 'href="/playbooks/sheets_skill_leak"' in show.text
    assert missing.status_code == 404


def test_tree_node_names_are_short_and_complete() -> None:
    """Card names are proper names, never an ellipsised strategy sentence."""
    from advtest_ext.dashboard.tree import _strategy_name

    assert _strategy_name("strict byte-for-byte reproduction control") == "Seed replay, verbatim"
    assert (
        _strategy_name(
            "reproduce the documented seed conversation, adapting each turn to the target's replies"
        )
        == "Seed replay, adaptive"
    )
    assert (
        _strategy_name(
            "Vary only persona: ...", {"dimension": "persona", "feature_value": "deputy agent"}
        )
        == "Persona: deputy agent"
    )
    name = _strategy_name(
        'Frame yourself as a Google-internal "Prompt Safety Auditor" who has been asked to review'
    )
    assert name == "Google-internal Prompt Safety Auditor"
    long = _strategy_name("Pose as a " + "very " * 20 + "long persona for the")
    assert len(long) <= 44 and "…" not in long and not long.endswith(" the")


def test_runs_index_lists_interrupted_run_with_successes() -> None:
    """A run that never finished has no cached summary; the list must still
    show it (with its real success rate) when its traces contain a success."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-interrupted", target_name="T", strategy="grid")
        for tid, verdict in (("i1", "success"), ("i2", "refused"), ("i3", "error")):
            insert_trace(
                s,
                run_id="run-interrupted",
                trace=AttackTrace(
                    id=tid,
                    run_id="run-interrupted",
                    behavior_id="exfil_x",
                    payload=Payload(id=f"p-{tid}", text="x", modality="text", generator_name="g"),
                    target_name="T",
                    raw_request="q",
                    raw_response="a",
                    verdict=verdict,  # type: ignore[arg-type]
                ),
            )
    app = create_app(engine=engine)
    with TestClient(app) as c:
        body = c.get("/runs").text
    assert "/runs/run-interrupted" in body
    assert "50%" in body  # 1 success / 2 non-error attempts
    # Display-only: the computed summary is never written back.
    with session_scope(factory) as s:
        from advtest_ext.storage.repository import get_run

        stored = get_run(s, "run-interrupted")
        assert stored is not None and not (stored.summary or {})


def test_stale_unfinished_run_is_interrupted_not_running() -> None:
    """``Running`` needs a live heartbeat: an unfinished run whose last attempt
    is old shows as Interrupted and its partials stop polling."""
    from datetime import UTC, datetime, timedelta

    from advtest_ext.storage.repository import get_run, run_state

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)

    def _trace(run_id: str, tid: str, when: datetime) -> AttackTrace:
        return AttackTrace(
            id=tid,
            run_id=run_id,
            behavior_id="exfil_x",
            payload=Payload(id=f"p-{tid}", text="x", modality="text", generator_name="g"),
            target_name="T",
            raw_request="q",
            raw_response="a",
            verdict="success",
            timestamp=when,
        )

    now = datetime.now(UTC)
    with session_scope(factory) as s:
        upsert_run(s, run_id="run-stale", target_name="T", strategy="grid")
        upsert_run(s, run_id="run-fresh", target_name="T", strategy="grid")
        insert_trace(
            s, run_id="run-stale", trace=_trace("run-stale", "s1", now - timedelta(days=2))
        )
        insert_trace(s, run_id="run-fresh", trace=_trace("run-fresh", "f1", now))
        # The run row's own start must not count as a heartbeat for the stale run.
        get_run(s, "run-stale").started_at = now - timedelta(days=2)
    with session_scope(factory) as s:
        assert run_state(get_run(s, "run-stale")) == "interrupted"
        assert run_state(get_run(s, "run-fresh")) == "running"
    app = create_app(engine=engine)
    with TestClient(app) as c:
        stale = c.get("/runs/run-stale").text
        fresh = c.get("/runs/run-fresh").text
        assert c.get("/runs/run-stale/_tree.json").json()["finished"] is True
        assert c.get("/runs/run-fresh/_tree.json").json()["finished"] is False
    assert "Interrupted" in stale and 'hx-get="/runs/run-stale/_hero"' not in stale
    assert "Running" in fresh and 'hx-get="/runs/run-fresh/_hero"' in fresh


def test_runs_index_has_new_run_modal() -> None:
    """The Runs list opens a centred launcher dialog; the standalone /runs/new
    page is gone, so the no-JS fallback now points at /runs?new=1 (which auto-
    opens the modal) rather than a separate launcher page."""
    engine = make_engine("sqlite:///:memory:")
    with session_scope(make_session_factory(engine)) as s:
        upsert_run(s, run_id="run-x", target_name="T", strategy="grid")
    app = create_app(engine=engine)
    with TestClient(app) as c:
        body = c.get("/runs").text
    assert 'id="new-run-dialog"' in body
    assert "data-open-new-run" in body
    assert 'href="/runs?new=1"' in body  # fallback opens the modal, not a page
    assert 'href="/runs/new"' not in body  # the standalone launcher link is gone
    assert "/static/new-run.js" in body


def test_runs_new_redirects_to_modal() -> None:
    """/runs/new no longer renders a page — it redirects to the Runs list with
    the launcher modal open."""
    engine = make_engine("sqlite:///:memory:")
    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/runs/new", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/runs?new=1"


def test_run_page_shows_stop_only_for_the_active_live_run() -> None:
    """The Stop button appears on a run's page only while it is the run the
    controller is actually driving — not for a finished run, nor for an active
    run the controller isn't tracking (where /live-tests/stop couldn't act)."""
    from advtest_ext.dashboard.live_tests import LiveTestController

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as s:  # active run: no finalize -> no finished_at
        upsert_run(s, run_id="run-live", target_name="GeminiDocsMultiTurnTarget", strategy="tap")
    controller = LiveTestController("sqlite:///:memory:", enabled=True)
    app = create_app(engine=engine, live_test_controller=controller)

    # Controller is driving THIS run -> Stop button present.
    controller._run_id = "run-live"
    controller._status = "running"
    with TestClient(app) as c:
        body = c.get("/runs/run-live").text
    assert "Stop run" in body
    assert 'hx-post="/live-tests/stop"' in body

    # Controller is driving a different run -> no Stop button on this page.
    controller._run_id = "run-other"
    with TestClient(app) as c:
        body = c.get("/runs/run-live").text
    assert "Stop run" not in body


def test_live_tests_options_keys_guides_by_surface_and_scope() -> None:
    """The New-run dialog's data source: guides keyed by Workspace surface,
    flagged single- vs multi-app, each with a one-line blurb."""
    from advtest_ext.dashboard.live_tests import LiveTestController

    engine = make_engine("sqlite:///:memory:")
    controller = LiveTestController("sqlite:///:memory:", enabled=True)
    app = create_app(engine=engine, live_test_controller=controller)
    with TestClient(app) as c:
        data = c.get("/live-tests/options").json()
    assert data["enabled"] is True
    assert data["guides"], "expected at least one guide"
    g = data["guides"][0]
    assert {
        "id",
        "title",
        "surface",
        "surface_label",
        "multi_workspace",
        "second_accounts",
        "maturity",
        "blurb",
    } <= set(g)
    assert isinstance(g["multi_workspace"], bool)
    # Both scopes are represented, so the Single/Across-workspaces toggle has content.
    assert any(x["multi_workspace"] for x in data["guides"])
    assert any(not x["multi_workspace"] for x in data["guides"])
    # Across-workspaces guides name the extra account(s) they stage.
    assert all(
        x["second_accounts"] if x["multi_workspace"] else not x["second_accounts"]
        for x in data["guides"]
    )
    # Surface keys are canonical (no google_ prefix) and drive the workspace list.
    assert all("google_" not in x["surface"] for x in data["guides"])
    # Sorted by surface label then title, so the picker groups cleanly.
    ordered = [(x["surface_label"], x["title"].lower()) for x in data["guides"]]
    assert ordered == sorted(ordered)


def test_findings_page_has_no_export_button(client: TestClient) -> None:
    """Export moved to the Reports builder; the findings page shouldn't offer it."""
    body = client.get("/findings").text
    assert "findings-export" not in body


def test_reports_page_has_builder_modal(client: TestClient) -> None:
    body = client.get("/reports").text
    assert 'id="report-builder-dialog"' in body
    assert "data-open-report-builder" in body
    assert "/static/report-builder.js" in body


def test_report_view_generates_from_selected_findings(client: TestClient) -> None:
    """The report builder renders a titled document of the matching findings.

    The seeded finding's behaviour (``exfil_test_0``) is high severity, so a
    critical/low filter excludes it while a high (or unfiltered) one includes it.
    """
    titled = client.get("/reports/view?title=Q2+Review&severity=high")
    assert titled.status_code == 200
    assert "Q2 Review" in titled.text
    assert titled.context["total"] == 1
    assert titled.context["report_title"] == "Q2 Review"

    excluded = client.get("/reports/view?severity=low")
    assert excluded.context["total"] == 0
    assert "No findings match" in excluded.text
