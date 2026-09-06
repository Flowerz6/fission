"""Guarded process controller for operator-launched live test workflows."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]

_STEP_LABELS = {
    "claude_write": "Scenario content prepared",
    "outlook_send": "Carrier message delivered",
    "gmail_send": "Cross-account response delivered",
    "docs_gemini": "Workspace retrieval turn executed",
}


def _public_event(line: str) -> str | None:
    """Convert runner output into a vendor-neutral customer activity event."""
    text = line.strip()
    if not text:
        return None
    step = re.match(r"\[(\d+)/(\d+)\]\s+\S+\s+\(([^)]+)\)", text)
    if step:
        kind = step.group(3)
        return _STEP_LABELS.get(kind, "Assessment activity recorded")
    lowered = text.lower()
    if "is not set" in lowered or "credential" in lowered or "api_key" in lowered:
        return "Assessment could not start · Environment configuration is incomplete"
    if "traceback" in lowered or text.startswith(("File ", "raise ")):
        return "Assessment encountered an execution error"
    if "[browser]" in lowered:
        return "Workspace session opened"
    if "[db] +" in lowered:
        return "Evidence checkpoint recorded"
    if text.startswith("Run:"):
        return "Assessment evidence finalized"
    if "injection fired" in lowered:
        return "Assessment completed · Test condition observed"
    if "canary not detected" in lowered:
        return "Assessment completed · Test condition not observed"
    return None


def _read_env_file(path: Path) -> dict[str, str]:
    """Parse simple KEY=VALUE entries without executing shell code."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        if value.startswith(("'", '"')):
            quote = value[0]
            closing = value.find(quote, 1)
            if closing != -1:
                value = value[1:closing]
        else:
            value = (
                "" if value.startswith("#") else re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
            )
        values[key] = value
    return values


def _assessment_environment() -> dict[str, str]:
    """Merge repo-local secrets into a child-only environment.

    Real process environment values win, matching normal deployment secret
    injection semantics. The .env file is read at launch time, so rotating a
    local key does not require restarting the dashboard.
    """
    env = os.environ.copy()
    for key, value in _read_env_file(_REPO_ROOT / ".env").items():
        if value and not env.get(key):
            env[key] = value
    return env


@dataclass(frozen=True)
class LiveTestSpec:
    id: str
    title: str
    surface: str
    method: str
    description: str
    prerequisites: tuple[str, ...]
    argv: tuple[str, ...]


def live_test_catalog() -> tuple[LiveTestSpec, ...]:
    """Return the fixed allow-list of workflows exposed in the dashboard."""
    return (
        LiveTestSpec(
            id="guided-workspace-playbook",
            title="Multi-turn playbook assessment",
            surface="Google Workspace",
            method="Guided branching exploration",
            description=(
                "Runs a selected assessment playbook through a newly created Workspace "
                "surface with a configurable exploration width."
            ),
            prerequisites=(
                "Workspace browser profile is connected",
                "Assessment policy and evaluator are configured",
            ),
            argv=("poetry", "run", "python", "scripts/run_guide.py"),
        ),
        LiveTestSpec(
            id="cross-surface-email-docs",
            title="Outlook → Gmail → Docs",
            surface="Cross-surface · multi-account",
            method="Multi-turn retrieval IPI",
            description=(
                "Plants a benign hidden canary in an Outlook-to-Gmail thread, then "
                "evaluates the Workspace document side panel across four retrieval turns."
            ),
            prerequisites=(
                "Sender and recipient accounts are connected",
                "Workspace document connection is verified",
                "Assessment services are available",
            ),
            argv=(
                "poetry",
                "run",
                "python",
                "scripts/demo_flow.py",
                "--scenario",
                "scripts/demo_scenario.json",
            ),
        ),
        LiveTestSpec(
            id="docs-injection-sweep",
            title="Docs — injection sweep",
            surface="Google Docs",
            method="Single-turn technique grid",
            description=(
                "Runs the bounded indirect-injection technique grid against the "
                "configured Google Docs target and streams traces to this dashboard."
            ),
            prerequisites=(
                "Workspace connection is verified",
                "Assessment policy and evaluator are configured",
            ),
            argv=(
                "poetry",
                "run",
                "advtest",
                "run",
                "--config",
                "configs/run_workspace_docs.yaml",
            ),
        ),
        LiveTestSpec(
            id="sheets-injection-sweep",
            title="Sheets — injection sweep",
            surface="Google Sheets",
            method="Single-turn technique grid",
            description=(
                "Runs the bounded Sheets indirect-injection configuration and "
                "streams traces to this dashboard."
            ),
            prerequisites=(
                "Sheets and Drive connections are verified",
                "Assessment policy and evaluator are configured",
            ),
            argv=(
                "poetry",
                "run",
                "advtest",
                "run",
                "--config",
                "configs/demo_workspace_sheets.yaml",
            ),
        ),
    )


