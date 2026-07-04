"""FastAPI + HTMX dashboard for advtest runs.

Pages:

* ``/`` and ``/runs`` — list runs with summary stats.
* ``/runs/{run_id}`` — single run view, headlined by the
  technique-by-payload-family ASR heatmap (the demo's visual hook).
* ``/runs/{run_id}/tree`` — per-(behaviour, technique) attack tree,
  reconstructed from ``Trace.parent_id`` self-joins. TAP runs draw
  as branching SVG trees; nodes are clickable, PAIR / single-step
  degrade to flat lists.
* ``/traces/{trace_id}`` — single-trace detail; reachable by clicking
  any node on the attack-tree page.
* ``/findings`` — flagged traces (severity-filterable via HTMX).
* ``/findings/{id}`` — full trace + replay-bundle download link.
* ``/attacks`` — registered AttackPlugins with citations.
* ``/adapters`` — registered DocumentTargets with capabilities.
* ``/hitl`` — judge-disagreement queue with resolution form.
* ``/references`` — references.bib rendered.

PDF export of /runs/{id} is handled by :mod:`report`; the route
returns 501 with a friendly install hint when ``weasyprint`` isn't
on the path (it's an optional extra because of cairo/pango native
deps).

The app is constructed via :func:`create_app` so tests can pass a
custom DB URL + output_dir, and the production CLI / uvicorn entry
points share the same factory.
"""

from __future__ import annotations

import hmac
import os
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text
from sqlalchemy.orm import Session

from advtest_ext.dashboard.registry import list_adapters, list_attacks
from advtest_ext.dashboard.tree import (
    build_phase_forests as _build_phase_forests,
)
from advtest_ext.dashboard.tree import (
    collapse_conversations as _collapse_conversations,
)
from advtest_ext.storage import (
    asr_heatmap_formatted,
    get_finding,
    get_recipe,
    get_run,
    get_seed,
    get_trace,
    list_finding_behavior_ids,
    list_finding_run_ids,
    list_findings,
    list_hitl,
    list_recipes,
    list_runs,
    list_seeds,
    list_signals_for_trace,
    list_traces_for_run,
    make_engine,
    make_session_factory,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.engine import Engine


_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"


# Tree-forest assembly is imported at the top of the module from
# :mod:`advtest_ext.dashboard.tree` so the PDF report module can
# share the same builder without a circular import via app → report → app.
# The heatmap formatter lives in :func:`advtest_ext.storage.repository.
# asr_heatmap_formatted` so the dashboard and the PDF report share one
# implementation and can't drift on family-bucketing or aggregation.


def _tree_state_hash(phases: list[dict[str, Any]], *, finished: bool) -> str:
    """A short fingerprint of everything the live attack-tree renders:
    every node's id, verdict, depth, parent and score, plus whether the
    run has finished. The HTMX poll sends back the hash it last rendered;
    when it still matches, the ``/_tree`` endpoint returns ``204 No
    Content`` and HTMX skips the swap — so the Mermaid trees are only
    re-rendered when something actually changed, instead of every 2 s
    (which caused the live-tree flicker)."""
    import hashlib

    parts: list[str] = ["fin" if finished else "live"]
    for phase in phases:
        parts.append(f"P{phase.get('index')}:{phase.get('n_success')}/{phase.get('n_traces')}")
        for cell in phase.get("forest", []):
            for line in cell.get("lines", []):
                t = line["trace"]
                score = t.judge_scores[0].score if getattr(t, "judge_scores", None) else None
                parts.append(
                    f"{getattr(t, 'id', '')}|{getattr(t, 'verdict', '')}|"
                    f"{getattr(t, 'depth', '')}|{getattr(t, 'parent_id', '')}|{score}"
                )
    # Sort so the fingerprint is independent of trace/query ordering — an
    # unstable order would change the hash every poll and defeat the
    # 204-skip (each part is self-identifying via its trace id).
    parts.sort()
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]


