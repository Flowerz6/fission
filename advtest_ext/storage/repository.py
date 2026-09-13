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
    CalibrationExample,
    CalibrationResult,
    Finding,
    GoldLabel,
    HitlItem,
    JudgeScore,
    RecipeRow,
    RecipeStepRow,
    Run,
    SeedRow,
    SignalRecord,
    Trace,
)

# Allowed enum values, validated at the insert boundary (fail loudly
# rather than let a typo'd verdict silently corrupt the gold set).
_VERDICTS = frozenset({"success", "partial", "refused", "error"})
_SAMPLE_REASONS = frozenset({"disagreement", "uniform"})

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


def update_trace_uncertainty(
    session: Session,
    *,
    trace_id: str,
    uncertainty: float,
    reason: str = "",
) -> None:
    """Persist the measurement-spine review-priority score onto an
    already-inserted trace row (computed after the signal fan-out, so
    it lands as a follow-up UPDATE like the verdict upgrade). No-op if
    the row doesn't exist."""
    row = session.get(Trace, trace_id)
    if row is not None:
        row.uncertainty = uncertainty
        row.uncertainty_reason = reason


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


def delete_finding_for_trace(session: Session, *, trace_id: str) -> bool:
    """Drop the Finding promoted from ``trace_id``, if any. Returns True
    when a row was removed. Used by the re-score path to retract a stale
    Finding whose trace no longer grades as ``success`` (e.g. a
    loose-regex-only win re-graded to ``partial``), so the Findings table
    never keeps a promotion the current verdict no longer supports."""
    finding = session.scalar(select(Finding).where(Finding.trace_id == trace_id))
    if finding is None:
        return False
    session.delete(finding)
    return True


def enqueue_hitl(
    session: Session,
    *,
    run_id: str,
    trace_id: str | None,
    reason: str,
    disagreement: float,
    sample_reason: str = "",
    uncertainty: float = 0.0,
) -> HitlItem:
    # Idempotent on (run_id, trace_id) while an item is still PENDING.
    # Re-scoring a run is a normal, repeatable operation, and without
    # this each pass stacked another copy of the same trace onto the
    # queue — five re-scores meant a reviewer met the same trace five
    # times. The compounding harm is in the gold set rather than the
    # wasted clicks: repeat labels on one trace look like independent
    # raters to the agreement maths, so the human ceiling ends up
    # measuring one person agreeing with themselves.
    #
    # Resolved items are deliberately NOT deduplicated against: once a
    # trace has been reviewed, re-queueing it is a real request for a
    # second opinion (which is how the ceiling gets computed at all).
    existing = session.scalars(
        select(HitlItem)
        .where(
            HitlItem.run_id == run_id,
            HitlItem.trace_id == trace_id,
            HitlItem.status == "pending",
        )
        .limit(1)
    ).first()
    if existing is not None:
        # Refresh the sampling metadata — the newest pass has the most
        # current uncertainty score — but do not mint a second row.
        existing.reason = reason
        existing.judge_disagreement = disagreement
        existing.sample_reason = sample_reason or existing.sample_reason
        existing.uncertainty = uncertainty
        return existing

    item = HitlItem(
        id=f"hitl-{uuid.uuid4().hex[:12]}",
        run_id=run_id,
        trace_id=trace_id,
        reason=reason,
        judge_disagreement=disagreement,
        sample_reason=sample_reason,
        uncertainty=uncertainty,
    )
    session.add(item)
    return item


# ----------------------------------------------------------------------
# Reads (dashboard side)
# ----------------------------------------------------------------------


def list_runs(session: Session, *, limit: int = 100, guide_id: str | None = None) -> list[Run]:
    stmt = select(Run).order_by(desc(Run.started_at))
    if guide_id is not None:
        stmt = stmt.where(Run.guide_id == guide_id)
    return list(session.scalars(stmt.limit(limit)))


def set_run_guide(session: Session, *, run_id: str, guide_id: str) -> None:
    """Tag a Run with the guide it was launched from. No-op if the row is
    missing. Idempotent — overwrites with the same value on a re-run."""
    run = session.get(Run, run_id)
    if run is not None:
        run.guide_id = guide_id


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


