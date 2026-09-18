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
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from advtest_ext.dashboard.live_tests import LiveTestController
from advtest_ext.dashboard.presentation import (
    attack_label,
    finding_method,
    finding_severity,
    finding_title,
    objective_label,
    run_display_name,
    run_method_label,
    run_surface_key,
    run_surface_label,
    severity_rank,
    surface_label,
)
from advtest_ext.dashboard.run_actions import (
    DeleteRun,
    RenameRun,
    delete_run_results,
    require_same_origin,
)
from advtest_ext.dashboard.services import (
    COLLECTION_PATHS,
    SERVICE_LABELS,
    group_surfaces,
    scope_campaign_reads,
    service_catalog,
    target_identity,
)
from advtest_ext.dashboard.transcript import build_transcript_groups
from advtest_ext.dashboard.tree import (
    build_phase_forests as _build_phase_forests,
)
from advtest_ext.dashboard.tree import (
    collapse_conversations as _collapse_conversations,
)
from advtest_ext.dashboard.tree import (
    tree_graph_payload as _tree_graph_payload,
)
from advtest_ext.storage import (
    asr_heatmap_formatted,
    count_gold_labels,
    count_hitl,
    finding_run_seq_map,
    get_finding,
    get_hitl,
    get_run,
    get_seed,
    get_trace,
    insert_gold_label,
    list_finding_behavior_ids,
    list_finding_run_ids,
    list_findings,
    list_hitl,
    list_runs,
    list_seeds,
    list_signals_for_trace,
    list_traces_for_run,
    make_engine,
    make_session_factory,
    next_pending_hitl,
    resolve_hitl,
    run_partial_count,
)
from advtest_ext.storage.repository import run_is_active, run_state

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


def _full_responses_for_traces(traces: list[Any]) -> dict[str, str]:
    """Return the best response text for each trace.

    Legacy multi-turn runs reused capture filenames after every conversation
    reset, so later nodes overwrote earlier JSONL files.  When a capture path
    is shared, the persisted raw_response is the only response still tied to
    the correct trace.  New runs have unique paths and retain the richer
    intercepted stream.
    """
    from collections import Counter

    from advtest_ext.dashboard.intercepted_extract import extract_full_response

    rows = list(traces)
    paths = [
        str(path)
        for trace in rows
        if (path := (trace.raw_response_data or {}).get("intercepted_traffic_path"))
    ]
    counts = Counter(paths)
    responses: dict[str, str] = {}
    for trace in rows:
        path = (trace.raw_response_data or {}).get("intercepted_traffic_path")
        captured = extract_full_response(path) if path and counts[str(path)] == 1 else ""
        responses[trace.id] = captured or trace.raw_response or ""
    return responses


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
                    f"{getattr(t, 'depth', '')}|{getattr(t, 'parent_id', '')}|{score}|"
                    f"{getattr(t, 'timestamp', '')}|{getattr(t, 'ended_at', '')}|"
                    f"{getattr(t, 'turn_count', 1)}|{getattr(t, 'attack_name', '')}|"
                    f"{getattr(t, 'raw_request', '')}|{getattr(t, 'extra_metadata', '')}"
                )
    # Sort so the fingerprint is independent of trace/query ordering — an
    # unstable order would change the hash every poll and defeat the
    # 204-skip (each part is self-identifying via its trace id).
    parts.sort()
    return hashlib.sha1("\n".join(parts).encode()).hexdigest()[:16]


# Verdicts an expert may assign in review. ``error`` is oracle-only
# (a crashed attempt) and never a human label, so it's excluded here.
_REVIEW_VERDICTS = ("success", "partial", "refused")


class ReviewSubmission(BaseModel):
    """Body of a ``POST /hitl/{id}/review`` — one expert label."""

    verdict: str
    confidence: int = Field(default=3, ge=1, le=5)
    reason: str = ""
    reviewer_id: str = ""
    review_seconds: int = Field(default=0, ge=0)


class LiveTestStart(BaseModel):
    """Explicit acknowledgement for a workflow that drives live accounts."""

    confirmed: bool = False
    playbook: str | None = None
    branching_factor: int | None = Field(default=None, ge=1, le=5)
    max_depth: int | None = Field(default=None, ge=1, le=12)
    max_nodes_per_seed: int | None = Field(default=None, ge=1, le=400)


def _hitl_blind_pct() -> int:
    """Fraction of reviews shown *blind* (oracle verdict hidden) to guard
    against anchoring bias — the reviewer judges the evidence, not the
    machine's guess. Env ``ADVTEST_HITL_BLIND_PCT`` (default 20)."""
    try:
        return max(0, min(100, int(os.environ.get("ADVTEST_HITL_BLIND_PCT", "20"))))
    except ValueError:
        return 20


def _is_blind_review(item_id: str) -> bool:
    """Deterministic per queue item (so a page refresh can't reroll it and
    a tamperer can't opt out): hash the id into a 0-99 bucket."""
    import hashlib

    pct = _hitl_blind_pct()
    if pct <= 0:
        return False
    bucket = int(hashlib.sha1(item_id.encode()).hexdigest(), 16) % 100
    return bucket < pct


def _oracle_snapshot(session: Session, trace: Any) -> dict[str, Any]:
    """Freeze the oracle's prediction for a trace at review time — the
    verdict, top judge score, and which signals fired — so the resulting
    GoldLabel scores this run with the predictor that actually labelled
    it (the flywheel mutates the oracle between runs)."""
    top_score = max((js.score for js in trace.judge_scores), default=0.0)
    signals = list_signals_for_trace(session, trace.id)
    layers: dict[str, Any] = {s.name: bool(s.fired) for s in signals}
    layers["judge_score"] = round(top_score, 4)
    return {
        "verdict": trace.verdict or "refused",
        "score": top_score,
        "layers": layers,
    }


def _overview_run_diff(session: Session, latest_id: str, prev_id: str) -> dict[str, int]:
    """NEW / FIXED / PERSISTING vulnerable (behaviour, technique) cells
    between the two most recent runs — the "re-test on every release"
    story. A cell is vulnerable if any trace in that run succeeded on it.
    NEW = vulnerable now, not before; FIXED = was, no longer; PERSISTING =
    both. Honest set arithmetic, no fabricated 'regressed' bucket."""

    def cells(run_id: str) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for t in list_traces_for_run(session, run_id, limit=100_000):
            if t.verdict == "success":
                out.add((t.behavior_id, t.technique_id or "-"))
        return out

    now, before = cells(latest_id), cells(prev_id)
    return {
        "new": len(now - before),
        "fixed": len(before - now),
        "persisting": len(now & before),
    }


