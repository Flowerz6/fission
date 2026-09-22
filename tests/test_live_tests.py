from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from advtest_ext.dashboard import create_app
from advtest_ext.dashboard.live_tests import (
    LiveTestController,
    _public_event,
    _read_env_file,
    live_test_catalog,
)
from advtest_ext.storage import make_engine


class _StubController:
    def __init__(self, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.specs = live_test_catalog()
        self.playbooks = ("sheets_skill_leak", "docs_language_override")
        self.started: list[str] = []
        self.stopped = 0
        self.state: dict[str, Any] = {
            "enabled": enabled,
            "active_id": None,
            "status": "idle",
            "exit_code": None,
            "lines": [],
        }

    def stop(self) -> dict[str, Any]:
        if self.state["status"] not in {"starting", "running"}:
            raise RuntimeError("no run is currently active")
        self.stopped += 1
        self.state.update(status="stopped")
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return dict(self.state)

    def start(
        self,
        test_id: str,
        *,
        playbook: str | None = None,
        branching_factor: int | None = None,
        max_depth: int | None = None,
        max_nodes_per_seed: int | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("live test launching is disabled")
        if test_id not in {spec.id for spec in self.specs}:
            raise KeyError(test_id)
        self.started.append(test_id)
        self.state.update(active_id=test_id, status="running")
        return self.snapshot()


def _client(controller: _StubController) -> TestClient:
    return TestClient(
        create_app(
            engine=make_engine("sqlite:///:memory:"),
            live_test_controller=controller,  # type: ignore[arg-type]
        )
    )


def test_live_tests_page_names_real_workflows() -> None:
    # The standalone launcher moved off /runs/new (now a modal on /runs); the
    # surface-assessment page still lives at /live-tests.
    with _client(_StubController()) as client:
        response = client.get("/live-tests")
    assert response.status_code == 200
    assert "Stop run" in response.text
    assert "Outlook → Gmail → Docs" in response.text
    assert "Docs — injection sweep" in response.text
    assert "Sheets — injection sweep" in response.text
    assert "Start demo" not in response.text
    assert "New run" in response.text


def test_live_test_start_requires_confirmation() -> None:
    controller = _StubController()
    with _client(controller) as client:
        response = client.post(
            "/live-tests/cross-surface-email-docs/start",
            json={"confirmed": False},
        )
    assert response.status_code == 422
    assert controller.started == []


def test_live_test_start_is_disabled_by_default() -> None:
    controller = _StubController(enabled=False)
    with _client(controller) as client:
        response = client.post(
            "/live-tests/cross-surface-email-docs/start",
            json={"confirmed": True},
        )
    assert response.status_code == 403


def test_live_test_start_returns_running_state() -> None:
    controller = _StubController()
    with _client(controller) as client:
        response = client.post(
            "/live-tests/cross-surface-email-docs/start",
            json={"confirmed": True},
        )
    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert controller.started == ["cross-surface-email-docs"]


def test_live_test_stop_marks_active_run_stopped() -> None:
    controller = _StubController()
    controller.state.update(active_id="guided-workspace-playbook", status="running")
    with _client(controller) as client:
        response = client.post("/live-tests/stop")
    assert response.status_code == 202
    assert response.json()["status"] == "stopped"
    assert controller.stopped == 1


def test_live_test_stop_rejects_when_idle() -> None:
    with _client(_StubController()) as client:
        response = client.post("/live-tests/stop")
    assert response.status_code == 409


def test_live_test_controller_rejects_launch_when_locked() -> None:
    controller = LiveTestController("sqlite:///:memory:", enabled=False)
    with pytest.raises(PermissionError):
        controller.start("cross-surface-email-docs")


def test_live_test_catalog_is_allow_listed() -> None:
    specs = live_test_catalog()
    assert {spec.id for spec in specs} == {
        "guided-workspace-playbook",
        "cross-surface-email-docs",
        "docs-injection-sweep",
        "sheets-injection-sweep",
    }
    commands = {spec.id: spec.argv for spec in specs}
    assert commands["guided-workspace-playbook"] == (
        "poetry",
        "run",
        "python",
        "scripts/run_guide.py",
    )
    assert commands["cross-surface-email-docs"] == (
        "poetry",
        "run",
        "python",
        "scripts/demo_flow.py",
        "--scenario",
        "scripts/demo_scenario.json",
    )
    assert commands["docs-injection-sweep"][:3] == ("poetry", "run", "advtest")
    assert commands["sheets-injection-sweep"][:3] == ("poetry", "run", "advtest")


def test_read_env_file_is_non_executable_and_supports_quotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# local secrets\nexport ANTHROPIC_API_KEY='secret value'\n"
        "BAD-KEY=nope\nSHELL_TRICK=$(touch /tmp/never-run)\n"
        "EMPTY_VALUE=  # optional setting\nQUOTED='value # retained' # comment\n",
        encoding="utf-8",
    )
    values = _read_env_file(path)
    assert values["ANTHROPIC_API_KEY"] == "secret value"
    assert "BAD-KEY" not in values
    assert values["SHELL_TRICK"] == "$(touch /tmp/never-run)"
    assert values["EMPTY_VALUE"] == ""
    assert values["QUOTED"] == "value # retained"


def test_public_activity_feed_redacts_implementation_details() -> None:
    assert _public_event("[9/12] exploit1 (docs_gemini)") == ("Workspace retrieval turn executed")
    event = _public_event("ANTHROPIC_API_KEY is not set. Export it")
    assert event == "Assessment could not start · Environment configuration is incomplete"
    assert "ANTHROPIC" not in event
    assert _public_event("  [claude] body = secret generated text") is None