def create_app(
    *,
    db_url: str | None = None,
    output_dir: Path | None = None,
    engine: Engine | None = None,
    auth_token: str | None = None,
) -> FastAPI:
    """Construct the FastAPI dashboard app.

    Tests pass an in-memory SQLite engine; the CLI / production
    entrypoints pass ``db_url`` and let the factory build a
    file-backed one. ``output_dir`` is the run-artefact root used
    when a Finding's replay-bundle path is relative to a known root.

    ``auth_token`` (or env ``ADVTEST_DASHBOARD_TOKEN``) gates every route
    except the probes behind a ``Authorization: Bearer <token>`` header. Unset
    (dev/CI default) → no auth. For production, prefer fronting this with an
    identity-aware proxy (GCP IAP / Cloud Run auth); the token is a simple
    built-in fallback so the dashboard is never anonymously exposed."""
    app = FastAPI(
        title="advtest dashboard",
        description="Browse runs, findings, HITL queue, and references.",
    )
    if engine is None:
        engine = make_engine(db_url)
    factory = make_session_factory(engine)

    token = auth_token if auth_token is not None else os.environ.get("ADVTEST_DASHBOARD_TOKEN")
    if token:

        @app.middleware("http")
        async def _require_token(request: Request, call_next: Any) -> Any:
            # Probes + static assets stay open — the orchestrator / load
            # balancer hit /healthz and /readyz unauthenticated.
            path = request.url.path
            if path in ("/healthz", "/readyz") or path.startswith("/static"):
                return await call_next(request)
            provided = request.headers.get("Authorization", "")
            if not hmac.compare_digest(provided, f"Bearer {token}"):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
            return await call_next(request)

    templates = Jinja2Templates(directory=_TEMPLATE_DIR)

    if _STATIC_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=_STATIC_DIR),
            name="static",
        )

    def get_session() -> Iterator[Session]:
        # Generator-style FastAPI dependency: yield the session and
        # then close it in the finally so the connection lands back
        # in the pool the moment the request returns. The previous
        # version returned the session directly and relied on GC,
        # which exhausts the pool in seconds when 5+ HTMX partials
        # poll every 2-3 s.
        session = factory()
        try:
            yield session
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        """Liveness probe: the process is up and serving. No dependencies —
        a failing /healthz means the container should be restarted."""
        return {"status": "ok"}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness probe: can we actually serve traffic? Checks database
        connectivity so a load balancer / k8s readiness gate holds traffic
        (503) while the DB is unreachable, instead of serving errors."""
        try:
            with factory() as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:  # pragma: no cover - exercised via a broken engine
            return JSONResponse(
                {"status": "not_ready", "db": f"error: {type(exc).__name__}"},
                status_code=503,
            )
        return JSONResponse({"status": "ready", "db": "ok"})

    @app.get("/", response_class=HTMLResponse)
    @app.get("/runs", response_class=HTMLResponse)
    async def runs_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        runs = list_runs(session)
        return templates.TemplateResponse(request, "runs/index.html", {"runs": runs})

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def runs_show(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id)
        return templates.TemplateResponse(request, "runs/show.html", ctx)

    # --- HTMX polling endpoints for the live-run dashboard. -----------
    # Each returns ONE section's partial HTML; the partial knows
    # whether to opt back into polling based on ``run.finished_at``.
    # No new auth / no new state — same context the full page builds,
    # just sliced. Polling intervals live in the partial templates.
    @app.get("/runs/{run_id}/_hero", response_class=HTMLResponse)
    async def runs_hero_partial(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id)
        return templates.TemplateResponse(request, "runs/_hero.html", ctx)

    @app.get("/runs/{run_id}/_findings", response_class=HTMLResponse)
    async def runs_findings_partial(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id)
        return templates.TemplateResponse(request, "runs/_findings.html", ctx)

    @app.get("/runs/{run_id}/_techniques", response_class=HTMLResponse)
    async def runs_techniques_partial(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id)
        return templates.TemplateResponse(request, "runs/_techniques.html", ctx)

    @app.get("/runs/{run_id}/_heatmap", response_class=HTMLResponse)
    async def runs_heatmap_partial(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id)
        return templates.TemplateResponse(request, "runs/_heatmap_wrap.html", ctx)

    @app.get("/runs/{run_id}/_timeline", response_class=HTMLResponse)
    async def runs_timeline_partial(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id)
        return templates.TemplateResponse(request, "runs/_timeline.html", ctx)

    def _build_run_context(session: Session, run_id: str) -> dict[str, Any]:
        """Shared context builder for the full /runs/{id} page and the
        five HTMX polling partials below. Single source of truth so
        the partial's slice of the dashboard always matches what the
        full page would render."""
        from advtest_ext.dashboard.intercepted_extract import (
            extract_full_response,
        )

        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        # Per-trace assembled response from intercepted Gemini traffic.
        # Empty string when no capture exists (api_mode trace, interception
        # disabled, or pre-Phase-2 trace). Templates fall back to
        # ``trace.raw_response`` in that case.
        full_responses: dict[str, str] = {}
        for t in traces:
            raw = t.raw_response_data or {}
            path = raw.get("intercepted_traffic_path")
            if path:
                full_responses[t.id] = extract_full_response(path)
        heatmap = asr_heatmap_formatted(session, run_id)
        findings = list_findings(session, run_id=run_id)

        # Top findings for the demo-grade cards section: sort by the
        # primary judge score (StrongREJECT lands on judge_scores[0])
        # and surface the highest-scoring 6. Falls back to the
        # promotion order when no judge score exists. Findings whose
        # trace was deleted (shouldn't happen, but FK could be loose
        # in legacy DBs) get filtered.
        def _score_of(finding: Any) -> float:
            t = getattr(finding, "trace", None)
            if t is None or not getattr(t, "judge_scores", None):
                return 0.0
            return float(t.judge_scores[0].score)

        top_findings = sorted(
            (f for f in findings if getattr(f, "trace", None) is not None),
            key=_score_of,
            reverse=True,
        )[:6]
        # Per-technique aggregate for the bar-chart strip: across all
        # behaviours in the run, what's the mean ASR per hiding
        # technique? Rolls up the heatmap rows. The heatmap dict is
        # untyped (``dict[str, object]``) so we narrow at the boundary.
        technique_chart: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = heatmap["rows"]  # type: ignore[assignment]
        for row in rows:
            attempts = sum(int(c["attempts"]) for c in row["cells"])
            successes = sum(int(c["successes"]) for c in row["cells"])
            asr = (successes / attempts) if attempts else 0.0
            technique_chart.append(
                {
                    "technique": row["technique"],
                    "asr": asr,
                    "attempts": attempts,
                    "successes": successes,
                }
            )
        technique_chart.sort(key=lambda r: r["asr"], reverse=True)
        # Per-phase attack-tree forests for the embedded live-tree include
        # (runs/_tree_live.html). Same builder the full-screen /tree route
        # uses, so the embedded tree matches it — without this the include
        # gets no ``phases`` and renders nothing on the run page.
        phases = _build_phase_forests(_collapse_conversations(list(traces)))
        return {
            "run": run,
            "traces": traces,
            "heatmap": heatmap,
            "findings": findings,
            "top_findings": top_findings,
            "technique_chart": technique_chart,
            "full_responses": full_responses,
            "phases": phases,
            "tree_hash": _tree_state_hash(phases, finished=bool(run.finished_at)),
        }

    @app.get("/runs/{run_id}/tree", response_class=HTMLResponse)
    async def runs_tree(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Per-(behaviour, technique) attack-tree view. TAP runs draw
        as the multi-level branching trees the algorithm actually
        explored; PAIR / single-step runs degenerate to a flat list
        of one root per cell, which the template handles with the
        same recursive macro."""
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        phases = _build_phase_forests(_collapse_conversations(list(traces)))
        tree_hash = _tree_state_hash(phases, finished=bool(run.finished_at))
        return templates.TemplateResponse(
            request,
            "runs/tree.html",
            {"run": run, "phases": phases, "tree_hash": tree_hash},
        )

    @app.get("/runs/{run_id}/_tree", response_class=HTMLResponse)
    async def runs_tree_partial(
        request: Request,
        run_id: str,
        v: str = "",
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """HTMX polling endpoint for the live Mermaid attack-tree view.
        Returns just the tree partial so the page swaps it in place as the
        run progresses. ``v`` is the state hash the client last rendered;
        when the tree is unchanged we return ``204 No Content`` so HTMX
        skips the swap and the Mermaid trees don't re-render (and flicker)
        every poll. See runs/_tree_live.html."""
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        phases = _build_phase_forests(_collapse_conversations(list(traces)))
        tree_hash = _tree_state_hash(phases, finished=bool(run.finished_at))
        if v and v == tree_hash:
            return Response(status_code=204)
        return templates.TemplateResponse(
            request,
            "runs/_tree_live.html",
            {"run": run, "phases": phases, "tree_hash": tree_hash},
        )

    @app.get("/runs/{run_id}/transcript", response_class=HTMLResponse)
    async def runs_transcript(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Multi-turn transcript view: per-turn traces (crescendo /
        tap_chain) reconstructed into readable conversations — each turn
        shows the prompt, the FULL intercepted Gemini reply, and the
        score. tap_chain renders one conversation per replay round with
        the diagnoser's reason."""
        from advtest_ext.dashboard.intercepted_extract import extract_full_response
        from advtest_ext.dashboard.transcript import build_transcript_groups

        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list(list_traces_for_run(session, run_id))
        full_responses: dict[str, str] = {}
        for t in traces:
            raw = t.raw_response_data or {}
            path = raw.get("intercepted_traffic_path")
            if path:
                full_responses[t.id] = extract_full_response(path)
        groups = build_transcript_groups(traces, full_responses)
        return templates.TemplateResponse(
            request,
            "runs/transcript.html",
            {"run": run, "groups": groups},
        )

    @app.get("/runs/{run_id}/report", response_class=HTMLResponse)
    async def runs_report_html(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        from advtest_ext.dashboard.report import build_report_context

        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        ctx = build_report_context(session=session, run=run)
        return templates.TemplateResponse(request, "report.html", ctx)

    @app.get("/runs/{run_id}/report.pdf")
    async def runs_report_pdf(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        from advtest_ext.dashboard.report import (
            WEASYPRINT_INSTALLED,
            build_report_context,
            render_pdf,
        )

        if not WEASYPRINT_INSTALLED:
            return PlainTextResponse(
                "PDF rendering requires the optional 'pdf' extra:\n"
                "    poetry install --extras pdf\n"
                "or, for raw pip:\n"
                "    pip install weasyprint\n"
                "The HTML version of this report is available at "
                f"/runs/{run_id}/report.",
                status_code=501,
            )
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        ctx = build_report_context(session=session, run=run)
        html = templates.get_template("report.html").render(request=request, **ctx)
        pdf_bytes = render_pdf(html, base_url=str(_TEMPLATE_DIR))
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": (f"inline; filename=advtest-{run_id}.pdf")},
        )

    @app.get("/runs/{run_id}/transcript-report.pdf")
    async def runs_transcript_report_pdf(
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Professional, paginated PDF of the run's multi-turn transcripts,
        built programmatically (reportlab): one section per conversation
        (tree node), each turn's prompt + Gemini reply verbatim. Intended to
        hand to a vendor security team. 501s with an install hint when
        reportlab isn't present."""
        from advtest_ext.dashboard.intercepted_extract import extract_full_response
        from advtest_ext.dashboard.transcript_report import (
            REPORTLAB_INSTALLED,
            build_transcript_pdf,
        )

        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        if not REPORTLAB_INSTALLED:
            return PlainTextResponse(
                "The transcript PDF report requires reportlab:\n    pip install reportlab\n",
                status_code=501,
            )
        traces = list(list_traces_for_run(session, run_id))
        # Intercepted-stream fallback for turns whose rendered capture is empty.
        full_responses: dict[str, str] = {}
        for t in traces:
            raw = t.raw_response_data or {}
            path = raw.get("intercepted_traffic_path")
            if path:
                full_responses[t.id] = extract_full_response(path)
        pdf_bytes = build_transcript_pdf(run=run, traces=traces, full_responses=full_responses)
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": (f"inline; filename=advtest-transcripts-{run_id}.pdf")},
        )

    @app.get("/traces/{trace_id}", response_class=HTMLResponse)
    async def traces_show(
        request: Request,
        trace_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Single-trace detail page. Reachable from the attack-tree
        view by clicking any node — every TAP / PAIR branch becomes
        a clickable link, including non-promoted (refused / partial)
        ones that ``/findings/{id}`` can't surface."""
        trace = get_trace(session, trace_id)
        if trace is None:
            raise HTTPException(404, f"trace {trace_id!r} not found")
        # If the trace was promoted to a finding, stash the finding
        # so the template can show the badge + replay-bundle link.
        finding = trace.finding if hasattr(trace, "finding") else None
        # Phase 2 lands an intercepted-traffic JSONL per trace; the
        # extractor pulls the full assembled response from the
        # captured streamGenerate bodies. This is the ground-truth
        # Gemini output — never truncated by Playwright's read-while-
        # streaming race, unlike ``trace.raw_response``. Empty
        # string when no capture (api_mode trace, interception
        # disabled, or pre-Phase-2 trace).
        from advtest_ext.dashboard.intercepted_extract import (
            extract_full_response,
        )

        raw = trace.raw_response_data or {}
        intercepted_path = raw.get("intercepted_traffic_path")
        full_response = extract_full_response(intercepted_path) if intercepted_path else ""
        # Phase 3 — load any SignalRecord rows attached to this trace
        # so the template can render the typed-signal panel alongside
        # the existing judge-scores table.
        signals = list_signals_for_trace(session, trace_id)
        return templates.TemplateResponse(
            request,
            "traces/show.html",
            {
                "trace": trace,
                "finding": finding,
                "full_response": full_response,
                "signals": signals,
            },
        )

    @app.get("/traces/{trace_id}/intercepted")
    async def traces_intercepted(
        trace_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Serve the per-trace Workspace Gemini traffic capture
        (JSONL). 404s cleanly when the trace doesn't exist OR the
        trace ran with interception disabled OR the capture file
        was removed from disk.

        Path is sourced from ``trace.raw_response_data['intercepted_traffic_path']``
        — set by the ui_mode adapters when ``ADVTEST_INTERCEPT_TRAFFIC``
        is on. We deliberately don't store it as a top-level Trace
        column to keep the storage schema additive-only for now;
        if dashboard filters by capture status become hot, hoist it
        in a later migration.
        """
        trace = get_trace(session, trace_id)
        if trace is None:
            raise HTTPException(404, f"trace {trace_id!r} not found")
        raw = trace.raw_response_data or {}
        capture_path = raw.get("intercepted_traffic_path")
        if not capture_path:
            raise HTTPException(404, "no intercepted traffic captured for this trace")
        path_obj = Path(capture_path)
        if not path_obj.exists():
            raise HTTPException(404, f"capture file missing on disk: {capture_path}")
        return FileResponse(
            path=str(path_obj),
            media_type="application/x-ndjson",
            filename=f"intercepted-{trace_id}.jsonl",
        )

    @app.get("/findings", response_class=HTMLResponse)
    async def findings_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        severity: str | None = None,
        run_id: str | None = None,
        behavior_id: str | None = None,
    ) -> Response:
        # Normalise empty-string filter values from HTMX form submits
        # back to ``None`` so they don't degenerate into ``WHERE col =
        # ''`` clauses.
        severity = severity or None
        run_id = run_id or None
        behavior_id = behavior_id or None
        findings = list_findings(
            session,
            severity=severity,
            run_id=run_id,
            behavior_id=behavior_id,
        )
        run_options = list_finding_run_ids(session)
        # Behavior dropdown is scoped to the selected run when one is
        # picked, so "within the run by behaviour" only offers
        # behaviours that actually have findings in that run.
        behavior_options = list_finding_behavior_ids(session, run_id=run_id)
        # If the request looks like an HTMX partial, return the table
        # only — keeps the filter swap snappy.
        partial = request.headers.get("hx-request") == "true"
        template = "findings/_table.html" if partial else "findings/index.html"
        return templates.TemplateResponse(
            request,
            template,
            {
                "findings": findings,
                "severity": severity,
                "run_id": run_id,
                "behavior_id": behavior_id,
                "run_options": run_options,
                "behavior_options": behavior_options,
            },
        )

    @app.get("/findings/{finding_id}", response_class=HTMLResponse)
    async def findings_show(
        request: Request,
        finding_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        from advtest_ext.dashboard.intercepted_extract import (
            extract_full_response,
        )

        finding = get_finding(session, finding_id)
        if finding is None:
            raise HTTPException(404, f"finding {finding_id!r} not found")
        trace = finding.trace
        raw = (trace.raw_response_data or {}) if trace else {}
        intercepted_path = raw.get("intercepted_traffic_path")
        full_response = extract_full_response(intercepted_path) if intercepted_path else ""
        return templates.TemplateResponse(
            request,
            "findings/show.html",
            {"finding": finding, "trace": trace, "full_response": full_response},
        )

    @app.get("/findings/{finding_id}/replay")
    async def findings_replay(
        finding_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        finding = get_finding(session, finding_id)
        if finding is None:
            raise HTTPException(404, f"finding {finding_id!r} not found")
        if not finding.replay_bundle_path:
            raise HTTPException(404, "this finding has no replay bundle on disk")
        path = Path(finding.replay_bundle_path)
        if not path.is_absolute() and output_dir is not None:
            path = output_dir / path
        if not path.exists():
            raise HTTPException(404, f"replay bundle not found at {path}")
        return FileResponse(
            path,
            media_type="application/zip",
            filename=path.name,
        )

    @app.get("/findings/{finding_id}/report.docx")
    async def findings_report_docx(
        finding_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Phase-7 — download the Finding's report as a .docx in the
        same OVERVIEW / METHODOLOGY / FINDINGS / RISKS / PRIMARY
        RECOMMENDATION / POC shape as the human-authored
        red-team reports the platform ingests (Google Red Team
        Report shape).

        The Recipe row (if the Finding came from a Recipe Run)
        and the Signal rows are loaded alongside the trace so the
        report's methodology + findings tables can reference
        them. When neither is available the report falls back to
        the legacy attack-name / technique / judge-scores shape.
        """
        from advtest_ext.core.seed_behaviors import load_seed_behaviors
        from advtest_ext.dashboard.report_docx import (
            ReportInputs,
            build_finding_report,
        )

        finding = get_finding(session, finding_id)
        if finding is None:
            raise HTTPException(404, f"finding {finding_id!r} not found")
        trace = finding.trace

        # Recipe lookup: the schema doesn't tie a Trace to a
        # Recipe today (Phase 5 leaves Recipe Runs as their own
        # path). When that wiring lands the lookup goes here.
        recipe_row = None

        signals = list_signals_for_trace(session, trace.id)

        # Behaviour description + success_criteria from the seed
        # catalogue. Looked up by id; falls back to empty
        # strings when the behaviour was added after this
        # finding was promoted (catalogue drift).
        behavior_lookup = {b.id: b for b in load_seed_behaviors()}
        behavior = behavior_lookup.get(trace.behavior_id)
        inputs = ReportInputs(
            finding=finding,
            trace=trace,
            recipe=recipe_row,
            behavior_description=(behavior.description if behavior is not None else ""),
            behavior_success_criteria=(behavior.success_criteria if behavior is not None else ""),
            signals=signals,
        )
        docx_bytes = build_finding_report(inputs)
        filename = f"finding-{finding_id}.docx"
        return Response(
            content=docx_bytes,
            media_type=("application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @app.get("/attacks", response_class=HTMLResponse)
    async def attacks_index(request: Request) -> Response:
        return templates.TemplateResponse(request, "attacks.html", {"attacks": list_attacks()})

    @app.get("/adapters", response_class=HTMLResponse)
    async def adapters_index(request: Request) -> Response:
        return templates.TemplateResponse(request, "adapters.html", {"adapters": list_adapters()})

    @app.get("/hitl", response_class=HTMLResponse)
    async def hitl_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        status: str | None = "pending",
    ) -> Response:
        items = list_hitl(session, status=status)
        return templates.TemplateResponse(
            request,
            "hitl.html",
            {"items": items, "status": status},
        )

    # ------------------------------------------------------------------
    # Phase-1 (Fission integration) — Seeds + Recipes read-only.
    # No ingest / edit routes yet; those land with Phase 6 (Extractor
    # + operator-review queue). For now the dashboard just exposes
    # whatever the storage layer holds so the operator can confirm
    # the tables are populating.
    # ------------------------------------------------------------------

    @app.get("/seeds", response_class=HTMLResponse)
    async def seeds_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        kind: str | None = None,
    ) -> Response:
        """Browse ingested Seeds — both human-chat and dataset-entry
        kinds. Optional ``kind`` filter for the per-kind tabs."""
        kind = kind or None
        items = list_seeds(session, kind=kind)
        return templates.TemplateResponse(
            request,
            "seeds/index.html",
            {"seeds": items, "kind": kind},
        )

    @app.get("/seeds/{seed_id}", response_class=HTMLResponse)
    async def seeds_show(
        request: Request,
        seed_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        seed = get_seed(session, seed_id)
        if seed is None:
            raise HTTPException(404, f"seed {seed_id!r} not found")
        recipes = list_recipes(session, seed_id=seed_id)
        return templates.TemplateResponse(
            request,
            "seeds/show.html",
            {"seed": seed, "recipes": recipes},
        )

    @app.get("/recipes", response_class=HTMLResponse)
    async def recipes_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        draft_state: str | None = None,
    ) -> Response:
        """Browse the Recipe catalogue. Optional ``draft_state``
        filter (``draft`` / ``approved`` / ``rejected``) for the
        operator-review queue surface — Phase 6 will populate the
        ``draft`` bucket from Extractor output."""
        draft_state = draft_state or None
        items = list_recipes(session, draft_state=draft_state)
        return templates.TemplateResponse(
            request,
            "recipes/index.html",
            {"recipes": items, "draft_state": draft_state},
        )

    @app.get("/recipes/{recipe_id}", response_class=HTMLResponse)
    async def recipes_show(
        request: Request,
        recipe_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        recipe = get_recipe(session, recipe_id)
        if recipe is None:
            raise HTTPException(404, f"recipe {recipe_id!r} not found")
        return templates.TemplateResponse(
            request,
            "recipes/show.html",
            {"recipe": recipe},
        )

    @app.get("/references", response_class=HTMLResponse)
    async def references_index(request: Request) -> Response:
        from advtest_ext.dashboard.references import parse_bibfile

        return templates.TemplateResponse(request, "references.html", {"entries": parse_bibfile()})

    return app


__all__ = ["create_app"]