def build_overview_context(session: Session) -> dict[str, Any]:
    """Assemble the command-center landing page from real data: cross-run
    aggregates, the latest run's calibrated ASR (PPI), the review-queue
    state, a run-over-run diff, and the latest run's technique heatmap."""
    from advtest_ext.core.ppi import ppi_asr_for_run
    from advtest_ext.dashboard.overview import build_overview

    stats = build_overview(session)
    latest = stats.recent_runs[0] if stats.recent_runs else None
    prev = stats.recent_runs[1] if len(stats.recent_runs) > 1 else None

    ppi = ppi_asr_for_run(session, latest.id) if latest is not None else None
    diff = (
        _overview_run_diff(session, latest.id, prev.id)
        if latest is not None and prev is not None
        else None
    )
    heatmap = (
        asr_heatmap_formatted(session, latest.id)
        if latest is not None
        else {"is_empty": True, "rows": [], "families": [], "techniques": []}
    )

    pending = list_hitl(session, status="pending", limit=500)
    queue = {
        "pending": len(pending),
        "gold": count_gold_labels(session),
        "uniform": sum(1 for i in pending if i.sample_reason == "uniform"),
        "disagreement": sum(1 for i in pending if i.sample_reason == "disagreement"),
        "top": pending[:3],
    }
    top_findings = list_findings(session, run_id=latest.id, limit=5) if latest is not None else []
    n_partial = run_partial_count(session, latest.id) if latest is not None else 0

    # Per-run ASR trend, most-recent last for the sparkline. build_overview
    # already ordered it oldest→newest.
    trend = stats.asr_trend[-12:]
    # Pre-compute the SVG polyline geometry (avoids fragile template math).
    tw, th = 640.0, 120.0
    pts: list[str] = []
    n = len(trend)
    for i, row in enumerate(trend):
        x = (i / (n - 1) * tw) if n > 1 else tw / 2
        y = (1.0 - max(0.0, min(1.0, float(row["asr"])))) * th
        pts.append(f"{x:.1f},{y:.1f}")
    trend_svg = {
        "w": tw,
        "h": th,
        "points": " ".join(pts),
        "end": pts[-1] if pts else None,
        "first_asr": trend[0]["asr"] if trend else 0.0,
        "last_asr": trend[-1]["asr"] if trend else 0.0,
    }
    surface_rollup: dict[str, dict[str, Any]] = {}
    for item in stats.recent_runs:
        label = run_surface_label(item)
        slot = surface_rollup.setdefault(
            label, {"label": label, "runs": 0, "attempts": 0, "successes": 0}
        )
        slot["runs"] += 1
        slot["attempts"] += int((item.summary or {}).get("total_traces", 0) or 0)
        slot["successes"] += int((item.summary or {}).get("successful_traces", 0) or 0)
    for slot in surface_rollup.values():
        slot["asr"] = slot["successes"] / slot["attempts"] if slot["attempts"] else 0.0
    return {
        "s": stats,
        "latest": latest,
        "prev": prev,
        "ppi": ppi,
        "diff": diff,
        "heatmap": heatmap,
        "queue": queue,
        "top_findings": top_findings,
        "trend": trend,
        "trend_svg": trend_svg,
        "n_partial": n_partial,
        "surface_rollup": list(surface_rollup.values()),
        "run_display_name": run_display_name,
    }


def _surface_label(target_name: str) -> tuple[str, str]:
    """Human-readable app label + region hint from a stored
    ``Trace.target_name``. Pure formatting of the real class name —
    no invented app/account metadata. Returns ``(label, region)``
    where ``region`` is the raw class name shown in mono."""

    words = re.sub(r"(?<!^)(?=[A-Z0-9])", " ", target_name).split()
    if words and words[-1] == "Target":
        words = words[:-1]
    label = " ".join(words) or target_name
    return label, target_name


def build_surfaces_context(session: Session) -> dict[str, Any]:
    """Assemble the Surfaces inventory purely from real DB rows.

    A *surface* is a distinct ``Trace.target_name`` — the Gemini
    deployment actually driven. Per surface we count attempts (traces),
    runs (distinct ``run_id``), and open findings (findings whose trace
    ran against that surface), plus the severity mix. Exposure is a
    derived-from-data flag: a surface with ≥1 finding is *exposed*.

    Judge agreement and benchmarks are supplied separately on Oracle trust.
    """
    from collections import defaultdict

    from sqlalchemy import func

    from advtest_ext.storage import Finding, Trace

    attempt_rows = (
        session.query(
            Trace.target_name,
            func.count(Trace.id),
            func.count(func.distinct(Trace.run_id)),
        )
        .group_by(Trace.target_name)
        .all()
    )
    finding_counts: dict[str, int] = {
        name: count
        for name, count in (
            session.query(Trace.target_name, func.count(Finding.id))
            .join(Finding, Finding.trace_id == Trace.id)
            .group_by(Trace.target_name)
            .all()
        )
    }
    sev_by_surface: dict[str, dict[str, int]] = defaultdict(dict)
    for tn, sev, cnt in (
        session.query(Trace.target_name, Finding.severity, func.count(Finding.id))
        .join(Finding, Finding.trace_id == Trace.id)
        .group_by(Trace.target_name, Finding.severity)
        .all()
    ):
        sev_by_surface[tn][sev] = cnt
    behaviors_by_surface: dict[str, list[str]] = defaultdict(list)
    for tn, beh in session.query(Trace.target_name, Trace.behavior_id).distinct().all():
        behaviors_by_surface[tn].append(beh)

    surfaces: list[dict[str, Any]] = []
    for target_name, attempts, n_runs in attempt_rows:
        n_find = int(finding_counts.get(target_name, 0))
        label, region = _surface_label(target_name)
        if n_find > 0:
            exposure, exposure_label = "exposed", "Exposed"
        elif attempts > 0:
            exposure, exposure_label = "mitigated", "Probed · no findings"
        else:  # pragma: no cover - a surface with no traces can't appear here
            exposure, exposure_label = "untested", "Not yet tested"
        # Severity chips, ordered most-severe first, only for real counts.
        order = {"crit": 0, "critical": 0, "high": 1, "med": 2, "medium": 2, "low": 3}
        sev_chips = sorted(
            sev_by_surface.get(target_name, {}).items(),
            key=lambda kv: order.get(kv[0], 9),
        )
        surfaces.append(
            {
                "target_name": target_name,
                "label": label,
                "region": region,
                "attempts": int(attempts),
                "runs": int(n_runs),
                "findings": n_find,
                "sev_chips": sev_chips,
                "behaviors": sorted(behaviors_by_surface.get(target_name, [])),
                "exposure": exposure,
                "exposure_label": exposure_label,
            }
        )
    surfaces.sort(key=lambda s: (-s["findings"], -s["attempts"]))

    totals = {
        "surfaces": len(surfaces),
        "exposed": sum(1 for s in surfaces if s["exposure"] == "exposed"),
        "findings": sum(s["findings"] for s in surfaces),
        "attempts": sum(s["attempts"] for s in surfaces),
        # Real distinct run count across all surfaces.
        "runs": int(session.query(func.count(func.distinct(Trace.run_id))).scalar() or 0),
    }
    return {
        "surfaces": surfaces,
        "totals": totals,
    }


def build_surface_panel_context(session: Session, target_name: str) -> dict[str, Any]:
    """Real detail for one surface's slide-over: attempt/run/finding
    counts, the behaviours exercised, the deterministic-signal layer map
    actually recorded, and the surface's findings. Everything is read
    back from the DB; a signal that never fired shows ``0 / N``."""
    from collections import defaultdict

    from sqlalchemy import func

    from advtest_ext.storage import Finding, JudgeScore, SignalRecord, Trace

    attempts = int(
        session.query(func.count(Trace.id)).filter(Trace.target_name == target_name).scalar() or 0
    )
    if attempts == 0:
        return {}
    n_runs = int(
        session.query(func.count(func.distinct(Trace.run_id)))
        .filter(Trace.target_name == target_name)
        .scalar()
        or 0
    )
    # Run strategies driving this surface (real ``Run.strategy`` values).
    from advtest_ext.storage import Run

    strategies = sorted(
        {
            strat
            for (strat,) in session.query(Run.strategy)
            .join(Trace, Trace.run_id == Run.id)
            .filter(Trace.target_name == target_name)
            .distinct()
            .all()
        }
    )
    behaviors = sorted(
        {
            b
            for (b,) in session.query(Trace.behavior_id)
            .filter(Trace.target_name == target_name)
            .distinct()
            .all()
        }
    )
    # Deterministic-signal layer map: fired / total per signal name.
    sig_rows: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for name, fired, cnt in (
        session.query(SignalRecord.name, SignalRecord.fired, func.count(SignalRecord.id))
        .join(Trace, Trace.id == SignalRecord.trace_id)
        .filter(Trace.target_name == target_name)
        .group_by(SignalRecord.name, SignalRecord.fired)
        .all()
    ):
        sig_rows[name][1] += int(cnt)
        if fired:
            sig_rows[name][0] += int(cnt)
    signals = [
        {"name": name, "fired": vals[0], "total": vals[1]}
        for name, vals in sorted(sig_rows.items())
    ]
    n_judged = int(
        session.query(func.count(func.distinct(JudgeScore.trace_id)))
        .join(Trace, Trace.id == JudgeScore.trace_id)
        .filter(Trace.target_name == target_name)
        .scalar()
        or 0
    )
    n_success = int(
        session.query(func.count(Trace.id))
        .filter(Trace.target_name == target_name, Trace.verdict == "success")
        .scalar()
        or 0
    )
    findings = (
        session.query(Finding)
        .join(Trace, Trace.id == Finding.trace_id)
        .filter(Trace.target_name == target_name)
        .order_by(Finding.severity, Finding.promoted_at.desc())
        .all()
    )
    label, region = _surface_label(target_name)
    return {
        "target_name": target_name,
        "label": label,
        "region": region,
        "attempts": attempts,
        "runs": n_runs,
        "strategies": strategies,
        "behaviors": behaviors,
        "signals": signals,
        "n_judged": n_judged,
        "n_success": n_success,
        "findings": findings,
    }