def insert_gold_label(
    session: Session,
    *,
    trace_id: str | None,
    run_id: str | None,
    behavior_id: str,
    oracle_verdict: str,
    expert_verdict: str,
    sample_reason: str,
    oracle_score: float = 0.0,
    oracle_layers_fired: dict[str, Any] | None = None,
    response_snapshot: str = "",
    expert_confidence: int = 3,
    reviewer_id: str = "",
    review_seconds: int = 0,
    reason: str = "",
    blind: bool = False,
) -> GoldLabel:
    """Record one immutable expert-labelled example — the measurement
    spine's ground truth (``docs/FISSION_MEASUREMENT_SPINE.md`` §2).

    Validates the verdict/sample-reason enums and clamps
    ``expert_confidence`` to 1-5 so a bad label can't silently corrupt
    the gold set the PPI metric and flywheel later trust. Unlike most
    inserts here this is intentionally NOT idempotent on a natural key:
    a fresh id is minted per call so re-reviewing a trace (e.g. a
    double-labelled trace for inter-rater κ) yields a distinct row.
    """
    if oracle_verdict not in _VERDICTS:
        raise ValueError(
            f"oracle_verdict must be one of {sorted(_VERDICTS)}, got {oracle_verdict!r}"
        )
    if expert_verdict not in _VERDICTS:
        raise ValueError(
            f"expert_verdict must be one of {sorted(_VERDICTS)}, got {expert_verdict!r}"
        )
    if sample_reason not in _SAMPLE_REASONS:
        raise ValueError(
            f"sample_reason must be one of {sorted(_SAMPLE_REASONS)}, got {sample_reason!r}"
        )
    label = GoldLabel(
        id=f"gold-{uuid.uuid4().hex[:12]}",
        trace_id=trace_id,
        run_id=run_id,
        behavior_id=behavior_id,
        oracle_verdict=oracle_verdict,
        oracle_score=oracle_score,
        oracle_layers_fired=dict(oracle_layers_fired or {}),
        response_snapshot=response_snapshot,
        sample_reason=sample_reason,
        expert_verdict=expert_verdict,
        expert_confidence=max(1, min(5, expert_confidence)),
        reviewer_id=reviewer_id,
        review_seconds=review_seconds,
        reason=reason,
        blind=blind,
    )
    session.add(label)
    return label


def list_gold_labels(
    session: Session,
    *,
    run_id: str | None = None,
    sample_reason: str | None = None,
    limit: int = 1000,
) -> list[GoldLabel]:
    """Read the gold set, newest first. Optional filters scope to one
    run or one sampling slice (``uniform`` is the slice PPI uses for the
    unbiased population estimate)."""
    stmt = select(GoldLabel).order_by(desc(GoldLabel.reviewed_at))
    if run_id is not None:
        stmt = stmt.where(GoldLabel.run_id == run_id)
    if sample_reason is not None:
        stmt = stmt.where(GoldLabel.sample_reason == sample_reason)
    return list(session.scalars(stmt.limit(limit)))


def get_gold_label(session: Session, gold_label_id: str) -> GoldLabel | None:
    return session.get(GoldLabel, gold_label_id)


def gold_label_oracle_expert_pairs(
    session: Session,
    run_id: str,
    *,
    sample_reason: str | None = "uniform",
) -> list[tuple[str, str]]:
    """``(oracle_verdict, expert_verdict)`` pairs for a run's gold labels,
    for the PPI rectifier. Defaults to the ``uniform`` slice — the only
    one that is a genuine simple random sample, hence the only one that
    keeps the ASR estimate unbiased (``sample_reason=None`` returns all)."""
    stmt = select(GoldLabel.oracle_verdict, GoldLabel.expert_verdict).where(
        GoldLabel.run_id == run_id
    )
    if sample_reason is not None:
        stmt = stmt.where(GoldLabel.sample_reason == sample_reason)
    return [(o, e) for (o, e) in session.execute(stmt).all()]


