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


# ----------------------------------------------------------------------
# Page-level smoke tests: every required URL returns 200
# ----------------------------------------------------------------------


def test_runs_index_renders(client: TestClient) -> None:
    resp = client.get("/runs")
    assert resp.status_code == 200
    assert "run-seed" in resp.text
    assert "Runs" in resp.text


def test_root_redirects_or_serves_runs_index(client: TestClient) -> None:
    """``/`` shares a handler with ``/runs`` — both must serve the
    same page."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "run-seed" in resp.text


def test_runs_show_renders_with_heatmap(client: TestClient) -> None:
    resp = client.get("/runs/run-seed")
    assert resp.status_code == 200
    body = resp.text
    # Heatmap section header.
    assert "Heatmap" in body
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


def test_runs_tree_renders_tap_shaped_chains() -> None:
    """End-to-end: seed a run with a TAP-shaped chain (root + two
    depth-1 children + one depth-2 grandchild), hit ``/runs/{id}/tree``,
    assert the response is 200 and that the parent-child links are
    actually reflected in the rendered HTML.

    The smoke test on its own (``status == 200``) would pass even if
    the helper grouped traces wrong; the assertions below check that
    each cell renders, that the grandchild's id appears nested under
    its parent's, and that the persisted depth + score fields make
    it through the template. Note: only ``parent_id`` and ``depth``
    are persisted from TAP metadata today — the rich
    ``metadata['tap']`` blob (branch index, attacker reasoning) is
    NOT on the storage ``Trace`` row, so this test deliberately
    doesn't seed it."""
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)

    def _seed_tap(factory: sessionmaker[Session]) -> None:
        with session_scope(factory) as s:
            upsert_run(
                s,
                run_id="run-tap",
                target_name="StubDocumentTarget",
                strategy="grid",
            )
            # Trace structure (one cell, behaviour=exfil_x, technique=None):
            #   root (depth=1, no parent)
            #     ├── child_a (depth=2, parent=root)         [score=0.9, success]
            #     │     └── grandchild (depth=3, parent=child_a)
            #     └── child_b (depth=2, parent=root)         [score=0.2, refused]
            spec = [
                ("root", None, 1, 0.4, "refused"),
                ("child_a", "root", 2, 0.9, "success"),
                ("child_b", "root", 2, 0.2, "refused"),
                ("grandchild", "child_a", 3, 0.95, "success"),
            ]
            for trace_id, parent, depth, score, verdict in spec:
                trace = AttackTrace(
                    id=trace_id,
                    run_id="run-tap",
                    behavior_id="exfil_x",
                    payload=Payload(
                        id=f"p-{trace_id}",
                        text=f"refined-prompt-{trace_id}",
                        modality="text",
                        generator_name="tap_indirect",
                    ),
                    target_name="StubDocumentTarget",
                    raw_request=f"refined-prompt-{trace_id}",
                    raw_response="response",
                    judge_scores=[
                        JudgeScore(
                            judge_name="strong_reject",
                            score=score,
                            flagged=verdict == "success",
                            reasoning="rubric",
                            judge_version="0.1",
                        )
                    ],
                    verdict=verdict,  # type: ignore[arg-type]
                    parent_id=parent,
                    depth=depth,
                )
                insert_trace(s, run_id="run-tap", trace=trace)
            finalize_run(
                s,
                run_id="run-tap",
                summary=SummaryStats(total_traces=4, successful_traces=2, overall_asr=0.5),
            )

    _seed_tap(factory)
    app = create_app(engine=engine)
    with TestClient(app) as c:
        resp = c.get("/runs/run-tap/tree")
    assert resp.status_code == 200
    body = resp.text
    # Cell header surfaced.
    assert "exfil_x" in body
    # 2 successes / 4 traces shows the cell summary worked.
    assert "2/4" in body
    # All four trace ids made it onto the page.
    for tid in ("root", "child_a", "child_b", "grandchild"):
        assert tid in body
    # ASCII tree rendering: box-drawing characters present.
    assert "├──" in body or "└──" in body
    # Each trace line is wrapped in an <a href="/traces/{id}"> so
    # users can click through to detail. Locks discoverability for
    # the trace detail route.
    for tid in ("root", "child_a", "child_b", "grandchild"):
        assert f'href="/traces/{tid}"' in body
    # Score from judge_scores[0] formatted onto the node.
    assert "0.95" in body or "0.90" in body
    # Structural assertion: grandchild's <li>-equivalent appears
    # under child_a (its parent), with the continuation column
    # ("│") between them, BEFORE child_b shows up.
    grandchild_pos = body.index("grandchild")
    child_a_pos = body.index("child_a")
    child_b_pos = body.index("child_b")
    assert child_a_pos < grandchild_pos < child_b_pos
    # No synthetic root node injected. The explanatory copy at the
    # top of the page legitimately uses the word "synthetic" to
    # describe the *data* (depth-1 branches' parent never persisted),
    # so we can't ban the word — instead, lock the structural fact
    # that the rendered lines correspond exactly to the four real
    # traces seeded above. Counting trace links is the cheapest way
    # to assert "no extra fake-root row was emitted".
    assert body.count('href="/traces/') == 4


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
        resp = c.get("/runs/run-ph/tree")
    assert resp.status_code == 200
    body = resp.text
    assert "Gmail → Docs" in body
    assert "Docs → Docs" in body
    assert body.index("Gmail → Docs") < body.index("Docs → Docs")
    assert "phase-head" in body


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
    # (a) Display label rewritten — the raw judge-name string ONLY
    # appears once (inside the explanatory blurb that references the
    # internal name in <code>); the table-cell label uses the human-
    # readable form. A regression to "{{ js.judge_name }}" in the
    # cell would push the count to 2+.
    assert body.count("strong_reject") == 1
    assert "StrongREJECT rubric" in body
    # (b) Direction-of-meaning blurb so 1.00 isn't read as "rejected".
    assert "compliance" in body.lower()
    assert "refused the attack" in body.lower()
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


def test_runs_tree_404_for_unknown_id(client: TestClient) -> None:
    resp = client.get("/runs/no-such-run/tree")
    assert resp.status_code == 404


def test_runs_show_links_to_tree(client: TestClient) -> None:
    """The per-run page must surface a link to the tree view —
    otherwise the route is undiscoverable from the UI."""
    resp = client.get("/runs/run-seed")
    assert resp.status_code == 200
    assert "/runs/run-seed/tree" in resp.text


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


def test_attacks_page_lists_registered_plugins(client: TestClient) -> None:
    resp = client.get("/attacks")
    assert resp.status_code == 200
    body = resp.text
    # No-arg constructible attacks from the registry.
    assert "static_payload" in body
    assert "indirect_injection" in body
    assert "pap" in body


def test_adapters_page_lists_registered_targets(client: TestClient) -> None:
    resp = client.get("/adapters")
    assert resp.status_code == 200
    assert "StubDocumentTarget" in resp.text


def test_hitl_page_renders_pending_items(client: TestClient) -> None:
    resp = client.get("/hitl")
    assert resp.status_code == 200
    assert "judge spread 0.6" in resp.text


def test_hitl_status_filter_changes_listing(client: TestClient) -> None:
    resp = client.get("/hitl", params={"status": "confirmed"})
    assert resp.status_code == 200
    # Pending item filtered out.
    assert "judge spread 0.6" not in resp.text


def test_references_page_renders(client: TestClient) -> None:
    resp = client.get("/references")
    assert resp.status_code == 200
    body = resp.text
    # The bib has well-known anchor entries we cite all over the codebase.
    assert "greshake2023ipi" in body
    assert "agentvigil2025" in body


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
