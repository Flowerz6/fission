"""Run actions persist names and delete only selected, completed run evidence."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from advtest_ext.core.models import RunConfig
from advtest_ext.dashboard import create_app
from advtest_ext.storage import make_engine, make_session_factory, session_scope, upsert_run
from advtest_ext.storage.models import (
    Finding,
    GoldLabel,
    HitlItem,
    JudgeScore,
    Run,
    SignalRecord,
    Trace,
)
from tests.test_dashboard import _seed_run


@pytest.fixture(params=[False, True], ids=["sqlite-default", "foreign-keys-enabled"])
def run_store(request, tmp_path):
    engine = make_engine("sqlite:///:memory:")
    if request.param:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    factory = make_session_factory(engine)
    _seed_run(factory)
    artifact = tmp_path / "saved-report.txt"
    artifact.write_text("Saved evidence")
    with session_scope(factory) as session:
        run = session.get(Run, "run-seed")
        run.config = {"behaviors": ["leak_system_prompt"], "budget": {"max_queries": 5}}
        run.output_dir = str(tmp_path)
        session.add(Run(id="keep", target_name="GmailTarget", strategy="grid"))
        session.add(SignalRecord(trace_id="t0", name="marker", kind="token_appears", fired=True))
        session.add(
            GoldLabel(
                id="gold",
                run_id="run-seed",
                trace_id="t0",
                behavior_id="leak_system_prompt",
                oracle_verdict="success",
                expert_verdict="success",
                sample_reason="uniform",
                response_snapshot="Saved human-reviewed response",
            )
        )
    state = {"status": "idle", "run_id": None}
    controller = SimpleNamespace(snapshot=lambda: state)
    with TestClient(create_app(engine=engine, live_test_controller=controller)) as client:
        yield client, factory, state, artifact


def test_rename_persists_preserves_config_and_survives_finalization(run_store):
    client, factory, _, _ = run_store
    response = client.patch("/runs/run-seed", json={"name": "  Docs extraction check  "})
    assert response.status_code == 200
    assert response.json()["name"] == "Docs extraction check"
    with session_scope(factory) as session:
        run = session.get(Run, "run-seed")
        assert run.config == {
            "display_name": "Docs extraction check",
            "behaviors": ["leak_system_prompt"],
            "budget": {"max_queries": 5},
        }
        upsert_run(
            session,
            run_id=run.id,
            target_name=run.target_name,
            strategy=run.strategy,
            config=RunConfig(
                target_name="stub",
                output_dir="out",
                attacks=["static_payload"],
                behaviors=["leak_system_prompt"],
            ),
        )
    assert "Docs extraction check" in client.get("/runs?service=all").text
    assert "Docs extraction check" in client.get("/reports?service=all").text


def test_invalid_names_and_missing_runs(run_store):
    client, _, _, _ = run_store
    for name in ["", "   ", "x" * 121]:
        assert client.patch("/runs/run-seed", json={"name": name}).status_code == 422
    assert client.patch("/runs/missing", json={"name": "A name"}).status_code == 404
    assert (
        client.request("DELETE", "/runs/missing", json={"confirmation": "missing"}).status_code
        == 404
    )


def test_delete_cleans_related_results_and_keeps_labels_files_and_other_runs(run_store):
    client, factory, _, artifact = run_store
    response = client.request("DELETE", "/runs/run-seed", json={"confirmation": "run-seed"})
    assert response.status_code == 204
    with factory() as session:
        assert session.get(Run, "run-seed") is None
        assert session.get(Run, "keep") is not None
        for model in (Trace, Finding, HitlItem, JudgeScore, SignalRecord):
            assert session.scalars(select(model)).all() == []
        label = session.get(GoldLabel, "gold")
        assert label.run_id is None and label.trace_id is None
        assert label.response_snapshot == "Saved human-reviewed response"
    assert artifact.read_text() == "Saved evidence"
    assert client.get("/runs/run-seed").status_code == 404


def test_delete_requires_confirmation_and_rejects_active_runs(run_store):
    client, factory, state, _ = run_store
    assert (
        client.request("DELETE", "/runs/run-seed", json={"confirmation": "wrong"}).status_code
        == 422
    )
    assert client.request("DELETE", "/runs/run-seed").status_code == 422
    assert client.request("DELETE", "/runs/keep", json={"confirmation": "keep"}).status_code == 409
    state.update(status="running", run_id="run-seed")
    assert (
        client.request("DELETE", "/runs/run-seed", json={"confirmation": "run-seed"}).status_code
        == 409
    )
    with factory() as session:
        assert session.get(Run, "run-seed") is not None
        assert session.get(Trace, "t0") is not None


def test_cross_site_changes_are_rejected_and_names_are_escaped(run_store):
    client, _, _, _ = run_store
    headers = {"Origin": "https://unrelated.example"}
    assert (
        client.patch("/runs/run-seed", json={"name": "Changed"}, headers=headers).status_code == 403
    )
    assert (
        client.request(
            "DELETE", "/runs/run-seed", json={"confirmation": "run-seed"}, headers=headers
        ).status_code
        == 403
    )
    name = "<img src=x onerror=alert(1)>"
    assert client.patch("/runs/run-seed", json={"name": name}).status_code == 200
    page = client.get("/runs?service=all").text
    assert name not in page
    assert "&lt;img" in page


def test_run_actions_require_configured_auth():
    engine = make_engine("sqlite:///:memory:")
    with TestClient(create_app(engine=engine, auth_token="test-only")) as client:
        assert client.patch("/runs/anything", json={"name": "Changed"}).status_code == 401
        assert (
            client.request(
                "DELETE", "/runs/anything", json={"confirmation": "anything"}
            ).status_code
            == 401
        )


def test_failed_delete_rolls_back_all_evidence(run_store, monkeypatch):
    import importlib

    module = importlib.import_module("advtest_ext.dashboard.app")
    original = module.delete_run_results

    def fail_before_commit(session, run_id):
        original(session, run_id)
        raise RuntimeError("Simulated write failure")

    monkeypatch.setattr(module, "delete_run_results", fail_before_commit)
    client, factory, _, _ = run_store
    with pytest.raises(RuntimeError, match="Simulated write failure"):
        client.request("DELETE", "/runs/run-seed", json={"confirmation": "run-seed"})
    with factory() as session:
        assert session.get(Run, "run-seed") is not None
        assert session.get(Trace, "t0") is not None
        assert session.get(GoldLabel, "gold").run_id == "run-seed"


def test_report_export_supports_print_without_optional_renderer(run_store, monkeypatch):
    from advtest_ext.dashboard import report

    monkeypatch.setattr(report, "WEASYPRINT_INSTALLED", False)
    client, _, _, _ = run_store
    response = client.get("/runs/run-seed/report?export=1")
    assert response.status_code == 200
    assert "Print / Save as PDF" in response.text
    assert "Download PDF" not in response.text
    assert "Print / Save as PDF" not in client.get("/runs/run-seed/report").text
    monkeypatch.setattr(report, "WEASYPRINT_INSTALLED", True)
    assert "Download PDF" in client.get("/runs/run-seed/report?export=1").text
