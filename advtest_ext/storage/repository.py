"""Repository: insert / query helpers over the storage models.

Wraps the SQLAlchemy session calls a) the orchestrator needs at
trace-completion time and b) the dashboard needs at request time.
Keeps both consumers off ``session.execute(select(...))``
boilerplate.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, desc, func, select

from advtest_ext.storage.models import (
    Finding,
    HitlItem,
    JudgeScore,
    RecipeRow,
    RecipeStepRow,
    Run,
    SeedRow,
    SignalRecord,
    Trace,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

    from advtest_ext.core.models import (
        AttackTrace as DomainTrace,
    )
    from advtest_ext.core.models import (
        RunConfig,
    )
    from advtest_ext.core.models import (
        SummaryStats as DomainSummaryStats,
    )


# ----------------------------------------------------------------------
# Inserts (orchestrator side)
# ----------------------------------------------------------------------


def upsert_run(
    session: Session,
    *,
    run_id: str,
    target_name: str,
    strategy: str,
    config: RunConfig | None = None,
    output_dir: str | None = None,
) -> Run:
    """Create or update the Run row. Idempotent so the orchestrator
    can call this at run-start (to lock in metadata even if the run
    crashes) AND at run-end (to write summary)."""
    run = session.get(Run, run_id)
    if run is None:
        run = Run(id=run_id, target_name=target_name, strategy=strategy)
        session.add(run)
    run.target_name = target_name
    run.strategy = strategy
    if config is not None:
        run.config = config.model_dump(mode="json")
    if output_dir is not None:
        run.output_dir = output_dir
    return run


def finalize_run(
    session: Session,
    *,
    run_id: str,
    summary: DomainSummaryStats,
) -> Run:
    """Stamp completion: ``finished_at`` + cached ``summary`` JSON."""
    run = session.get(Run, run_id)
    if run is None:
        raise ValueError(f"finalize_run: no Run row for {run_id!r}")
    run.finished_at = datetime.now(UTC)
    run.summary = summary.model_dump(mode="json")
    return run


def insert_trace(session: Session, *, run_id: str, trace: DomainTrace) -> Trace:
    """Persist a domain ``AttackTrace`` and its judge-score children.
    Hoisted ``parent_id`` and ``depth`` come straight from the domain
    object — the orchestrator already populated them.

    Idempotent on ``trace.id``: if a row already exists we return it
    unchanged. Important for ``advtest import-run`` (a re-import of
    the same on-disk run shouldn't violate the primary key) AND for
    a future at-least-once orchestrator delivery where a crash and
    restart could re-emit the same trace.
    """
    existing = session.get(Trace, trace.id)
    if existing is not None:
        return existing
    row = Trace(
        id=trace.id,
        run_id=run_id,
        timestamp=trace.timestamp,
        behavior_id=trace.behavior_id,
        attack_name=trace.payload.generator_name,
        technique_id=trace.technique.id if trace.technique else None,
        target_name=trace.target_name,
        payload_id=trace.payload.id,
        payload_text=trace.payload.text,
        raw_request=trace.raw_request,
        raw_response=trace.raw_response,
        raw_response_data=trace.raw_response_data,
        verdict=trace.verdict,
        parent_id=trace.parent_id,
        depth=trace.depth,
        extra_metadata=dict(trace.metadata),
    )
    session.add(row)
    for js in trace.judge_scores:
        session.add(
            JudgeScore(
                trace_id=trace.id,
                judge_name=js.judge_name,
                score=js.score,
                flagged=js.flagged,
                reasoning=js.reasoning,
                judge_version=js.judge_version,
            )
        )
    return row


def update_trace_verdict(session: Session, *, trace_id: str, verdict: str) -> None:
    """Update a persisted trace's verdict in place. Used by the
    orchestrator's layered-oracle upgrade: when a deterministic signal
    fires (ground truth that the attack landed), the trace verdict is
    promoted to 'success' even if the LLM judge scored it lower. No-op
    if the row doesn't exist."""
    row = session.get(Trace, trace_id)
    if row is not None:
        row.verdict = verdict


def insert_signal_records(
    session: Session,
    *,
    trace_id: str,
    records: list[dict[str, Any]],
) -> None:
    """Persist a batch of signal-evaluation records for one trace.

    Each entry in ``records`` is the orchestrator's
    SignalResult-derived dict: ``{name, kind, fired, reasoning,
    evidence}``. Decoupling the wire shape from the
    ``judges.signals.SignalResult`` class keeps the storage layer
    free of any import dependency on the signal classes — useful
    for replay-bundle re-ingestion where the original signal
    instances aren't reconstructed.

    Idempotency is not enforced here: the orchestrator only calls
    this once per trace at completion, and the signals table has
    a synthetic primary key so duplicate calls would append rather
    than collide.
    """
    for r in records:
        session.add(
            SignalRecord(
                trace_id=trace_id,
                name=r["name"],
                kind=r["kind"],
                fired=bool(r["fired"]),
                reasoning=r.get("reasoning", ""),
                evidence=dict(r.get("evidence") or {}),
            )
        )


def list_signals_for_trace(session: Session, trace_id: str) -> list[SignalRecord]:
    """Return every SignalRecord for ``trace_id``, ordered by
    insertion id so dashboards render signals in the order the
    orchestrator evaluated them."""
    return list(
        session.scalars(
            select(SignalRecord)
            .where(SignalRecord.trace_id == trace_id)
            .order_by(SignalRecord.id.asc())
        )
    )


def promote_finding(
    session: Session,
    *,
    run_id: str,
    trace_id: str,
    severity: str = "med",
    title: str = "",
    description: str = "",
    replay_bundle_path: str | None = None,
) -> Finding:
    """Promote an existing Trace to a Finding. Idempotent on
    ``trace_id`` — calling twice updates the existing row rather
    than failing the unique constraint."""
    finding = session.scalar(select(Finding).where(Finding.trace_id == trace_id))
    if finding is None:
        finding = Finding(
            id=f"finding-{uuid.uuid4().hex[:12]}",
            run_id=run_id,
            trace_id=trace_id,
        )
        session.add(finding)
    finding.severity = severity
    finding.title = title
    finding.description = description
    finding.replay_bundle_path = replay_bundle_path
    return finding


def enqueue_hitl(
    session: Session,
    *,
    run_id: str,
    trace_id: str | None,
    reason: str,
    disagreement: float,
) -> HitlItem:
    item = HitlItem(
        id=f"hitl-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        trace_id=trace_id,
        reason=reason,
        judge_disagreement=disagreement,
    )
    session.add(item)
    return item


# ----------------------------------------------------------------------
# Reads (dashboard side)
# ----------------------------------------------------------------------


def list_runs(session: Session, *, limit: int = 100) -> list[Run]:
    return list(session.scalars(select(Run).order_by(desc(Run.started_at)).limit(limit)))


def get_run(session: Session, run_id: str) -> Run | None:
    return session.get(Run, run_id)


def get_trace(session: Session, trace_id: str) -> Trace | None:
    """Look up a single :class:`Trace` by primary key. Used by the
    dashboard's ``/traces/{id}`` route — clicking a node on the
    attack-tree page lands here. Returns ``None`` for unknown ids
    so the route can render a clean 404 instead of an exception."""
    return session.get(Trace, trace_id)


def list_traces_for_run(session: Session, run_id: str, *, limit: int = 1000) -> list[Trace]:
    return list(
        session.scalars(
            select(Trace).where(Trace.run_id == run_id).order_by(Trace.timestamp.asc()).limit(limit)
        )
    )


def list_findings(
    session: Session,
    *,
    run_id: str | None = None,
    severity: str | None = None,
    behavior_id: str | None = None,
    limit: int = 200,
) -> list[Finding]:
    stmt = select(Finding).order_by(desc(Finding.promoted_at))
    if run_id is not None:
        stmt = stmt.where(Finding.run_id == run_id)
    if severity is not None:
        stmt = stmt.where(Finding.severity == severity)
    if behavior_id is not None:
        stmt = stmt.join(Trace, Finding.trace_id == Trace.id).where(
            Trace.behavior_id == behavior_id
        )
    return list(session.scalars(stmt.limit(limit)))


def list_finding_run_ids(session: Session) -> list[str]:
    """Distinct run_ids that have at least one finding. Ordered by
    most recent finding-promotion time so the dropdown surfaces the
    runs the user just looked at."""
    rows = session.execute(
        select(Finding.run_id, func.max(Finding.promoted_at).label("last"))
        .group_by(Finding.run_id)
        .order_by(desc("last"))
    ).all()
    return [r[0] for r in rows]


def list_finding_behavior_ids(
    session: Session,
    *,
    run_id: str | None = None,
) -> list[str]:
    """Distinct behavior_ids across findings (optionally scoped to a
    single run). Joins to Trace because behavior_id lives there."""
    stmt = (
        select(Trace.behavior_id)
        .join(Finding, Finding.trace_id == Trace.id)
        .group_by(Trace.behavior_id)
        .order_by(Trace.behavior_id.asc())
    )
    if run_id is not None:
        stmt = stmt.where(Finding.run_id == run_id)
    return [r for r in session.scalars(stmt)]


def get_finding(session: Session, finding_id: str) -> Finding | None:
    return session.get(Finding, finding_id)


# ----------------------------------------------------------------------
# Phase-1 (Fission integration) — Seed + Recipe + Step helpers.
# Insert / list / get only — orchestration logic stays elsewhere.
# Bidirectional mapping between the SQL rows and the Pydantic
# dataclasses (advtest_ext.core.seed.Seed,
# advtest_ext.core.recipe.Recipe) is intentionally explicit at
# the boundary rather than mixed into the ORM model.
# ----------------------------------------------------------------------


def insert_seed(session: Session, *, seed: Any) -> SeedRow:
    """Persist a :class:`advtest_ext.core.seed.Seed` as a SeedRow.
    Idempotent on ``seed.id`` — returns the existing row when
    re-inserting the same id."""
    existing = session.get(SeedRow, seed.id)
    if existing is not None:
        return existing
    row = SeedRow(
        id=seed.id,
        kind=seed.kind,
        title=seed.title,
        source_format=seed.source_format,
        raw_artefact_path=seed.raw_artefact_path,
        taxonomy_hints=list(seed.taxonomy_hints),
        submitted_by=seed.submitted_by,
        submitted_at=seed.submitted_at,
        payload=dict(seed.payload),
    )
    session.add(row)
    return row


def list_seeds(
    session: Session,
    *,
    kind: str | None = None,
    limit: int = 200,
) -> list[SeedRow]:
    """Browse the Seeds inbox. Optional kind filter for the
    dashboard's per-kind tabs."""
    stmt = select(SeedRow).order_by(desc(SeedRow.submitted_at))
    if kind is not None:
        stmt = stmt.where(SeedRow.kind == kind)
    return list(session.scalars(stmt.limit(limit)))


def get_seed(session: Session, seed_id: str) -> SeedRow | None:
    return session.get(SeedRow, seed_id)


def insert_recipe(session: Session, *, recipe: Any) -> RecipeRow:
    """Persist a :class:`advtest_ext.core.recipe.Recipe` and its
    Steps. Idempotent on ``recipe.id``.

    The Recipe's ``mutation_surface`` field is a Pydantic model;
    we ``.model_dump()`` it into a plain dict so the JSON column
    can round-trip it without reaching back into the Pydantic
    layer at read time."""
    existing = session.get(RecipeRow, recipe.id)
    if existing is not None:
        return existing
    row = RecipeRow(
        id=recipe.id,
        seed_id=recipe.seed_id,
        version=recipe.version,
        name=recipe.name,
        content_hash=recipe.content_hash,
        rediscovered_count=recipe.rediscovered_count,
        signals=list(recipe.signals),
        mutation_surface=recipe.mutation_surface.model_dump(),
        taxonomy=dict(recipe.taxonomy),
        created_at=recipe.created_at,
    )
    session.add(row)
    for position, step in enumerate(recipe.steps):
        session.add(
            RecipeStepRow(
                recipe_id=recipe.id,
                position=position,
                step_id=step.id,
                intent=step.intent,
                surface=step.surface.model_dump(),
                operation=step.operation,
                params=dict(step.params),
                success_signals=list(step.success_signals),
                on_fail=step.on_fail,
            )
        )
    return row


def list_recipes(
    session: Session,
    *,
    seed_id: str | None = None,
    draft_state: str | None = None,
    limit: int = 200,
) -> list[RecipeRow]:
    """Browse the Recipe catalogue. Optional filters for the
    dashboard's per-seed view and the operator-review queue."""
    stmt = select(RecipeRow).order_by(desc(RecipeRow.created_at))
    if seed_id is not None:
        stmt = stmt.where(RecipeRow.seed_id == seed_id)
    if draft_state is not None:
        stmt = stmt.where(RecipeRow.draft_state == draft_state)
    return list(session.scalars(stmt.limit(limit)))


def get_recipe(session: Session, recipe_id: str) -> RecipeRow | None:
    return session.get(RecipeRow, recipe_id)


def list_hitl(
    session: Session,
    *,
    status: str | None = "pending",
    limit: int = 200,
) -> list[HitlItem]:
    stmt = select(HitlItem).order_by(desc(HitlItem.enqueued_at))
    if status is not None:
        stmt = stmt.where(HitlItem.status == status)
    return list(session.scalars(stmt.limit(limit)))


def asr_heatmap(session: Session, run_id: str) -> dict[tuple[str, str], dict[str, float | int]]:
    """Aggregate (technique_id, behavior_id) → ``{asr, attempts,
    successes}``. The dashboard's headline heatmap renders straight
    from this; ``technique_id=NULL`` collapses to the literal string
    ``"-"`` so it shows up as a real row.

    Errors-verdict traces are excluded from the denominator
    (consistent with :func:`advtest_ext.core.orchestrator.summarise`).
    """
    success_int = case((Trace.verdict == "success", 1), else_=0)
    rows = session.execute(
        select(
            func.coalesce(Trace.technique_id, "-").label("tech"),
            Trace.behavior_id,
            func.count().label("attempts"),
            func.sum(success_int).label("successes"),
        )
        .where(Trace.run_id == run_id)
        .where(Trace.verdict != "error")
        .group_by("tech", Trace.behavior_id)
    ).all()
    out: dict[tuple[str, str], dict[str, float | int]] = {}
    for tech, behavior_id, attempts, successes in rows:
        succ = int(successes or 0)
        att = int(attempts or 0)
        out[(tech, behavior_id)] = {
            "attempts": att,
            "successes": succ,
            "asr": (succ / att) if att else 0.0,
        }
    return out


def _payload_family(behavior_id: str) -> str:
    """Bucket a Behavior id into a "family" label for the heatmap.

    Cheap heuristic: the behaviour-id prefix before the first
    underscore. Real catalogues land in 4-6 buckets (exfil_, override_,
    inject_, unauthorized_, leak_, fake_, malicious_), which matches
    BehaviorCategory's intent without forcing the dashboard to
    join against ``Behavior`` rows."""
    return behavior_id.split("_", 1)[0] if behavior_id else "?"


def asr_heatmap_formatted(session: Session, run_id: str) -> dict[str, object]:
    """Aggregate per-(technique, payload-family) ASR into a matrix
    shape the dashboard + PDF report templates iterate without
    re-keying. Rows = techniques, columns = payload families, cells =
    mean ASR weighted by attempts, plus the absolute counts for the
    cell tooltip.

    Shared between :mod:`advtest_ext.dashboard.app` and
    :mod:`advtest_ext.dashboard.report` so both views are guaranteed
    to bucket and aggregate identically.
    """
    raw = asr_heatmap(session, run_id)
    techniques = sorted({tech for (tech, _b) in raw})
    families = sorted({_payload_family(bid) for (_t, bid) in raw})
    aggregated: dict[tuple[str, str], dict[str, float | int]] = defaultdict(
        lambda: {"attempts": 0, "successes": 0, "asr": 0.0}
    )
    for (tech, bid), cell in raw.items():
        fam = _payload_family(bid)
        bucket = aggregated[(tech, fam)]
        bucket["attempts"] = int(bucket["attempts"]) + int(cell["attempts"])
        bucket["successes"] = int(bucket["successes"]) + int(cell["successes"])
    for bucket in aggregated.values():
        att = int(bucket["attempts"])
        bucket["asr"] = (int(bucket["successes"]) / att) if att else 0.0
    matrix: list[dict[str, object]] = []
    for tech in techniques:
        row_cells = []
        for fam in families:
            agg_cell = aggregated.get((tech, fam))
            row_cells.append(
                {
                    "family": fam,
                    "asr": (agg_cell["asr"] if agg_cell else 0.0),
                    "attempts": (agg_cell["attempts"] if agg_cell else 0),
                    "successes": (agg_cell["successes"] if agg_cell else 0),
                }
            )
        matrix.append({"technique": tech, "cells": row_cells})
    return {
        "techniques": techniques,
        "families": families,
        "rows": matrix,
        "is_empty": not aggregated,
    }


__all__ = [
    "asr_heatmap",
    "asr_heatmap_formatted",
    "enqueue_hitl",
    "finalize_run",
    "get_finding",
    "get_run",
    "insert_trace",
    "list_findings",
    "list_hitl",
    "list_runs",
    "list_traces_for_run",
    "promote_finding",
    "upsert_run",
]
