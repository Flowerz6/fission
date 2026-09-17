"""Review findings inside their run without losing run or selection context."""

import pytest
from fastapi.testclient import TestClient

from advtest_ext.core.models import AttackTrace, Payload
from advtest_ext.dashboard import create_app
from advtest_ext.storage import (
    insert_trace,
    make_engine,
    make_session_factory,
    promote_finding,
    session_scope,
    upsert_run,
)
from advtest_ext.storage.models import Finding, Run
from tests.test_dashboard import _seed_run


@pytest.fixture
def review_store():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    _seed_run(factory)
    with session_scope(factory) as session:
        upsert_run(session, run_id="other-run", target_name="GmailTarget", strategy="grid")
        for index in range(8):
            run_id = "other-run" if index == 7 else "run-seed"
            trace = AttackTrace(
                id=f"review-{index}",
                run_id=run_id,
                target_name="GoogleDocsTarget",
                behavior_id="leak_system_prompt",
                raw_request=f"Prompt {index}",
                raw_response=f"Response {index}",
                verdict="success",
                payload=Payload(
                    id=f"payload-{index}",
                    text="test",
                    modality="text",
                    generator_name="static_payload",
                ),
            )
            insert_trace(session, run_id=run_id, trace=trace)
            promote_finding(
                session,
                run_id=run_id,
                trace_id=trace.id,
                severity="high",
                title=f"Review finding {index}",
            )
    with TestClient(create_app(engine=engine)) as client:
        yield client, factory


def test_run_contains_all_findings_and_initial_evidence(review_store):
    client, _ = review_store
    response = client.get("/runs/run-seed")
    assert response.status_code == 200
    assert len(response.context["review_findings"]) == 8
    assert response.text.count("data-run-finding=") == 8
    assert "data-finding-id=" in response.text
    assert "View all" not in response.text
    assert "User prompt" in response.text and "Model response" in response.text
    assert 'data-finding-step="1"' in response.text


def test_selected_finding_survives_direct_link_and_live_refresh(review_store):
    client, factory = review_store
    with session_scope(factory) as session:
        finding = session.query(Finding).filter(Finding.trace_id == "review-3").one()
        finding_id = finding.id
        session.get(Run, "run-seed").finished_at = None
    for path in ["/runs/run-seed", "/runs/run-seed/_findings"]:
        response = client.get(path, params={"finding": finding_id})
        assert response.status_code == 200
        assert response.context["selected_finding"].id == finding_id
        assert f'data-selected-finding="{finding_id}"' in response.text
        assert "Response 3" in response.text


def test_evidence_endpoint_is_scoped_to_run(review_store):
    client, factory = review_store
    with factory() as session:
        finding_id = session.query(Finding).filter(Finding.trace_id == "review-3").one().id
    response = client.get(f"/runs/run-seed/findings/{finding_id}/_evidence")
    assert response.status_code == 200
    assert "Response 3" in response.text
    assert "from_run=run-seed" in response.text
    assert "<html" not in response.text
    assert client.get(f"/runs/other-run/findings/{finding_id}/_evidence").status_code == 404
    assert client.get("/runs/run-seed/findings/missing/_evidence").status_code == 404
    detail = client.get(f"/findings/{finding_id}?from_run=run-seed")
    assert "← Back to run" in detail.text
    assert f"/runs/run-seed?finding={finding_id}#run-findings" in detail.text
    assert "← Back to findings" in client.get(f"/findings/{finding_id}").text


def test_empty_run_and_list_links(review_store):
    client, _ = review_store
    assert client.get("/runs/missing").status_code == 404
    listing = client.get("/runs?service=all").text
    assert "onclick='return openRunPanel" not in listing


def test_finding_embeds_its_full_conversation_and_counts_groups():
    from advtest_ext.core.models import SummaryStats
    from advtest_ext.storage import finalize_run

    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        upsert_run(session, run_id="conversation-run", target_name="SheetsTarget", strategy="grid")
        for index in range(6):
            turn = index + 1 if index < 5 else 1
            trace = AttackTrace(
                id=f"turn-{index}",
                run_id="conversation-run",
                target_name="SheetsTarget",
                behavior_id="sheets_skill_leak",
                depth=turn,
                payload=Payload(
                    id=f"p-{index}", text="payload", modality="text", generator_name="tap_crescendo"
                ),
                raw_request=f"User message {index}",
                raw_response=("Complete response " * 900 + f"END-OF-TURN-{index}"),
                verdict="success" if index == 4 else "refused",
                metadata={
                    "tap_crescendo": {
                        "node_id": "control" if index < 5 else "next-node",
                        "turn": turn,
                        "depth": 0,
                    }
                },
            )
            insert_trace(session, run_id="conversation-run", trace=trace)
        finding = promote_finding(
            session,
            run_id="conversation-run",
            trace_id="turn-4",
            severity="high",
            title="Skill disclosure",
        )
        finding_id = finding.id
        finalize_run(
            session,
            run_id="conversation-run",
            summary=SummaryStats(total_traces=6, successful_traces=1, overall_asr=1 / 6),
        )
    with TestClient(create_app(engine=engine)) as client:
        response = client.get("/runs/conversation-run")
        assert response.context["run_metrics"]["conversations"] == 2
        assert response.context["run_metrics"]["attempts"] == 6
        assert response.context["run_metrics"]["successful"] == 1
        assert len(response.context["selected_conversation"].turns) == 5
        assert "Multi-turn transcript →" not in response.text
        assert "Technique effectiveness" not in response.text
        assert "Heatmap, technique" not in response.text
        evidence = client.get(f"/runs/conversation-run/findings/{finding_id}/_evidence").text
        assert evidence.count('<section class="transcript-turn') == 5
        assert evidence.count("data-finding-turn") == 1
        assert "END-OF-TURN-0" in evidence and "END-OF-TURN-4" in evidence
        assert "END-OF-TURN-5" not in evidence
        assert "Jump to finding" in evidence


def test_charts_require_real_comparisons(review_store):
    from advtest_ext.storage.models import Trace

    client, factory = review_store
    assert not client.get("/runs/run-seed").context["show_technique_chart"]
    with session_scope(factory) as session:
        session.get(Trace, "t0").technique_id = "white_text"
        session.get(Trace, "review-0").technique_id = "encoded"
    response = client.get("/runs/run-seed")
    assert response.context["show_technique_chart"]
    assert response.context["show_technique_heatmap"]
    assert {row["technique"] for row in response.context["technique_heatmap"]["rows"]} == {
        "white_text",
        "encoded",
    }
    assert "Technique comparison" in response.text
    assert "Technique and payload breakdown" in response.text
