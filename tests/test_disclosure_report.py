"""Portable disclosure reports preserve evidence and avoid unsupported claims."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from advtest_ext.core.models import AttackTrace, Payload
from advtest_ext.dashboard import create_app
from advtest_ext.storage import (
    insert_gold_label,
    insert_trace,
    make_engine,
    make_session_factory,
    promote_finding,
    session_scope,
    upsert_run,
)
from advtest_ext.storage.models import Finding, Run, Trace
from tests.test_dashboard import _seed_run


@pytest.fixture
def reports():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    _seed_run(factory)
    with session_scope(factory) as session:
        run = session.get(Run, "run-seed")
        run.target_name = "GoogleDocsTarget"
        run.config = {
            "target_options": {"model": "recorded-model-version", "api_key": "DO-NOT-EXPORT-KEY"}
        }
        for index in range(2):
            trace = session.get(Trace, f"t{index}")
            trace.target_name = "GoogleDocsTarget"
            trace.extra_metadata = {"tap_crescendo": {"node_id": "n0", "turn": index + 1}}
            trace.raw_response = "Evidence line.\n" * 140 + f"END-OF-RESPONSE-{index}"
            trace.payload_text = "payload\n" * 160 + "END-OF-PAYLOAD"
        insert_gold_label(
            session,
            trace_id="t0",
            run_id="run-seed",
            behavior_id="exfil_test_0",
            oracle_verdict="success",
            expert_verdict="refused",
            sample_reason="uniform",
            response_snapshot=session.get(Trace, "t0").raw_response,
        )
        upsert_run(session, run_id="other", target_name="OpenAIChatTarget", strategy="grid")
        trace = AttackTrace(
            id="other-trace",
            run_id="other",
            target_name="OpenAIChatTarget",
            behavior_id="test",
            payload=Payload(
                id="other-p", text="test", modality="text", generator_name="static_payload"
            ),
            raw_request="Other service request",
            raw_response="Other service response",
            verdict="success",
        )
        insert_trace(session, run_id="other", trace=trace)
        promote_finding(
            session,
            run_id="other",
            trace_id=trace.id,
            severity="high",
            title="Other service finding",
        )
        finding_id = session.query(Finding).filter(Finding.run_id == "run-seed").one().id
    with TestClient(create_app(engine=engine)) as client:
        yield client, factory, finding_id


def test_report_has_complete_evidence_and_review_state(reports):
    client, _, finding_id = reports
    response = client.get(
        "/reports/view",
        params={
            "service": "gemini",
            "recipient": "Google AI Security",
            "author": "Research team",
            "contact": "research@example.test",
        },
    )
    assert response.status_code == 200
    assert response.context["total"] == 1
    case = response.context["cases"][0]
    assert case["id"] == finding_id
    assert case["validation"] == "Human-reviewed response: refused"
    assert case["model"] == "recorded-model-version"
    assert len(case["turns"]) == 2
    assert "END-OF-RESPONSE-0" in response.text and "END-OF-RESPONSE-1" in response.text
    assert "END-OF-PAYLOAD" in response.text
    assert "DO-NOT-EXPORT-KEY" not in response.text
    assert "Other service finding" not in response.text
    assert "Evidence draft" in response.text
    assert "Google AI Security" in response.text
    assert "Security impact and expected behavior" in response.text
    assert case["artifacts"][0]["response_sha256"] in response.text


def test_single_finding_export_respects_service_scope(reports):
    client, _, finding_id = reports
    report = client.get("/reports/view", params={"finding_id": finding_id, "service": "all"})
    assert report.context["total"] == 1
    assert report.context["source_runs"] == 1
    excluded = client.get("/reports/view", params={"finding_id": finding_id, "service": "openai"})
    assert excluded.context["total"] == 0
    assert finding_id not in excluded.text.split("<main")[1]


def test_invalid_dates_do_not_silently_expand_scope(reports):
    client, _, _ = reports
    assert client.get("/reports/view?date_from=2026-02-31").status_code == 422
    assert client.get("/reports/view?date_from=2026-09-18&date_to=2026-09-01").status_code == 422


def test_pdf_is_available_without_weasyprint(reports, monkeypatch, tmp_path):
    from advtest_ext.dashboard import report

    monkeypatch.setattr(report, "WEASYPRINT_INSTALLED", False)
    client, _, _ = reports
    response = client.get("/reports/view.pdf?service=gemini&recipient=Google")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
    Path("/tmp/fission-disclosure-test.pdf").write_bytes(response.content)
    preview = client.get("/reports/view?service=gemini")
    assert preview.context["pdf_available"]


def test_report_escapes_author_text_and_preserves_renamed_runs(reports):
    client, factory, _ = reports
    with session_scope(factory) as session:
        session.get(Run, "run-seed").config = {"display_name": "Named assessment"}
    response = client.get(
        "/reports/view", params={"service": "gemini", "author": "<script>alert(1)</script>"}
    )
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "Named assessment" in response.text
