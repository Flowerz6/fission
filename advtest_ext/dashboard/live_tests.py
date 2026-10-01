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
            title="Multi-turn guide assessment",
            surface="Google Workspace",
            method="Guided branching exploration",
            description=(
                "Runs a selected guide through a newly created Workspace "
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


@dataclass(frozen=True)
class _QueuedRun:
    """A single prepared guide run waiting its turn in a back-to-back batch."""

    test_id: str
    playbook: str
    argv: tuple[str, ...]
    run_id: str
    label: str


class LiveTestController:
    """Run allow-listed workflows one at a time; optionally queue several
    guide runs to execute back-to-back (a batch)."""

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
            stem
            for stem in sorted(
                {
                    p.stem
                    for pat in ("*.json", "*.yml", "*.yaml")
                    for p in (_REPO_ROOT / "guides").glob(pat)
                }
            )
        )
        # Reentrant: _advance() pops the next queued run and calls _spawn()
        # while already holding the lock, so the whole hand-off is atomic
        # against a concurrent stop().
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._active_id: str | None = None
        self._run_id: str | None = None
        self._status = "idle"
        self._exit_code: int | None = None
        self._stop_requested = False
        self._lines: deque[str] = deque(maxlen=300)
        # Back-to-back batch state: a queue of prepared runs launched one at a
        # time, and an ordered per-item status list the dashboard renders.
        self._queue: deque[_QueuedRun] = deque()
        self._batch: list[dict[str, Any]] = []
        self._batch_active = False

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
        max_depth: int | None = None,
        max_nodes_per_seed: int | None = None,
    ) -> dict[str, Any]:
        """Launch a single workflow (the existing one-off path)."""
        if not self.enabled:
            raise PermissionError("live test launching is disabled")
        spec = self._specs.get(test_id)
        if spec is None:
            raise KeyError(test_id)
        env = _assessment_environment()
        if test_id in {"guided-workspace-playbook", "cross-surface-email-docs"} and not env.get(
            "ANTHROPIC_API_KEY"
        ):
            raise ValueError("Assessment services are not configured on this deployment")
        # A single launch clears any prior batch display so a finished queue
        # doesn't linger, and re-arms the stop flag for this fresh session.
        with self._lock:
            self._queue.clear()
            self._batch = []
            self._batch_active = False
            self._stop_requested = False
        if test_id == "guided-workspace-playbook":
            prepared = self._prepare_guided(
                playbook,
                branching_factor=branching_factor,
                max_depth=max_depth,
                max_nodes_per_seed=max_nodes_per_seed,
            )
            # Reserve the Run row synchronously so the run page resolves the
            # instant the launcher redirects to it — the subprocess only writes
            # its own Run row after staging (browser login + carrier plant), a
            # 30-60s window during which /runs/<id> would otherwise 404.
            self._precreate_run_row(prepared.run_id, prepared.playbook)
            return self._spawn(prepared.argv, test_id=test_id, run_id=prepared.run_id)
        argv = spec.argv
        launch_run_id: str | None = None
        if test_id == "cross-surface-email-docs":
            launch_run_id = f"demo-{uuid.uuid4().hex[:12]}"
            argv = (*argv, "--run-id", launch_run_id)
        return self._spawn(argv, test_id=test_id, run_id=launch_run_id)

    def start_batch(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """Queue several guide runs to execute back-to-back. Each item is a
        ``{playbook, branching_factor?, max_depth?, max_nodes_per_seed?}`` dict.
        The first launches immediately; the rest fire automatically as each
        predecessor finishes (see :meth:`_advance`). All items are validated up
        front, so one bad guide rejects the whole batch before anything runs."""
        if not self.enabled:
            raise PermissionError("live test launching is disabled")
        if not items:
            raise ValueError("select at least one guide to queue")
        if len(items) > 50:
            raise ValueError("queue at most 50 runs at once")
        env = _assessment_environment()
        if not env.get("ANTHROPIC_API_KEY"):
            raise ValueError("Assessment services are not configured on this deployment")
        prepared = [
            self._prepare_guided(
                item.get("playbook"),
                branching_factor=item.get("branching_factor"),
                max_depth=item.get("max_depth"),
                max_nodes_per_seed=item.get("max_nodes_per_seed"),
            )
            for item in items
        ]
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("another live test is already running")
            self._stop_requested = False
            self._queue = deque(prepared)
            self._batch = [
                {"playbook": p.playbook, "label": p.label, "run_id": p.run_id, "status": "queued"}
                for p in prepared
            ]
            self._batch_active = True
            # Launch the first item now; _advance pops it under the same lock.
            self._advance()
        return self.snapshot()

    def _prepare_guided(
        self,
        playbook: str | None,
        *,
        branching_factor: int | None = None,
        max_depth: int | None = None,
        max_nodes_per_seed: int | None = None,
    ) -> _QueuedRun:
        """Validate the guide + tree knobs and build the runner argv + run id.

        The node budget is the HARD cap on nodes explored per seed — depth and
        branching do nothing while it stays at the default 4, so raise it to
        search widely for more successful mutations. Generous ceilings keep a
        single run from ballooning into thousands of live sessions."""
        spec = self._specs["guided-workspace-playbook"]
        if playbook not in self._playbooks:
            raise ValueError("select a valid guide")
        factor = 1 if branching_factor is None else branching_factor
        if not 1 <= factor <= 5:
            raise ValueError("branching factor must be between 1 and 5")
        depth = 3 if max_depth is None else max_depth
        if not 1 <= depth <= 12:
            raise ValueError("max depth must be between 1 and 12")
        node_budget = 4 if max_nodes_per_seed is None else max_nodes_per_seed
        if not 1 <= node_budget <= 400:
            raise ValueError("node budget must be between 1 and 400")
        run_id = f"run-{uuid.uuid4().hex[:12]}"
        argv = (
            *spec.argv,
            "--guide",
            str(playbook),
            "--browser-mint",
            # Every dashboard run uses CLAUDE end-to-end (attacker + planner) so the
            # diagnose->adapt reasoning runs at full quality without a CLI flag. See
            # configs/dashboard.yaml. --planner-role splits the Claude planner from
            # the Claude turn-writer so the branch diagnosis is its own strong call.
            "--provider-config",
            "configs/dashboard.yaml",
            "--planner-role",
            "CLAUDE_PLANNER",
            "--branching-factor",
            str(factor),
            "--max-depth",
            str(depth),
            "--max-nodes-per-seed",
            str(node_budget),
            "--run-id",
            run_id,
        )
        return _QueuedRun("guided-workspace-playbook", str(playbook), argv, run_id, str(playbook))

    def _spawn(
        self, argv: tuple[str, ...], *, test_id: str, run_id: str | None
    ) -> dict[str, Any]:
        """Launch a prepared argv as the sole active process. Does NOT touch
        ``_stop_requested`` (callers arm it) so batch auto-advance can't clear a
        stop that arrived mid-hand-off."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("another live test is already running")
            self._lines.clear()
            self._active_id = test_id
            self._run_id = run_id
            self._status = "starting"
            self._exit_code = None
            env = _assessment_environment()
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

    def _advance(self) -> None:
        """Launch the next queued run, if any. Holds the lock across pop→spawn
        so a concurrent stop() (which clears the queue) can't slip a cancelled
        run through. When the queue drains, the batch is marked inactive."""
        with self._lock:
            if self._stop_requested or not self._queue:
                if not self._queue:
                    self._batch_active = False
                return
            nxt = self._queue.popleft()
            self._mark_batch(nxt.run_id, "running")
            self._precreate_run_row(nxt.run_id, nxt.playbook)
            self._spawn(nxt.argv, test_id=nxt.test_id, run_id=nxt.run_id)

    def _mark_batch(self, run_id: str | None, status: str) -> None:
        """Set the per-item status for ``run_id`` in the batch display list."""
        if run_id is None:
            return
        for item in self._batch:
            if item["run_id"] == run_id:
                item["status"] = status
                break

    def _precreate_run_row(self, run_id: str, playbook: str | None) -> None:
        """Insert (idempotent) the Run row at launch so the run page resolves
        immediately with a live, stoppable tree. Best-effort: on any failure the
        launch still proceeds and the subprocess creates the row itself — the
        only regression is the brief 'run not found' window this closes."""
        try:
            from advtest_ext.guides.corpus import load_guides
            from advtest_ext.guides.run import default_target_name
            from advtest_ext.storage import (
                make_engine,
                make_session_factory,
                session_scope,
                upsert_run,
            )

            target_name = "google_docs_multiturn"
            if playbook:
                guide = next(
                    (g for g in load_guides(_REPO_ROOT / "guides") if g.id == playbook), None
                )
                if guide is not None:
                    try:
                        target_name = default_target_name(guide)
                    except Exception:
                        target_name = guide.target.app
            factory = make_session_factory(make_engine(self.db_url))
            with session_scope(factory) as s:
                upsert_run(s, run_id=run_id, target_name=target_name, strategy="tap_crescendo")
        except Exception as exc:  # never block a launch on the reservation
            with self._lock:
                self._lines.append(f"(run row reservation skipped: {type(exc).__name__})")

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
        should_advance = False
        with self._lock:
            if self._process is process:
                self._exit_code = exit_code
                if self._stop_requested:
                    self._status = "stopped"
                    self._lines.append("Run stopped by administrator")
                    final = "stopped"
                else:
                    self._status = "succeeded" if exit_code == 0 else "failed"
                    final = self._status
                self._mark_batch(self._run_id, final)
                # Auto-advance the batch: launch the next queued run unless the
                # operator stopped it. When the queue is empty, the batch ends.
                if self._batch_active and not self._stop_requested and self._queue:
                    should_advance = True
                elif self._batch_active and not self._queue:
                    self._batch_active = False
        if should_advance:
            self._advance()

    def stop(self) -> dict[str, Any]:
        """Stop the active workflow and every child in its process group, and
        cancel any remaining queued runs so a batch halts cleanly."""
        with self._lock:
            process = self._process
            # Cancel the rest of the batch either way — the operator asked to
            # stop, so nothing further should auto-launch.
            for item in self._queue:
                self._mark_batch(item.run_id, "stopped")
            self._queue.clear()
            self._batch_active = False
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
                "batch": [dict(item) for item in self._batch],
                "batch_active": self._batch_active,
                "queued": len(self._queue),
            }