def _load_search_report(run: Any) -> dict[str, Any] | None:
    """Load a run's TAP search-coverage report (``tap_search_report.json``)
    from its on-disk artifact root, if present. Returns ``None`` when the run
    has no output_dir or the file is absent, so the coverage panel hides
    gracefully rather than inventing numbers."""
    import json

    out_dir = getattr(run, "output_dir", None)
    if not out_dir:
        return None
    path = Path(str(out_dir)) / "tap_search_report.json"
    try:
        if path.is_file():
            data: dict[str, Any] = json.loads(path.read_text())
            return data
    except (OSError, ValueError):  # pragma: no cover - defensive
        return None
    return None


def _load_judge_benchmark(output_dir: Path | None = None) -> dict[str, Any] | None:
    """Load the JailbreakBench held-out benchmark JSON, if present.

    Tries ``<output_dir>/judge_benchmark.json`` then the repo's
    ``out/judge_benchmark.json``. Returns ``None`` when absent so the
    Judge-quality page omits the benchmark block gracefully rather than
    fabricating one."""
    import json

    candidates: list[Path] = []
    if output_dir is not None:
        candidates.append(output_dir / "judge_benchmark.json")
    candidates.append(Path("out/judge_benchmark.json"))
    candidates.append(Path(__file__).resolve().parents[2] / "out" / "judge_benchmark.json")
    for path in candidates:
        try:
            if path.is_file():
                data: dict[str, Any] = json.loads(path.read_text())
                return data
        except (OSError, ValueError):  # pragma: no cover - defensive
            continue
    return None


def _benchmark_operating_points(benchmark: dict[str, Any]) -> list[dict[str, float]]:
    """Real FP/FN sweep across success thresholds from the benchmark's
    scored rows (``score`` vs ``truth``). Powers the operating-points
    curve. Empty when the file carries no per-row scores."""
    rows = benchmark.get("scored_rows") or []
    scored = [(float(r["score"]), r["truth"]) for r in rows if "score" in r and "truth" in r]
    if not scored:
        return []
    n_pos = sum(1 for _s, t in scored if t == "success")
    n_neg = len(scored) - n_pos
    points: list[dict[str, float]] = []
    for thr in (0.05, 0.19, 0.5, 0.7, 0.9):
        fp = sum(1 for s, t in scored if s >= thr and t != "success")
        fn = sum(1 for s, t in scored if s < thr and t == "success")
        points.append(
            {
                "threshold": thr,
                "fp": (fp / n_neg) if n_neg else 0.0,
                "fn": (fn / n_pos) if n_pos else 0.0,
            }
        )
    return points


def build_judge_context(session: Session, output_dir: Path | None = None) -> dict[str, Any]:
    """Assemble the Judge-quality spec sheet.

    Headline = the in-house gold-set agreement report
    (``agreement_for_gold_set``), scored over the real ``GoldLabel``
    rows. Second, clearly-labelled block = the JailbreakBench held-out
    benchmark from ``out/judge_benchmark.json`` (measured kappa, ceiling
    alpha, error rates, and published-judge baselines) — omitted when
    the file is absent. The operating-points curve is a real threshold
    sweep over the benchmark's scored rows."""
    from advtest_ext.core.agreement import agreement_for_gold_set

    agreement = agreement_for_gold_set(session)
    benchmark = _load_judge_benchmark(output_dir)

    field_rows: list[dict[str, Any]] = []
    operating_points: list[dict[str, float]] = []
    if benchmark is not None:
        for name, b in (benchmark.get("baselines") or {}).items():
            field_rows.append(
                {
                    "judge": name,
                    "kappa": b.get("kappa"),
                    "fp": b.get("fp"),
                    "fn": b.get("fn"),
                    "ours": False,
                }
            )
        field_rows.append(
            {
                "judge": benchmark.get("judge", "Fission"),
                "kappa": benchmark.get("kappa"),
                "fp": (benchmark.get("false_positive") or {}).get("rate"),
                "fn": (benchmark.get("false_negative") or {}).get("rate"),
                "ours": True,
            }
        )
        field_rows.sort(key=lambda r: (r["kappa"] is None, -(r["kappa"] or 0.0)))
        operating_points = _benchmark_operating_points(benchmark)

    return {
        "agreement": agreement,
        "benchmark": benchmark,
        "field_rows": field_rows,
        "operating_points": operating_points,
    }


