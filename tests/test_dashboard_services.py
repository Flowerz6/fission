"""Service filters operate on complete campaigns and canonical surface counts."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from advtest_ext.core.models import AttackTrace, Payload, SummaryStats
from advtest_ext.dashboard import create_app
from advtest_ext.dashboard.app import build_surfaces_context
from advtest_ext.dashboard.services import group_surfaces, scope_campaign_reads, target_identity
from advtest_ext.storage import (
    finalize_run,
    insert_trace,
    make_engine,
    make_session_factory,
    promote_finding,
    session_scope,
    upsert_run,
)
from advtest_ext.storage.repository import list_runs


@pytest.fixture
def service_engine():
    engine = make_engine("sqlite:///:memory:")
    factory = make_session_factory(engine)
    with session_scope(factory) as session:
        for run_id, target, traces in [
            (
                "gemini-one",
                "GeminiSheetsMultiTurnTarget",
                ["GeminiSheetsMultiTurnTarget", "SheetsTarget", "GmailTarget"],
            ),
            ("gemini-two", "SheetsTarget", ["GeminiSheetsSidePanel"]),
            ("openai-one", "OpenAIChatTarget", ["OpenAIChatTarget"]),
            ("unknown-one", "StubDocumentTarget", ["StubDocumentTarget"]),
        ]:
            upsert_run(session, run_id=run_id, target_name=target, strategy="grid")
            for i, trace_target in enumerate(traces):
                trace = AttackTrace(
                    id=f"{run_id}-{i}",
                    run_id=run_id,
                    target_name=trace_target,
                    behavior_id=f"scenario-{run_id}",
                    payload=Payload(
                        id=f"p-{run_id}-{i}",
                        text="payload",
                        modality="text",
                        generator_name="static_payload",
                    ),
                    raw_request="test prompt",
                    raw_response="test response",
                    verdict="success",
                )
                insert_trace(session, run_id=run_id, trace=trace)
                promote_finding(
                    session,
                    run_id=run_id,
                    trace_id=trace.id,
                    severity="high",
                    title=f"Finding {run_id}-{i}",
                )
            finalize_run(
                session,
                run_id=run_id,
                summary=SummaryStats(
                    total_traces=len(traces), successful_traces=len(traces), overall_asr=1.0
                ),
            )
    return engine


@pytest.mark.parametrize(
    "name,surface",
    [
        ("GoogleDocsTarget", "docs"),
        ("google_docs_multiturn", "docs"),
        ("GeminiDocsMultiTurnTarget", "docs"),
        ("SheetsTarget", "sheets"),
        ("GeminiSheetsSidePanel", "sheets"),
        ("GmailTarget", "gmail"),
        ("SlidesTarget", "slides"),
        ("CalendarTarget", "calendar"),
    ],
)
def test_known_adapter_aliases(name, surface):
    assert target_identity(name)[:2] == ("gemini", surface)


def test_stub_document_is_not_misclassified_as_gemini():
    assert target_identity("StubDocumentTarget")[0] == "other"
    assert target_identity("StubDocumentTarget")[1] != target_identity("DifferentTarget")[1]


def test_service_selection_scopes_collections_and_persists(service_engine):
    with TestClient(create_app(engine=service_engine)) as client:
        overview = client.get("/?service=gemini")
        assert overview.context["s"].total_runs == 2
        assert overview.context["s"].total_traces == 4
        assert overview.context["s"].total_findings == 4
        assert client.cookies.get("fission-service") == "gemini"
        assert {r.id for r in client.get("/runs").context["runs"]} == {"gemini-one", "gemini-two"}
        findings = client.get("/findings")
        assert len(findings.context["findings"]) == 4
        assert {rid for rid, _ in findings.context["run_options"]} == {"gemini-one", "gemini-two"}
        assert set(findings.context["behavior_options"]) == {
            "scenario-gemini-one",
            "scenario-gemini-two",
        }
        assert {r.id for r in client.get("/reports").context["runs"]} == {
            "gemini-one",
            "gemini-two",
        }
        assert client.get("/?service=openai").context["s"].total_traces == 1
        assert [r.id for r in client.get("/runs").context["runs"]] == ["openai-one"]
        partial = client.get("/findings?service=gemini", headers={"HX-Request": "true"})
        assert "Finding gemini-one" in partial.text and "Finding openai-one" not in partial.text
        assert len(client.get("/runs?service=all").context["runs"]) == 4
        assert client.get("/runs?service=invalid").status_code == 400
        assert client.cookies.get("fission-service") == "all"


def test_grouping_merges_adapters_and_deduplicates_campaigns(service_engine):
    factory = make_session_factory(service_engine)
    with factory() as session:
        scope_campaign_reads(session, ["gemini-one", "gemini-two"])
        groups = group_surfaces(session, build_surfaces_context(session)["surfaces"], "gemini")
        sheets = next(group for group in groups if group["key"] == "sheets")
        assert sheets["attempts"] == 3
        assert sheets["runs"] == 2  # two configurations in gemini-one count once
        assert sheets["findings"] == 3
        assert len(sheets["adapters"]) == 3
        assert sheets["sev_chips"] == [("high", 3)]
        assert len(groups) == 6
        drive = next(group for group in groups if group["key"] == "drive")
        assert drive["exposure_label"] == "Not configured"
        docs = next(group for group in groups if group["key"] == "docs")
        assert docs["exposure_label"] == "Not tested"


def test_group_panels_keep_adapter_evidence_and_campaign_links(service_engine):
    with TestClient(create_app(engine=service_engine)) as client:
        response = client.get("/surface-groups/gemini/sheets/_panel?service=gemini")
        assert response.status_code == 200
        assert 'href="/runs/gemini-one"' in response.text
        assert "GeminiSheetsMultiTurnTarget" in response.text
        adapter = client.get("/surfaces/SheetsTarget/_panel")
        assert "Finding gemini-one-1" in adapter.text
        assert (
            client.get("/surface-groups/openai/assistant/_panel?service=gemini").status_code == 404
        )
        # Direct evidence remains intact regardless of a collection selection.
        assert client.get("/runs/openai-one").status_code == 200


def test_service_filter_applies_before_query_limit(service_engine):
    with make_session_factory(service_engine)() as session:
        scope_campaign_reads(session, ["gemini-one"])
        assert [run.id for run in list_runs(session, limit=1)] == ["gemini-one"]