def gold_label_rater_panels(
    session: Session,
    *,
    run_id: str | None = None,
) -> list[tuple[str, str, list[str]]]:
    """``(trace_id, oracle_verdict, [expert_verdict, ...])`` per reviewed
    trace — the shape ``core.agreement`` needs.

    A trace reviewed by two labellers has TWO ``GoldLabel`` rows (the
    insert mints a fresh id per call rather than upserting on trace_id,
    precisely so independent reviews both survive). Grouping them here
    is what makes the human-ceiling alpha computable: each trace yields one
    consensus item plus one multi-rater panel.

    The oracle verdict is taken from the first row of each group; every
    row for a trace snapshots the SAME frozen prediction, since the
    prediction is captured at sample time and never recomputed.

    Labels whose ``trace_id`` is NULL (the source trace was deleted —
    gold labels outlive their traces by design) are grouped under their
    own label id so they still contribute to κ, just never to the
    ceiling.

    **At most one label per (trace, reviewer) reaches the panel**, newest
    kept. The human ceiling is only meaningful as agreement between
    DIFFERENT people; counting one reviewer's repeat passes as separate
    raters measures self-consistency, which is far higher than
    inter-rater agreement and would inflate the ceiling the oracle is
    scored against. Labels with no ``reviewer_id`` cannot be attributed,
    so they collapse to a single anonymous rater — conservative by
    construction, since that can only ever lower the ceiling.
    """
    stmt = select(
        GoldLabel.id,
        GoldLabel.trace_id,
        GoldLabel.oracle_verdict,
        GoldLabel.expert_verdict,
        GoldLabel.reviewer_id,
    ).order_by(GoldLabel.reviewed_at.asc())
    if run_id is not None:
        stmt = stmt.where(GoldLabel.run_id == run_id)

    # (trace key) -> (oracle verdict, {reviewer -> latest verdict})
    grouped: dict[str, tuple[str, dict[str, str]]] = {}
    for label_id, trace_id, oracle_verdict, expert_verdict, reviewer_id in session.execute(
        stmt
    ).all():
        key = trace_id or f"orphan:{label_id}"
        if key not in grouped:
            grouped[key] = (oracle_verdict, {})
        # Ascending reviewed_at means a later pass overwrites an earlier
        # one for the same reviewer — a correction, not a second vote.
        grouped[key][1][reviewer_id or ""] = expert_verdict
    return [(k, o, list(by_reviewer.values())) for k, (o, by_reviewer) in grouped.items()]


def run_oracle_asr_counts(session: Session, run_id: str) -> tuple[int, int]:
    """``(n_scorable, n_success)`` for a run: the oracle's ASR numerator
    and denominator over all non-``error`` traces (the same denominator
    the orchestrator's summary uses)."""
    scorable = case((Trace.verdict != "error", 1), else_=0)
    success = case((Trace.verdict == "success", 1), else_=0)
    row = session.execute(
        select(func.coalesce(func.sum(scorable), 0), func.coalesce(func.sum(success), 0)).where(
            Trace.run_id == run_id
        )
    ).one()
    return int(row[0]), int(row[1])


def run_partial_count(session: Session, run_id: str) -> int:
    """How many of a run's traces graded ``partial`` — the model engaged
    or leaked corroborating detail without a proven full landing. Surfaced
    on the overview so partials are visible rather than folded silently
    into the non-success denominator of the ASR."""
    return int(
        session.scalar(
            select(func.count()).where(Trace.run_id == run_id, Trace.verdict == "partial")
        )
        or 0
    )


def count_gold_labels(session: Session, *, run_id: str | None = None) -> int:
    """How many labels exist (optionally per run). Backs the Phase-0
    exit signal — "the gold set is growing"."""
    stmt = select(func.count()).select_from(GoldLabel)
    if run_id is not None:
        stmt = stmt.where(GoldLabel.run_id == run_id)
    return int(session.scalar(stmt) or 0)


def insert_calibration_example(
    session: Session,
    *,
    id: str,
    dataset: str,
    attack_type: str,
    input_text: str,
    expected_verdict: str,
    external_id: str | None = None,
    response_text: str = "",
    label_raw: str = "",
    label_meta: dict[str, Any] | None = None,
) -> CalibrationExample:
    """Insert one external calibration example. Validates the verdict
    against the shared vocabulary and is idempotent on ``id`` (a stable
    id derived from ``dataset`` + ``external_id``/content hash lets a
    re-ingest of the same dump update rather than duplicate)."""
    if expected_verdict not in _VERDICTS:
        raise ValueError(
            f"expected_verdict must be one of {sorted(_VERDICTS)}, got {expected_verdict!r}"
        )
    existing = session.get(CalibrationExample, id)
    if existing is not None:
        return existing
    row = CalibrationExample(
        id=id,
        dataset=dataset,
        attack_type=attack_type,
        external_id=external_id,
        input_text=input_text,
        response_text=response_text,
        expected_verdict=expected_verdict,
        label_raw=label_raw,
        label_meta=dict(label_meta or {}),
    )
    session.add(row)
    return row


def list_calibration_examples(
    session: Session,
    *,
    dataset: str | None = None,
    attack_type: str | None = None,
    limit: int = 5000,
) -> list[CalibrationExample]:
    """Read the calibration set, optionally scoped to one dataset or
    attack type (for per-stratum calibration)."""
    stmt = select(CalibrationExample).order_by(CalibrationExample.id.asc())
    if dataset is not None:
        stmt = stmt.where(CalibrationExample.dataset == dataset)
    if attack_type is not None:
        stmt = stmt.where(CalibrationExample.attack_type == attack_type)
    return list(session.scalars(stmt.limit(limit)))