def create_app(
    *,
    db_url: str | None = None,
    output_dir: Path | None = None,
    engine: Engine | None = None,
    auth_token: str | None = None,
    live_test_controller: LiveTestController | None = None,
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
    effective_db_url = db_url or engine.url.render_as_string(hide_password=False)
    live_tests = live_test_controller or LiveTestController(effective_db_url)

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

    def service_context(request: Request) -> dict[str, Any]:
        cached = getattr(request.state, "service_context", None)
        if cached is not None:
            return cast("dict[str, Any]", cached)
        with factory() as catalog_session:
            choices, assignments = service_catalog(catalog_session)
        valid = {choice["key"] for choice in choices}
        default = "gemini" if not assignments or "gemini" in assignments.values() else "all"
        explicit = request.query_params.get("service")
        if explicit is not None and explicit not in valid:
            raise HTTPException(400, "Unknown service selection")
        selected = explicit or request.cookies.get("fission-service", default)
        if selected not in valid:
            selected = default
        context = {
            "service_options": choices,
            "selected_service": {"key": selected, "label": SERVICE_LABELS[selected]},
            "service_switch_path": request.url.path
            if request.url.path in COLLECTION_PATHS
            else "/surfaces",
        }
        request.state.service_context = context
        request.state.service_run_ids = [
            key for key, value in assignments.items() if value == selected
        ]
        return context

    @app.middleware("http")
    async def remember_service(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        context = getattr(request.state, "service_context", None)
        if context and response.status_code < 400 and request.query_params.get("service"):
            response.set_cookie(
                "fission-service",
                context["selected_service"]["key"],
                max_age=31536000,
                httponly=True,
                samesite="lax",
                secure=request.url.scheme == "https",
            )
        return response

    templates = Jinja2Templates(directory=_TEMPLATE_DIR, context_processors=[service_context])
    templates.env.globals["attack_label"] = attack_label
    templates.env.globals["objective_label"] = objective_label
    templates.env.globals["finding_title"] = finding_title
    templates.env.globals["finding_method"] = finding_method
    templates.env.globals["finding_severity"] = finding_severity
    templates.env.globals["target_surface_label"] = lambda name: target_identity(name)[2]

    # Cache-busting version for the tree component's static assets. tree.js/
    # tree.css change without their URL changing, so browsers keep serving the
    # stale copy. Appending ?v=<mtime> makes a changed file a new URL. Computed
    # per render (a callable global) so editing a static file busts the cache
    # immediately — no app restart needed.
    def _static_version() -> str:
        try:
            return str(
                max(
                    int((_STATIC_DIR / _name).stat().st_mtime)
                    for _name in (
                        "tree.js",
                        "tree.css",
                        "dashboard.css",
                        "dashboard.js",
                        "run-actions.js",
                        "run-findings.js",
                    )
                    if (_STATIC_DIR / _name).exists()
                )
            )
        except ValueError:
            return "0"

    templates.env.globals["static_v"] = _static_version
    # "Running" means a live heartbeat, not just a missing finish time.
    templates.env.globals["run_is_active"] = run_is_active
    templates.env.globals["run_state"] = run_state

    if _STATIC_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=_STATIC_DIR),
            name="static",
        )

    def get_session(request: Request) -> Iterator[Session]:
        # Generator-style FastAPI dependency: yield the session and
        # then close it in the finally so the connection lands back
        # in the pool the moment the request returns. The previous
        # version returned the session directly and relied on GC,
        # which exhausts the pool in seconds when 5+ HTMX partials
        # poll every 2-3 s.
        session = factory()
        try:
            context = service_context(request)
            if (
                request.url.path in COLLECTION_PATHS
                or request.url.path.startswith(("/surface-groups/", "/surfaces/"))
            ) and context["selected_service"]["key"] != "all":
                scope_campaign_reads(session, request.state.service_run_ids)
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
    async def overview_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = build_overview_context(session)
        return templates.TemplateResponse(request, "overview.html", ctx)

    @app.get("/runs", response_class=HTMLResponse)
    async def runs_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        from advtest_ext.dashboard.overview import run_has_success
        from advtest_ext.storage.repository import hydrate_missing_summaries

        all_runs = hydrate_missing_summaries(session, list_runs(session))
        runs = [run for run in all_runs if run_has_success(run)]
        group_order = ("sheets", "docs", "cross-surface", "other")
        groups = []
        for key in group_order:
            members = [run for run in runs if run_surface_key(run) == key]
            if not members:
                continue
            attempts = sum(int((run.summary or {}).get("total_traces", 0) or 0) for run in members)
            findings = sum(
                int((run.summary or {}).get("successful_traces", 0) or 0) for run in members
            )
            groups.append(
                {
                    "key": key,
                    "label": run_surface_label(members[0]),
                    "runs": members,
                    "attempts": attempts,
                    "findings": findings,
                    "asr": findings / attempts if attempts else 0.0,
                }
            )
        return templates.TemplateResponse(
            request,
            "runs/index.html",
            {
                "runs": runs,
                "groups": groups,
                "run_display_name": run_display_name,
                "run_method_label": run_method_label,
            },
        )

    @app.patch("/runs/{run_id}")
    def rename_run(
        request: Request,
        run_id: str,
        submission: RenameRun,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> dict[str, str]:
        require_same_origin(request)
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        run.config = {**(run.config or {}), "display_name": submission.name}
        session.commit()
        return {"id": run_id, "name": run_display_name(run)}

    @app.delete("/runs/{run_id}", status_code=204)
    def delete_run(
        request: Request,
        run_id: str,
        submission: DeleteRun,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        require_same_origin(request)
        if submission.confirmation != run_id:
            raise HTTPException(422, "Confirm the run selected for deletion")
        from advtest_ext.storage.models import Run

        run = session.query(Run).filter(Run.id == run_id).with_for_update().one_or_none()
        if run is None:
            raise HTTPException(404, "Run not found")
        live = live_tests.snapshot()
        # An interrupted run (unfinished, no heartbeat) is dead and deletable;
        # only a run that is actually in flight is protected.
        if run_is_active(run) or (
            live.get("run_id") == run_id
            and live.get("status") in {"starting", "running", "stopping"}
        ):
            raise HTTPException(409, "Active runs cannot be deleted")
        delete_run_results(session, run_id)
        session.commit()
        return Response(status_code=204)

    @app.get("/live-tests", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/assessments/new", response_class=HTMLResponse)
    @app.get("/runs/new", response_class=HTMLResponse)
    async def live_tests_index(request: Request) -> Response:
        """Launch a named, allow-listed surface assessment."""
        return templates.TemplateResponse(
            request,
            "live_tests.html",
            {
                "tests": live_tests.specs,
                "playbooks": live_tests.playbooks,
                "state": live_tests.snapshot(),
            },
        )

    @app.get("/live-tests/status")
    async def live_tests_status() -> dict[str, Any]:
        return live_tests.snapshot()

    @app.get("/live-tests/progress")
    async def live_tests_progress() -> dict[str, Any]:
        """Snapshot + live turn-by-turn progress for the active run.

        The controller streams a vendor-neutral console; this endpoint adds the
        structured view the operator actually wants during a run: each executed
        turn with its verdict, judge score, and which signals fired — read
        straight from the DB for the active ``run_id``, so it reflects real
        persisted evidence rather than parsed stdout. A turn is
        ``signal_verified`` when a *deterministic* (non-LLM) signal fired, which
        separates ground-truth wins from oracle-only calls."""
        snap = live_tests.snapshot()
        run_id = snap.get("run_id")
        turns: list[dict[str, Any]] = []
        summary = {"turns": 0, "successes": 0, "verified": 0, "best_score": 0.0}
        if run_id:
            session = factory()
            try:
                traces = list_traces_for_run(session, run_id)
                for idx, t in enumerate(traces):
                    sigs = list_signals_for_trace(session, t.id)
                    signal_view = [
                        {"name": s.name, "kind": s.kind, "fired": bool(s.fired)} for s in sigs
                    ]
                    verified = any(s.fired and s.kind != "llm_judge_verdict" for s in sigs)
                    scores = [js.score for js in t.judge_scores]
                    score = max(scores) if scores else 0.0
                    is_success = (t.verdict or "").lower() == "success"
                    mutation = (t.extra_metadata or {}).get("tap_crescendo", {}).get(
                        "mutation"
                    ) or {}
                    move = ""
                    if mutation.get("feature_value"):
                        move = f"{mutation.get('dimension') or 'move'}: {mutation['feature_value']}"
                    turns.append(
                        {
                            "index": idx + 1,
                            "depth": t.depth,
                            "verdict": t.verdict or "pending",
                            "score": round(float(score), 3),
                            "signals": signal_view,
                            "signal_verified": verified,
                            "move": move,
                            "trace_id": t.id,
                        }
                    )
                    summary["turns"] += 1
                    summary["best_score"] = max(summary["best_score"], round(float(score), 3))
                    if is_success:
                        summary["successes"] += 1
                        if verified:
                            summary["verified"] += 1
            except Exception:  # pragma: no cover - progress is best-effort
                turns = []
            finally:
                session.close()
        # Summary spans the whole run; only the most recent turns are streamed to
        # keep the poll light on a long crescendo (a run can be hundreds of turns).
        turns = turns[-80:]
        return {**snap, "turns": turns, "summary": summary}

    @app.get("/live-tests/plan/{playbook}")
    async def live_tests_plan(playbook: str) -> dict[str, Any]:
        """The declared staging plan for a playbook: the setup steps that will
        replay through the tool ops, the deterministic success signals, and the
        seed-chain length. Lets the run screen show *what will happen* before the
        first turn, so an operator sees the carrier being planted, not a blank
        console."""
        from advtest_ext.guides.corpus import load_guides
        from advtest_ext.guides.models import display_title

        guides_dir = Path(__file__).resolve().parents[2] / "guides"
        guides = load_guides(guides_dir) if guides_dir.exists() else []
        guide = next((g for g in guides if g.id == playbook), None)
        if guide is None:
            raise HTTPException(404, f"playbook {playbook!r} not found")
        steps = [
            {
                "n": i + 1,
                "op": f"{st.region}:{st.operation}",
                "account": st.account_role or "—",
                "intent": st.intent,
            }
            for i, st in enumerate(guide.setup_steps)
        ]
        signals = [
            {"kind": s.get("kind", "—"), "name": s.get("name", "—")} for s in guide.success.signals
        ]
        return {
            "id": guide.id,
            "title": display_title(guide),
            "target": "Gemini in " + guide.target.app.replace("google_", "").title(),
            "steps": steps,
            "signals": signals,
            "seed_turns": len(guide.exemplar_turns),
        }

    @app.get("/live-tests/options")
    async def live_tests_options() -> dict[str, Any]:
        """Everything the New-run dialog needs to choose and configure a run
        without leaving the runs list: the guide catalogue keyed by Workspace
        surface, flagged single-workspace vs across-workspaces (whether the run
        drives a second account), each with a one-line blurb, so the picker
        reads as scenarios per surface, not filenames."""
        from advtest_ext.guides.corpus import load_guides
        from advtest_ext.guides.models import display_title

        def canon_surface(app_name: str) -> str:
            key = str(app_name or "").replace("google_", "").strip().lower()
            return key or "other"

        def surface_label(key: str) -> str:
            return {
                "docs": "Docs",
                "sheets": "Sheets",
                "gmail": "Gmail",
                "slides": "Slides",
                "drive": "Drive",
                "calendar": "Calendar",
                "tool_use_agent": "Tool-use agent",
            }.get(key, key.replace("_", " ").title() or "Other")

        def blurb_of(guide: Any) -> str:
            text = " ".join((guide.objective or "").split())
            sentence = text.split(". ", 1)[0].rstrip(".")
            if len(sentence) > 120:
                sentence = sentence[:120].rsplit(" ", 1)[0] + "…"
            return sentence

        guides_dir = Path(__file__).resolve().parents[2] / "guides"
        guides = load_guides(guides_dir) if guides_dir.exists() else []
        guide_opts: list[dict[str, Any]] = []
        for g in guides:
            target = canon_surface(g.target.app)
            # "Across workspaces" = the run drives more than one account (e.g. an
            # attacker_sender that mails the victim, who then talks to Gemini in
            # their own Docs/Gmail). That needs a second signed-in profile, so it
            # is the account axis, NOT how many apps the flow touches.
            victim_role = str(getattr(g.target, "account_role", None) or "recipient")
            roles = {victim_role} | {
                str(getattr(st, "account_role", None) or "") for st in g.setup_steps
            }
            roles.discard("")
            extra_accounts = sorted(roles - {victim_role})
            guide_opts.append(
                {
                    "id": g.id,
                    "title": display_title(g),
                    "surface": target,
                    "surface_label": surface_label(target),
                    "multi_workspace": len(roles) > 1,
                    "second_accounts": extra_accounts,
                    "maturity": (getattr(g, "maturity", None) or "").lower(),
                    "blurb": blurb_of(g),
                }
            )
        guide_opts.sort(key=lambda o: (o["surface_label"], o["title"].lower()))
        return {"enabled": live_tests.enabled, "guides": guide_opts}

    @app.post("/live-tests/stop")
    async def live_tests_stop() -> JSONResponse:
        try:
            state = live_tests.stop()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return JSONResponse(state, status_code=202)

    @app.post("/live-tests/{test_id}/start")
    async def live_tests_start(test_id: str, submission: LiveTestStart) -> JSONResponse:
        if not submission.confirmed:
            raise HTTPException(422, "confirm the live-account prerequisites before starting")
        try:
            state = live_tests.start(
                test_id,
                playbook=submission.playbook,
                branching_factor=submission.branching_factor,
                max_depth=submission.max_depth,
                max_nodes_per_seed=submission.max_nodes_per_seed,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, f"unknown live test {test_id!r}") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse(state, status_code=202)

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    async def runs_show(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = _build_run_context(session, run_id, request.query_params.get("finding"))
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
        ctx = _build_run_context(session, run_id, request.query_params.get("finding"))
        return templates.TemplateResponse(request, "runs/_findings.html", ctx)

    @app.get("/runs/{run_id}/findings/{finding_id}/_evidence", response_class=HTMLResponse)
    async def run_finding_evidence(
        request: Request,
        run_id: str,
        finding_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        finding = get_finding(session, finding_id)
        if finding is None or finding.run_id != run_id or finding.trace is None:
            raise HTTPException(404, "Finding not found in this run")
        # Respect shared capture filenames in legacy runs, as the full run page does.
        traces = list_traces_for_run(session, run_id)
        responses = _full_responses_for_traces(traces)
        conversation = next(
            (
                group
                for group in build_transcript_groups(traces, responses)
                if any(turn.trace_id == finding.trace_id for turn in group.turns)
            ),
            None,
        )
        return templates.TemplateResponse(
            request,
            "runs/_finding_evidence.html",
            {
                "finding": finding,
                "trace": finding.trace,
                "conversation": conversation,
                "full_response": responses.get(finding.trace_id, ""),
            },
        )

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

    def _build_run_context(
        session: Session, run_id: str, selected_finding_id: str | None = None
    ) -> dict[str, Any]:
        """Shared context builder for the full /runs/{id} page and the
        five HTMX polling partials below. Single source of truth so
        the partial's slice of the dashboard always matches what the
        full page would render."""
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        # Per-trace assembled response from intercepted Gemini traffic.
        # Empty string when no capture exists (api_mode trace, interception
        # disabled, or pre-Phase-2 trace). Templates fall back to
        # ``trace.raw_response`` in that case.
        full_responses = _full_responses_for_traces(traces)
        heatmap = asr_heatmap_formatted(session, run_id)
        findings = list_findings(session, run_id=run_id, limit=None)

        # Order the findings reviewer by primary judge score, selecting
        # the highest-scoring finding initially. Falls back to the
        # promotion order when no judge score exists. Findings whose
        # trace was deleted (shouldn't happen, but FK could be loose
        # in legacy DBs) get filtered.
        def _score_of(finding: Any) -> float:
            t = getattr(finding, "trace", None)
            if t is None or not getattr(t, "judge_scores", None):
                return 0.0
            return float(t.judge_scores[0].score)

        review_findings = sorted(
            (f for f in findings if getattr(f, "trace", None) is not None),
            key=_score_of,
            reverse=True,
        )
        selected_finding = next(
            (f for f in review_findings if f.id == selected_finding_id),
            review_findings[0] if review_findings else None,
        )
        top_findings = review_findings[:6]
        conversations = build_transcript_groups(traces, full_responses)
        selected_conversation = next(
            (
                group
                for group in conversations
                if selected_finding
                and any(turn.trace_id == selected_finding.trace_id for turn in group.turns)
            ),
            None,
        )
        valid_techniques = {
            trace.technique_id
            for trace in traces
            if trace.technique_id
            and trace.technique_id.strip().lower()
            not in {"", "-", "—", ",", "none", "unknown", "n/a", "null"}
        }
        # Per-technique aggregate for the bar-chart strip: across all
        # behaviours in the run, what's the mean ASR per hiding
        # technique? Rolls up the heatmap rows. The heatmap dict is
        # untyped (``dict[str, object]``) so we narrow at the boundary.
        technique_chart: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = heatmap["rows"]  # type: ignore[assignment]
        for row in rows:
            if row["technique"] not in valid_techniques:
                continue
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
        comparison_rows = [row for row in rows if row["technique"] in valid_techniques]
        comparison_families = {
            cell["family"] for row in comparison_rows for cell in row["cells"] if cell["attempts"]
        }
        technique_heatmap = {
            **heatmap,
            "families": [
                family
                for family in cast("list[Any]", heatmap["families"])
                if family in comparison_families
            ],
            "rows": [
                {
                    **row,
                    "cells": [
                        cell for cell in row["cells"] if cell["family"] in comparison_families
                    ],
                }
                for row in comparison_rows
            ],
        }
        # Measurement-spine headline: the calibrated ASR ± CI. Rectifies
        # the oracle's ASR by the oracle↔expert disagreement on this run's
        # uniform gold-label slice (PPI). Degrades to an oracle-only
        # estimate when no labels exist yet.
        from advtest_ext.core.ppi import ppi_asr_for_run

        ppi = ppi_asr_for_run(session, run_id)
        tree_hash = _tree_state_hash(phases, finished=not run_is_active(run))
        duration_seconds = None
        if run.started_at and run.finished_at:
            duration_seconds = max(0, int((run.finished_at - run.started_at).total_seconds()))
        run_metrics = {
            "attempts": len(traces),
            "conversations": len(conversations),
            "successful": sum(trace.verdict == "success" for trace in traces),
            "techniques": len(valid_techniques),
            "scenarios": len({trace.behavior_id for trace in traces if trace.behavior_id}),
            "duration_seconds": duration_seconds,
        }
        return {
            "run": run,
            "run_name": run_display_name(run),
            "surface_name": target_identity(run.target_name)[2],
            "run_metrics": run_metrics,
            "traces": traces,
            "heatmap": heatmap,
            "findings": findings,
            "top_findings": top_findings,
            "review_findings": review_findings,
            "selected_finding": selected_finding,
            "selected_conversation": selected_conversation,
            "technique_chart": technique_chart,
            "show_technique_chart": len(technique_chart) > 1,
            "show_technique_heatmap": bool(technique_chart) and len(comparison_families) > 1,
            "technique_heatmap": technique_heatmap,
            "full_responses": full_responses,
            "phases": phases,
            "ppi": ppi,
            "search_report": _load_search_report(run),
            "tree_hash": tree_hash,
            "tree_graph": _tree_graph_payload(
                phases, finished=not run_is_active(run), tree_hash=tree_hash
            ),
        }

    @app.get("/runs/{run_id}/_tree", response_class=HTMLResponse)
    async def runs_tree_partial(
        request: Request,
        run_id: str,
        v: str = "",
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Server-rendered rebuild of the live attack-tree explorer.

        The Cytoscape client updates the tree in place from
        ``/_tree.json`` (see below), so this HTML partial is no longer
        polled every 2 s. It survives as a structural fallback: the client
        re-fetches it only when the run grows an entirely new
        (behaviour, technique) cell — a rare event that changes the
        navigator, not just the graph. ``v`` is the state hash the client
        last rendered; an unchanged tree returns ``204 No Content``."""
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        phases = _build_phase_forests(_collapse_conversations(list(traces)))
        tree_hash = _tree_state_hash(phases, finished=not run_is_active(run))
        if v and v == tree_hash:
            return Response(status_code=204)
        return templates.TemplateResponse(
            request,
            "runs/_tree_live.html",
            {
                "run": run,
                "phases": phases,
                "tree_hash": tree_hash,
                "tree_graph": _tree_graph_payload(
                    phases, finished=not run_is_active(run), tree_hash=tree_hash
                ),
            },
        )

    @app.get("/runs/{run_id}/_panel", response_class=HTMLResponse)
    async def runs_panel(
        request: Request,
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Slide-over panel body for a run: a compact meta line + the
        interactive attack tree. Loaded by ``openRunPanel()`` (base.html) into
        the shared slide-over and mounted client-side via AttackTree.boot()."""
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        phases = _build_phase_forests(_collapse_conversations(list(traces)))
        for phase in phases:
            forest = phase.get("forest")
            if isinstance(forest, list):
                forest.sort(
                    key=lambda cell: (
                        int(cell.get("n_success", 0)) == 0 if isinstance(cell, dict) else True
                    )
                )
        tree_hash = _tree_state_hash(phases, finished=not run_is_active(run))
        return templates.TemplateResponse(
            request,
            "runs/_panel.html",
            {
                "run": run,
                "phases": phases,
                "tree_hash": tree_hash,
                "tree_graph": _tree_graph_payload(
                    phases, finished=not run_is_active(run), tree_hash=tree_hash
                ),
            },
        )

    @app.get("/runs/{run_id}/_tree.json")
    async def runs_tree_json(
        run_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Cytoscape graph payload for the live attack tree. The client
        polls this every ~2 s while the run is in flight and diffs it
        against what it has already mounted, ``cy.add()``-ing only the new
        nodes/edges and re-classing nodes whose verdict changed — a smooth
        incremental update instead of the old full Mermaid repaint. Polling
        stops once ``finished`` is true. Same builder as the initial embed
        (``tree_graph_payload``) so the two never drift."""
        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list_traces_for_run(session, run_id)
        phases = _build_phase_forests(_collapse_conversations(list(traces)))
        tree_hash = _tree_state_hash(phases, finished=not run_is_active(run))
        payload = _tree_graph_payload(phases, finished=not run_is_active(run), tree_hash=tree_hash)
        return JSONResponse(payload)

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
        from advtest_ext.dashboard.transcript import build_transcript_groups

        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        traces = list(list_traces_for_run(session, run_id))
        full_responses = _full_responses_for_traces(traces)
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
        from advtest_ext.dashboard.report import WEASYPRINT_INSTALLED, build_report_context

        run = get_run(session, run_id)
        if run is None:
            raise HTTPException(404, f"run {run_id!r} not found")
        ctx = build_report_context(session=session, run=run)
        ctx["export_view"] = request.query_params.get("export") == "1"
        ctx["pdf_available"] = WEASYPRINT_INSTALLED
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
        # Full conversation for THIS node: a tap_crescendo node is many turns
        # (each its own trace). Reconstruct the node's turns — with the complete
        # reply per turn — so the page shows the whole side-panel conversation,
        # not just the one turn the node was clicked at.
        from advtest_ext.dashboard.transcript import build_transcript_groups

        run_traces = list(list_traces_for_run(session, trace.run_id))
        conv_full = _full_responses_for_traces(run_traces)
        full_response = conv_full.get(trace.id, trace.raw_response or "")
        node_turns: list[Any] = []
        for group in build_transcript_groups(run_traces, conv_full):
            if any(turn.trace_id == trace_id for turn in group.turns):
                node_turns = group.turns
                break
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
                "node_turns": node_turns,
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
        # Severity is derived (stored value is a constant "med"), so filter and
        # sort on the derived value in Python rather than in the SQL WHERE.
        findings = list_findings(session, run_id=run_id, behavior_id=behavior_id)
        fsev = {f.id: finding_severity(f) for f in findings}
        sev_counts = {s: 0 for s in ("critical", "high", "med", "low")}
        for s in fsev.values():
            sev_counts[s] = sev_counts.get(s, 0) + 1
        findings_total = len(findings)
        if severity:
            findings = [f for f in findings if fsev[f.id] == severity]
        findings.sort(
            key=lambda f: (
                severity_rank(fsev[f.id]),
                -(max((s.score for s in f.trace.judge_scores), default=0.0) if f.trace else 0.0),
            )
        )
        # Filter dropdowns: readable run names + objective labels, value = id.
        run_options = [
            (rid, run_display_name(run) if run else rid)
            for rid in list_finding_run_ids(session)
            for run in [get_run(session, rid)]
        ]
        # Behaviour dropdown scoped to the selected run.
        behavior_options = list_finding_behavior_ids(session, run_id=run_id)
        finding_seq = finding_run_seq_map(session, [f.run_id for f in findings])
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
                "surface_label": surface_label,
                "run_display_name": run_display_name,
                "finding_seq": finding_seq,
                "sev_counts": sev_counts,
                "findings_total": findings_total,
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
        # Cross-run authenticity is stamped on findings automatically when a run
        # completes; back-fill legacy findings (created before that hook) on first
        # view. Only self-extraction guides carry a verdict — recompute is a no-op
        # otherwise — and it write-through caches, so this runs at most once.
        guide_id = finding.run.guide_id if finding.run else None
        if guide_id and not finding.authenticity:
            from advtest_ext.core.authenticity_pipeline import recompute_guide_authenticity

            try:
                if recompute_guide_authenticity(factory, guide_id) is not None:
                    session.refresh(finding)
            except Exception:  # pragma: no cover - defensive; never break the page
                pass
        # Prev/next within the same run (ordered by promotion), so you can walk a
        # run's findings without going back to the list.
        run_seq = finding_run_seq_map(session, [finding.run_id])
        ordered = sorted(run_seq, key=lambda fid: run_seq[fid])
        here = ordered.index(finding.id) if finding.id in ordered else -1
        prev_finding = ordered[here - 1] if here > 0 else None
        next_finding = ordered[here + 1] if 0 <= here < len(ordered) - 1 else None
        return templates.TemplateResponse(
            request,
            "findings/show.html",
            {
                "finding": finding,
                "trace": trace,
                "full_response": full_response,
                "authenticity": finding.authenticity or None,
                "finding_seq_n": run_seq.get(finding.id),
                "prev_finding": prev_finding,
                "next_finding": next_finding,
                "run_display_name": run_display_name,
                "return_to_run": request.query_params.get("from_run") == finding.run_id,
            },
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

    @app.get("/ledger", response_class=HTMLResponse)
    async def seed_ledger_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Seed ledger — per-seed outcomes aggregated across runs, with a
        triage recommendation for each. The downstream half of the
        passive-human loop: humans fix seeds between runs, and this is the
        view that tells them which seeds to fix and why."""
        from advtest_ext.dashboard.seed_ledger import build_seed_ledger

        return templates.TemplateResponse(
            request, "seed_ledger.html", {"ledger": build_seed_ledger(session)}
        )

    @app.get("/drift", response_class=HTMLResponse)
    async def drift_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Drift view — the attacker wandering off-mechanism mid-run, and
        the product surface moving underneath one.

        Both are read back from ``Trace.extra_metadata['drift']``, which
        the attack and adapter layers have been writing all along; the
        aggregation and the template existed with no route to reach them.
        """
        from advtest_ext.dashboard.drift import build_drift_summary

        return templates.TemplateResponse(
            request, "drift.html", {"d": build_drift_summary(session)}
        )

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
            {
                "items": items,
                "status": status,
                "n_pending": count_hitl(session, status="pending"),
                "n_gold": count_gold_labels(session),
            },
        )

    @app.get("/hitl/{item_id}", response_class=HTMLResponse)
    async def hitl_review(
        request: Request,
        item_id: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        item = get_hitl(session, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="queue item not found")
        trace = get_trace(session, item.trace_id) if item.trace_id else None
        oracle = _oracle_snapshot(session, trace) if trace is not None else None
        signals = list_signals_for_trace(session, item.trace_id) if item.trace_id else []
        nxt = next_pending_hitl(session, exclude_id=item_id)
        import secrets

        reviewer_id = "reviewer-" + secrets.token_hex(4)
        return templates.TemplateResponse(
            request,
            "hitl_review.html",
            {
                "item": item,
                "trace": trace,
                "oracle": oracle,
                "signals": signals,
                "blind": _is_blind_review(item_id),
                "verdicts": _REVIEW_VERDICTS,
                "next_id": nxt.id if nxt else None,
                "reviewer_id": reviewer_id,
                "n_pending": count_hitl(session, status="pending"),
                "n_gold": count_gold_labels(session),
            },
        )

    @app.post("/hitl/{item_id}/review")
    async def hitl_submit_review(
        item_id: str,
        body: ReviewSubmission,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        if body.verdict not in _REVIEW_VERDICTS:
            raise HTTPException(
                status_code=422,
                detail=f"verdict must be one of {list(_REVIEW_VERDICTS)}",
            )
        item = get_hitl(session, item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="queue item not found")
        if item.status != "pending":
            raise HTTPException(status_code=409, detail="item already reviewed")
        trace = get_trace(session, item.trace_id) if item.trace_id else None
        if trace is None:
            raise HTTPException(status_code=400, detail="trace unavailable; cannot label")

        oracle = _oracle_snapshot(session, trace)
        blind = _is_blind_review(item_id)
        insert_gold_label(
            session,
            trace_id=trace.id,
            run_id=item.run_id,
            behavior_id=trace.behavior_id,
            oracle_verdict=oracle["verdict"],
            oracle_score=oracle["score"],
            oracle_layers_fired=oracle["layers"],
            response_snapshot=trace.raw_response,
            # The stratified slice that queued this trace (uniform is the
            # load-bearing slice for PPI's unbiased estimate).
            sample_reason=item.sample_reason or "uniform",
            expert_verdict=body.verdict,
            expert_confidence=body.confidence,
            reviewer_id=body.reviewer_id,
            review_seconds=body.review_seconds,
            reason=body.reason,
            blind=blind,
        )
        # confirmed = expert agreed with the oracle; rejected = overruled.
        agreed = body.verdict == oracle["verdict"]
        resolve_hitl(
            session,
            item_id=item_id,
            status="confirmed" if agreed else "rejected",
            resolution=f"expert={body.verdict} (oracle={oracle['verdict']})",
        )
        session.commit()

        nxt = next_pending_hitl(session, exclude_id=item_id)
        return JSONResponse(
            {
                "ok": True,
                "agreed": agreed,
                "next_id": nxt.id if nxt else None,
                "n_pending": count_hitl(session, status="pending"),
                "n_gold": count_gold_labels(session),
            }
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
        return templates.TemplateResponse(
            request,
            "seeds/show.html",
            {"seed": seed},
        )

    @app.get("/variants", response_class=HTMLResponse)
    async def variants_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        guide_id: str | None = None,
        status: str | None = None,
    ) -> Response:
        """Proven variants harvested from runs — winning trajectories parented to the
        guide they came from (not seeds, not new playbooks). Grouped by guide."""
        from advtest_ext.storage import list_guide_variants

        rows = list_guide_variants(session, guide_id=guide_id, status=status)
        by_guide: dict[str, list[dict[str, Any]]] = {}
        for v in rows:
            by_guide.setdefault(v.guide_id, []).append(
                {
                    "id": v.id,
                    "behavior_id": v.behavior_id,
                    "status": v.status,
                    "turns": len(v.seed_chain or []),
                    "signals": (v.provenance or {}).get("verified_signals", []),
                    "run_id": (v.provenance or {}).get("run_id", ""),
                    "created_at": v.created_at,
                }
            )
        return templates.TemplateResponse(
            request,
            "variants.html",
            {"by_guide": by_guide, "total": len(rows), "status": status},
        )

    @app.get("/playbooks", response_class=HTMLResponse)
    async def playbooks_index(request: Request) -> Response:
        """The attack playbooks — the file-based ``guides/`` corpus the LLM
        attacker runs, grounded in real seeder sessions. (Distinct from Recipes,
        which are DB seed-derived; a playbook is a normalized tap_crescendo
        steering doc.)"""
        from advtest_ext.guides.corpus import load_guides
        from advtest_ext.guides.models import display_title

        guides_dir = Path(__file__).resolve().parents[2] / "guides"
        guides = load_guides(guides_dir) if guides_dir.exists() else []
        items = [
            {
                "id": g.id,
                "title": display_title(g),
                "objective": g.objective,
                "surface": f"{g.target.app}/{g.target.surface}",
                "author": g.provenance.author or "—",
                "n_signals": len(g.success.signals),
                "n_steps": len(g.setup_steps),
                "tags": g.tags,
            }
            for g in guides
        ]
        return templates.TemplateResponse(request, "playbooks/index.html", {"playbooks": items})

    @app.get("/playbooks/{playbook_id}", response_class=HTMLResponse)
    async def playbooks_show(request: Request, playbook_id: str) -> Response:
        from advtest_ext.guides.corpus import load_guides
        from advtest_ext.guides.models import display_title

        guides_dir = Path(__file__).resolve().parents[2] / "guides"
        guides = load_guides(guides_dir) if guides_dir.exists() else []
        guide = next((x for x in guides if x.id == playbook_id), None)
        if guide is None:
            raise HTTPException(404, f"playbook {playbook_id!r} not found")

        # Map the guide onto the report's Playbook vocabulary (Table 2 / the
        # coverage map: attack class x technique x carrier surface x target
        # surface x effect).
        _app_display = {
            "google_sheets": "Sheets",
            "google_docs": "Docs",
            "gmail": "Gmail",
            "outlook": "Outlook",
        }
        roles: list[str] = []
        for r in [guide.target.account_role, *[s.account_role for s in guide.setup_steps]]:
            if r and r not in roles:
                roles.append(r)
        step_apps = [s.app for s in guide.setup_steps]
        if "gmail" in step_apps:
            carrier = "a Gmail message"
        elif any(s.operation == "set_document_body" for s in guide.setup_steps):
            carrier = "a Doc body"
        else:
            carrier = "the chat turn (direct)"
        taxonomy = {
            "attack_class": (guide.threat_class or "—").replace("_", " "),
            "carrier_surface": carrier,
            "target_surface": "Gemini in " + _app_display.get(guide.target.app, guide.target.app),
            "technique": list(guide.tags),
        }

        # Assemble the canonical Playbook object (report Table 2) as an
        # ordered list of (key, value, kind) rows the template renders as a
        # YAML-style config document. ``kind`` drives styling: scalar / list /
        # map / block. Everything here is real guide content mapped onto the
        # report's field names — no fabricated values.
        seed_chain = [{"n": i + 1, "text": t} for i, t in enumerate(guide.exemplar_turns)]
        steps = [
            {
                "n": i + 1,
                "op": f"{st.region}:{st.operation}",
                "account": st.account_role or "—",
                "intent": st.intent,
            }
            for i, st in enumerate(guide.setup_steps)
        ]
        signals = [
            {
                "kind": s.get("kind", "—"),
                "name": s.get("name", "—"),
                "match": s.get("pattern") or s.get("token") or s.get("canary") or "",
            }
            for s in guide.success.signals
        ]
        config = {
            "playbook_id": guide.id,
            "version": 1,
            "seed_ref": guide.provenance.source,
            "intent": (guide.threat_class or "—").replace("_", " "),
            "taxonomy": taxonomy,
            "accounts": roles,
            "steps": steps,
            "seed_chain": seed_chain,
            "signals": signals,
            "mutation_surface_allowed": list(guide.mutation_surface),
            "invariants": list(guide.invariants),
            "last_replay_succeeded_at": None,
            "rediscovered_count": 0,
        }
        return templates.TemplateResponse(
            request,
            "playbooks/show.html",
            {
                "g": guide,
                "title": display_title(guide),
                "accounts": roles,
                "taxonomy": taxonomy,
                "config": config,
            },
        )

    @app.get("/surfaces", response_class=HTMLResponse)
    async def surfaces_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Deployment-surface coverage: every distinct Gemini target under
        test, with real attempt / run / finding counts and an oracle-trust
        signature headlining the page. Rows open a slide-over with the
        surface's real detail."""
        ctx = build_surfaces_context(session)
        selected = service_context(request)["selected_service"]["key"]
        ctx["surface_groups"] = group_surfaces(session, ctx["surfaces"], selected)
        ctx["totals"]["surfaces"] = len(ctx["surface_groups"])
        ctx["totals"]["exposed"] = sum(
            group["exposure"] == "exposed" for group in ctx["surface_groups"]
        )
        return templates.TemplateResponse(request, "surfaces.html", ctx)

    @app.get("/surface-groups/{service}/{surface}/_panel", response_class=HTMLResponse)
    async def surface_group_panel(
        request: Request,
        service: str,
        surface: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        ctx = build_surfaces_context(session)
        selected = service_context(request)["selected_service"]["key"]
        groups = group_surfaces(session, ctx["surfaces"], selected)
        group = next(
            (item for item in groups if item["service"] == service and item["key"] == surface), None
        )
        if group is None:
            raise HTTPException(404, "Unknown workspace surface")
        from advtest_ext.storage.models import Run

        campaigns = (
            session.query(Run)
            .filter(Run.id.in_(group["campaign_ids"]))
            .order_by(Run.started_at.desc())
            .all()
        )
        return templates.TemplateResponse(
            request,
            "surfaces/_group_panel.html",
            {
                "surface": group,
                "campaigns": campaigns,
                "run_display_name": run_display_name,
            },
        )

    @app.get("/surfaces/{target_name}/_panel", response_class=HTMLResponse)
    async def surfaces_panel(
        request: Request,
        target_name: str,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        """Real slide-over detail for one surface — loaded by HTMX when a
        row is clicked. 404s for a surface name that has no traces."""
        ctx = build_surface_panel_context(session, target_name)
        if not ctx:
            raise HTTPException(404, f"surface {target_name!r} has no traces")
        return templates.TemplateResponse(request, "surfaces/_panel.html", ctx)

    @app.get("/references", response_class=HTMLResponse)
    async def references_index(request: Request) -> Response:
        from advtest_ext.dashboard.references import parse_bibfile

        return templates.TemplateResponse(request, "references.html", {"entries": parse_bibfile()})

    @app.get("/reports", response_class=HTMLResponse)
    async def reports_index(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
    ) -> Response:
        from advtest_ext.dashboard.presentation import surface_key
        from advtest_ext.storage.models import Finding as _Finding
        from advtest_ext.storage.models import Trace as _Trace

        runs = [run for run in list_runs(session, limit=200) if run.finished_at is not None]
        # Options for the report-builder modal: scenarios, workspaces, and runs
        # that actually have findings.
        scenarios = [(b, objective_label(b)) for b in list_finding_behavior_ids(session)]
        finding_targets = [
            t
            for (t,) in session.query(_Trace.target_name)
            .join(_Finding, _Finding.trace_id == _Trace.id)
            .distinct()
        ]
        workspaces = sorted(
            {(surface_key(t), surface_label(t)) for t in finding_targets if t},
            key=lambda x: x[1],
        )
        finding_runs = list_finding_run_ids(session)
        return templates.TemplateResponse(
            request,
            "reports.html",
            {
                "runs": runs,
                "run_display_name": run_display_name,
                "scenarios": scenarios,
                "workspaces": workspaces,
                "finding_runs": finding_runs,
            },
        )

    @app.get("/reports/view", response_class=HTMLResponse)
    async def reports_view(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        title: str | None = None,
        severity: list[str] | None = Query(default=None),  # noqa: B008
        behavior_id: list[str] | None = Query(default=None),  # noqa: B008
        workspace: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        run_id: str | None = None,
    ) -> Response:
        from advtest_ext.dashboard.report import (
            WEASYPRINT_INSTALLED,
            build_findings_report_context,
        )

        ctx = build_findings_report_context(
            session,
            title=title,
            severities=severity,
            behavior_ids=behavior_id,
            workspace=workspace,
            date_from=date_from,
            date_to=date_to,
            run_id=run_id,
        )
        ctx["pdf_available"] = WEASYPRINT_INSTALLED
        return templates.TemplateResponse(request, "reports/document.html", ctx)

    @app.get("/reports/view.pdf")
    async def reports_view_pdf(
        request: Request,
        session: Session = Depends(get_session),  # noqa: B008
        title: str | None = None,
        severity: list[str] | None = Query(default=None),  # noqa: B008
        behavior_id: list[str] | None = Query(default=None),  # noqa: B008
        workspace: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        run_id: str | None = None,
    ) -> Response:
        from advtest_ext.dashboard.report import (
            WEASYPRINT_INSTALLED,
            build_findings_report_context,
            render_pdf,
        )

        if not WEASYPRINT_INSTALLED:
            return PlainTextResponse(
                "PDF rendering requires the optional 'pdf' extra:\n"
                "    poetry install --extras pdf\n"
                "The HTML version is available at /reports/view.",
                status_code=501,
            )
        ctx = build_findings_report_context(
            session,
            title=title,
            severities=severity,
            behavior_ids=behavior_id,
            workspace=workspace,
            date_from=date_from,
            date_to=date_to,
            run_id=run_id,
        )
        ctx["pdf_available"] = True
        html = templates.get_template("reports/document.html").render(request=request, **ctx)
        pdf_bytes = render_pdf(html, base_url=str(_TEMPLATE_DIR))
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="fission-findings-report.pdf"'},
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_index(request: Request) -> Response:
        return templates.TemplateResponse(request, "settings.html", {})

    @app.get("/account", response_class=HTMLResponse)
    async def account_index(request: Request) -> Response:
        return templates.TemplateResponse(request, "account.html", {})

    return app


__all__ = ["create_app"]
