"""Dashboard surface for the PIMiner strategy loop (advtest_ext/strategy/).

Launches ``scripts/run_strategy_loop.py`` as a guarded subprocess — reusing
``live_tests``' assessment environment so the child inherits the connected
Workspace browser profile (``ADVTEST_PLAYWRIGHT_PROFILE_DIR``) and the
provider keys — and renders the persisted ``strategy_runs`` /
``strategy_attempts`` / ``strategy_replays`` tables (lineage, parent chain,
true M/N). One run at a time, gated by ``ADVTEST_ENABLE_LIVE_TESTS`` exactly
like the live-test launcher.
"""

from __future__ import annotations

import subprocess
import threading
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from advtest_ext.dashboard.live_tests import _REPO_ROOT, _assessment_environment
from advtest_ext.storage.models import StrategyAttempt, StrategyReplay, StrategyRun


class StrategyStart(BaseModel):
    """JSON launch submission (the dashboard posts this; no form-encoding, so
    no python-multipart dependency)."""

    target: str = "workspace"
    samples: str = "tests/data/strategy_samples.jsonl"
    n_max: int = 10
    replay: int = 0
    route: bool = False
    trigger_prompt: str | None = None
    target_model: str = "gpt-4o-mini"

_ALLOWED_TARGETS = ("workspace", "agentdojo", "offline")
_DEFAULT_LIBRARY = "advtest_ext/strategy/library"


class StrategyController:
    """Run one strategy-loop subprocess at a time, gated and log-streamed."""

    def __init__(self, db_url: str, *, enabled: bool | None = None) -> None:
        self.db_url = db_url
        env = _assessment_environment()
        self.enabled = (
            env.get("ADVTEST_ENABLE_LIVE_TESTS", "").lower() in {"1", "true", "yes"}
            if enabled is None
            else enabled
        )
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._status = "idle"
        self._exit_code: int | None = None
        self._lines: deque[str] = deque(maxlen=300)
        self._options: dict[str, Any] = {}

    def _build_argv(self, opts: dict[str, Any]) -> list[str]:
        target = opts.get("target", "workspace")
        if target not in _ALLOWED_TARGETS:
            raise ValueError(f"target must be one of {_ALLOWED_TARGETS}")
        samples = str(opts.get("samples") or "tests/data/strategy_samples.jsonl")
        if not (_REPO_ROOT / samples).is_file():
            raise ValueError(f"samples file not found: {samples}")
        n_max = max(1, min(int(opts.get("n_max", 10)), 25))
        replay = max(0, min(int(opts.get("replay", 0)), 10))
        argv = [
            "poetry", "run", "python", "scripts/run_strategy_loop.py",
            "--samples", samples,
            "--target", target,
            "--n-max", str(n_max),
            "--threat-model", "black_box",
            "--persist",
        ]
        if opts.get("route"):
            argv += ["--route", "--library-dir", str(opts.get("library_dir") or _DEFAULT_LIBRARY)]
        if replay:
            argv += ["--replay", str(replay)]
        if target == "agentdojo":
            argv += ["--target-model", str(opts.get("target_model") or "gpt-4o-mini")]
        if target == "workspace":
            argv += ["--judge-role", "CLAUDE_JUDGE"]
            if opts.get("trigger_prompt"):
                argv += ["--trigger-prompt", str(opts["trigger_prompt"])]
        return argv

    def start(self, opts: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            raise PermissionError("strategy launching is disabled")
        argv = self._build_argv(opts)
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuntimeError("a strategy run is already active")
            env = _assessment_environment()
            env["ADVTEST_DB_URL"] = self.db_url
            env["PYTHONUNBUFFERED"] = "1"
            self._lines.clear()
            self._options = dict(opts)
            self._status = "running"
            self._exit_code = None
            self._process = subprocess.Popen(
                argv, cwd=_REPO_ROOT, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True,
            )
            threading.Thread(target=self._collect, args=(self._process,), daemon=True).start()
        return self.snapshot()

    def _collect(self, process: subprocess.Popen[str]) -> None:
        if process.stdout is not None:
            for line in process.stdout:
                with self._lock:
                    self._lines.append(line.rstrip("\n"))
        code = process.wait()
        with self._lock:
            self._exit_code = code
            self._status = "finished" if code == 0 else "failed"

    def stop(self) -> dict[str, Any]:
        import os
        import signal

        with self._lock:
            process = self._process
            if process is None or process.poll() is not None:
                raise RuntimeError("no strategy run is active")
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
                "status": self._status,
                "exit_code": self._exit_code,
                "options": dict(self._options),
                "lines": list(self._lines),
            }


def _run_view(session: Session, run_id: str) -> dict[str, Any]:
    run = session.get(StrategyRun, run_id)
    if run is None:
        raise HTTPException(404, f"unknown strategy run {run_id!r}")
    attempts = list(
        session.scalars(
            select(StrategyAttempt)
            .where(StrategyAttempt.run_id == run_id)
            .order_by(StrategyAttempt.sample_key, StrategyAttempt.iter)
        )
    )
    grouped: dict[tuple[str, str], list[StrategyAttempt]] = defaultdict(list)
    for a in attempts:
        grouped[(a.lineage_id, a.sample_key)].append(a)
    replays = {
        r.sample_key: r
        for r in session.scalars(
            select(StrategyReplay).where(StrategyReplay.run_id == run_id)
        )
    }
    cases = [
        {
            "lineage_id": lineage,
            "sample_key": sample_key,
            "status": rows[0].status,
            "attempts": rows,
            "replay": replays.get(sample_key),
        }
        for (lineage, sample_key), rows in sorted(grouped.items())
    ]
    return {"run": run, "cases": cases}


def build_strategy_router(
    templates: Jinja2Templates,
    get_session: Callable[..., Any],
    controller: StrategyController,
) -> APIRouter:
    """Build the ``/strategy`` router closing over templates + DB dep + controller."""
    router = APIRouter(prefix="/strategy", tags=["strategy"])

    @router.get("", response_class=HTMLResponse)
    def index(request: Request, session: Session = Depends(get_session)) -> Response:  # noqa: B008
        runs = list(
            session.scalars(select(StrategyRun).order_by(StrategyRun.started_at.desc()).limit(50))
        )
        return templates.TemplateResponse(
            request,
            "strategy/index.html",
            {
                "runs": runs,
                "status": controller.snapshot(),
                "targets": _ALLOWED_TARGETS,
                "default_library": _DEFAULT_LIBRARY,
            },
        )

    @router.get("/status")
    def status() -> JSONResponse:
        return JSONResponse(controller.snapshot())

    @router.post("/start")
    def start(submission: StrategyStart) -> JSONResponse:
        try:
            state = controller.start(submission.model_dump())
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse(state, status_code=202)

    @router.post("/stop")
    def stop() -> JSONResponse:
        try:
            return JSONResponse(controller.stop())
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{run_id}", response_class=HTMLResponse)
    def detail(
        run_id: str, request: Request, session: Session = Depends(get_session)  # noqa: B008
    ) -> Response:
        return templates.TemplateResponse(
            request, "strategy/run.html", {"run_id": run_id, **_run_view(session, run_id)}
        )

    return router