def count_calibration_examples(session: Session, *, dataset: str | None = None) -> int:
    stmt = select(func.count()).select_from(CalibrationExample)
    if dataset is not None:
        stmt = stmt.where(CalibrationExample.dataset == dataset)
    return int(session.scalar(stmt) or 0)


def insert_calibration_result(
    session: Session,
    *,
    id: str,
    example_id: str,
    judge_name: str,
    predicted_verdict: str,
    score: float,
    correct: bool,
    judge_version: str = "",
    batch: str = "",
) -> CalibrationResult:
    """Persist one oracle prediction over a calibration example. Idempotent
    on ``id`` so a re-run of the same batch updates rather than duplicates."""
    existing = session.get(CalibrationResult, id)
    if existing is not None:
        return existing
    row = CalibrationResult(
        id=id,
        example_id=example_id,
        judge_name=judge_name,
        judge_version=judge_version,
        predicted_verdict=predicted_verdict,
        score=score,
        correct=correct,
        batch=batch,
    )
    session.add(row)
    return row


def calibration_predictions(
    session: Session,
    *,
    batch: str | None = None,
) -> list[tuple[str, str, float, str, str]]:
    """Join stored oracle predictions to their example ground truth for the
    calibration report. Returns ``(expected, predicted, score, dataset,
    attack_type)`` tuples — the exact shape ``core.calibration_report``
    consumes. Uses the most recent batch when ``batch`` is None."""
    if batch is None:
        batch = session.scalar(
            select(CalibrationResult.batch).order_by(desc(CalibrationResult.created_at)).limit(1)
        )
        if batch is None:
            return []
    rows = session.execute(
        select(
            CalibrationExample.expected_verdict,
            CalibrationResult.predicted_verdict,
            CalibrationResult.score,
            CalibrationExample.dataset,
            CalibrationExample.attack_type,
        )
        .join(CalibrationResult, CalibrationResult.example_id == CalibrationExample.id)
        .where(CalibrationResult.batch == batch)
    ).all()
    return [(e, p, float(s), d, a) for (e, p, s, d, a) in rows]


def list_calibration_batches(session: Session) -> list[str]:
    """Distinct batch ids, most recent first."""
    rows = session.execute(
        select(CalibrationResult.batch, func.max(CalibrationResult.created_at).label("last"))
        .group_by(CalibrationResult.batch)
        .order_by(desc("last"))
    ).all()
    return [r[0] for r in rows]


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


def get_hitl(session: Session, item_id: str) -> HitlItem | None:
    return session.get(HitlItem, item_id)


def count_hitl(session: Session, *, status: str | None = "pending") -> int:
    stmt = select(func.count()).select_from(HitlItem)
    if status is not None:
        stmt = stmt.where(HitlItem.status == status)
    return int(session.scalar(stmt) or 0)


def next_pending_hitl(session: Session, *, exclude_id: str | None = None) -> HitlItem | None:
    """The oldest still-pending queue item (optionally excluding one id),
    so the review UI can chain straight to the next trace for a fast
    keyboard-driven pass."""
    stmt = select(HitlItem).where(HitlItem.status == "pending").order_by(HitlItem.enqueued_at.asc())
    if exclude_id is not None:
        stmt = stmt.where(HitlItem.id != exclude_id)
    return session.scalars(stmt.limit(1)).first()


def resolve_hitl(
    session: Session,
    *,
    item_id: str,
    status: str,
    resolution: str = "",
) -> HitlItem | None:
    """Mark a queue item reviewed. ``status`` is ``confirmed`` (expert
    agreed with the oracle) or ``rejected`` (expert overruled it). No-op
    returning None when the id is unknown."""
    item = session.get(HitlItem, item_id)
    if item is None:
        return None
    item.status = status
    item.resolution = resolution
    item.resolved_at = datetime.now(UTC)
    return item


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
    "calibration_predictions",
    "count_calibration_examples",
    "count_gold_labels",
    "count_hitl",
    "enqueue_hitl",
    "finalize_run",
    "get_finding",
    "get_gold_label",
    "get_hitl",
    "get_run",
    "gold_label_oracle_expert_pairs",
    "insert_calibration_example",
    "insert_calibration_result",
    "insert_gold_label",
    "insert_trace",
    "list_calibration_batches",
    "list_calibration_examples",
    "list_findings",
    "list_gold_labels",
    "list_hitl",
    "list_runs",
    "list_traces_for_run",
    "next_pending_hitl",
    "promote_finding",
    "resolve_hitl",
    "run_oracle_asr_counts",
    "update_trace_uncertainty",
    "upsert_run",
]