class LiveTestController:
    """Run at most one allow-listed workflow and retain a bounded console."""

    def __init__(self, db_url: str, *, enabled: bool | None = None) -> None:
        self.db_url = db_url
        configured_env = _assessment_environment()
        self.enabled = (
            configured_env.get("ADVTEST_ENABLE_LIVE_TESTS", "").lower() in {"1", "true", "yes"}
            if enabled is None
            else enabled
        )
        self._specs = {spec.id: spec for spec in live_test_catalog()}
        self._playbooks = tuple(
            path.stem for path in sorted((_REPO_ROOT / "guides").glob("*.json"))
        )
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._active_id: str | None = None
        self._run_id: str | None = None
        self._status = "idle"
        self._exit_code: int | None = None
        self._stop_requested = False
        self._lines: deque[str] = deque(maxlen=300)

    @property
    def specs(self) -> tuple[LiveTestSpec, ...]:
        return tuple(self._specs.values())

    @property
    def playbooks(self) -> tuple[str, ...]:
        return self._playbooks

    def start(
        self,
        test_id: str,
        *,
        playbook: str | None = None,
        branching_factor: int | None = None,
    ) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("live test launching is disabled")
        spec = self._specs.get(test_id)
        if spec is None:
            raise KeyError(test_id)
        argv = spec.argv
        launch_run_id: str | None = None
        env = _assessment_environment()
        if test_id in {"guided-workspace-playbook", "cross-surface-email-docs"} and not env.get(
            "ANTHROPIC_API_KEY"
        ):
            raise ValueError("Assessment services are not configured on this deployment")
        if test_id == "guided-workspace-playbook":
            if playbook not in self._playbooks:
                raise ValueError("select a valid assessment playbook")
            factor = 1 if branching_factor is None else branching_factor
            if not 1 <= factor <= 5:
                raise ValueError("branching factor must be between 1 and 5")
            launch_run_id = f"run-{uuid.uuid4().hex[:12]}"
            argv = (*argv, "--guide", playbook, "--browser-mint", "--branching-factor", str(factor))
            argv = (*argv, "--run-id", launch_run_id)
        elif test_id == "cross-surface-email-docs":
            launch_run_id = f"demo-{uuid.uuid4().hex[:12]}"
            argv = (*argv, "--run-id", launch_run_id)
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("another live test is already running")
            self._lines.clear()
            self._active_id = test_id
            self._run_id = launch_run_id
            self._status = "starting"
            self._exit_code = None
            self._stop_requested = False
            env["ADVTEST_DB_URL"] = self.db_url
            env["PYTHONUNBUFFERED"] = "1"
            self._process = subprocess.Popen(
                argv,
                cwd=_REPO_ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                start_new_session=True,
            )
            self._status = "running"
            threading.Thread(target=self._collect, args=(self._process,), daemon=True).start()
        return self.snapshot()

    def _collect(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            event = _public_event(line)
            if event is None:
                continue
            with self._lock:
                if not self._lines or self._lines[-1] != event:
                    self._lines.append(event)
        exit_code = process.wait()
        with self._lock:
            if self._process is process:
                self._exit_code = exit_code
                if self._stop_requested:
                    self._status = "stopped"
                    self._lines.append("Run stopped by administrator")
                else:
                    self._status = "succeeded" if exit_code == 0 else "failed"

    def stop(self) -> dict[str, Any]:
        """Stop the active workflow and every child in its process group."""
        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                raise RuntimeError("no run is currently active")
            self._stop_requested = True
            self._status = "stopping"
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "active_id": self._active_id,
                "run_id": self._run_id,
                "status": self._status,
                "exit_code": self._exit_code,
                "lines": list(self._lines),
            }
