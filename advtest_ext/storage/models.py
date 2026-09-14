"""SQLAlchemy ORM models for advtest persistence.

Five tables back the run/finding lifecycle. Schema is intentionally
Postgres-compatible (no SQLite-only types, JSON column instead of
JSONB) so a dev SQLite database round-trips through ``alembic
upgrade`` to a production Postgres without column-type rewrites.

* :class:`Run` — one row per orchestrator run. Carries summary
  stats so the runs index page renders without a hot-path join.
* :class:`Trace` — one row per attack attempt. ``parent_id`` and
  ``depth`` are first-class columns (hoisted by the orchestrator
  from attack-namespace metadata) so chain reconstruction
  (PAIR / Crescendo / TAP) is a SQL self-join, not a metadata-blob
  parse.
* :class:`JudgeScore` — per-judge per-trace scores. Many-to-one with
  Trace; the dashboard aggregates these to plot per-judge agreement.
* :class:`Finding` — flagged outcomes promoted from Trace (typically
  ``verdict='success'`` or HITL-confirmed). Each ships a replay
  bundle artefact path so the bundle is locatable from a single SQL
  row.
* :class:`HitlItem` — judge-disagreement queue entries; the dashboard
  shows them on /hitl and lets a human resolve.

Reproducibility infrastructure (FOUNDATIONS.md "open problems" #4)
is one of our headline contributions — every Finding row points at
a self-contained replay bundle on disk, and the in-database state
is enough to render the dashboard plus the per-finding PDF report.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Common metadata anchor for all advtest tables."""


class Run(Base):
    """One orchestrator run: target, attacks, behaviours, budget,
    and aggregate summary stats.

    The ``output_dir`` column is the on-disk root for the run's
    artefacts (per-trace JSON, JSONL backup, replay bundles). It
    must stay populated even after a run finishes so a redeploy of
    the dashboard can still locate the artefacts.
    """

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    target_name: Mapped[str] = mapped_column(String(128))
    strategy: Mapped[str] = mapped_column(String(32))
    guide_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    """The guide/playbook this run was launched from, when any. Lets the
    authenticity view list only same-guide runs so cross-run comparisons
    are apples-to-apples. Null for ad-hoc config runs."""
    output_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Frozen RunConfig dump (target_name, attacks, behaviors,
    budget, judges, max_concurrency) for full reproducibility."""
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Cached SummaryStats — rendered straight onto /runs and
    /runs/{id} without re-aggregating. Populated at run completion."""

    traces: Mapped[list[Trace]] = relationship(back_populates="run", cascade="all, delete-orphan")
    findings: Mapped[list[Finding]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    hitl_items: Mapped[list[HitlItem]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class Trace(Base):
    """One attack attempt against the target. Every row is durable
    even if no judge ever flags it — the orchestrator records all
    attempts, not just successes, so the denominator of any ASR
    computation is reproducible from the database alone."""

    __tablename__ = "traces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    behavior_id: Mapped[str] = mapped_column(String(128), index=True)
    attack_name: Mapped[str] = mapped_column(String(64), index=True)
    technique_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    target_name: Mapped[str] = mapped_column(String(128))
    payload_id: Mapped[str] = mapped_column(String(128))
    payload_text: Mapped[str] = mapped_column(Text)
    raw_request: Mapped[str] = mapped_column(Text)
    raw_response: Mapped[str] = mapped_column(Text)
    raw_response_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    """First-class column for tree/chain reconstruction. Hoisted from
    attack-namespace metadata at orchestrator persistence time so the
    dashboard's chain view is a SQL self-join, not a metadata parse."""
    depth: Mapped[int] = mapped_column(Integer, default=0, index=True)
    """First-class column. 0 for single-step attacks or chain roots."""
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Free-form metadata blob (BoN augmentation chains, MSJ shot
    counts, attacker reasoning, etc.). Renamed away from ``metadata``
    to avoid colliding with SQLAlchemy's reserved attribute."""
    uncertainty: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    """Measurement-spine review-priority score in [0, 1]
    (``core.uncertainty``). Indexed so the stratified sampler can
    ``ORDER BY uncertainty DESC`` to pull the top-K disagreement slice.
    0.0 for runs with no signal stack / no judge score."""
    uncertainty_reason: Mapped[str] = mapped_column(Text, default="")
    """Human-readable axis that drove ``uncertainty`` (shown in the
    review UI so the expert sees *why* a trace was surfaced)."""

    run: Mapped[Run] = relationship(back_populates="traces")
    judge_scores: Mapped[list[JudgeScore]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )
    signals: Mapped[list[SignalRecord]] = relationship(
        back_populates="trace", cascade="all, delete-orphan"
    )
    finding: Mapped[Finding | None] = relationship(
        back_populates="trace", uselist=False, cascade="all, delete-orphan"
    )


class JudgeScore(Base):
    """One judge's verdict on one trace. Many-to-one with Trace.

    Modelled as a separate table (vs. JSON column on Trace) because
    the dashboard's pairwise judge-agreement view does GROUP BY
    judge_name and JOIN on trace_id; that's a clean SQL story when
    judge scores are real rows.
    """

    __tablename__ = "judge_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    judge_name: Mapped[str] = mapped_column(String(64), index=True)
    score: Mapped[float] = mapped_column(Float)
    flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    judge_version: Mapped[str] = mapped_column(String(64), default="unknown")

    trace: Mapped[Trace] = relationship(back_populates="judge_scores")


class SignalRecord(Base):
    """One signal evaluation persisted to storage. Phase 3.

    Each row records one Signal's verdict against one Trace:
    fired (yes/no), human-readable reasoning, and the evidence
    dict the Signal subclass produced (match counts, first match
    text, surface read, etc.).

    Distinct from :class:`JudgeScore` even when the signal is a
    :class:`LlmJudgeVerdict` wrapping an existing judge — both
    rows get written so the legacy ``/findings`` dashboard surfaces
    keep working unchanged.

    Naming note: the SQLAlchemy model is ``SignalRecord`` to avoid
    collision with the abstract ``Signal`` class in
    :mod:`advtest_ext.judges.signals.base`. Both names are clearer
    by context — code that talks to the DB sees ``SignalRecord``,
    code that evaluates a predicate sees ``Signal``.
    """

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.id", ondelete="CASCADE"), index=True
    )
    # The operator-supplied label. Multiple signals of the same
    # kind can be told apart by name on the dashboard.
    name: Mapped[str] = mapped_column(String(128), index=True)
    # The registry kind string — ``token_appears``, ``regex_match``,
    # ``canary_appears``, ``http_response_shape``, ``llm_judge_verdict``.
    kind: Mapped[str] = mapped_column(String(64), index=True)
    fired: Mapped[bool] = mapped_column(Boolean, index=True)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    # Whatever the signal subclass wanted to keep — match counts,
    # first-match preview, judge score / version (for
    # llm_judge_verdict), surface read, etc. Free-form by design.
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    trace: Mapped[Trace] = relationship(back_populates="signals")


class Finding(Base):
    """A flagged outcome promoted from a Trace. One Finding per Trace
    at most. Promotion criteria are policy-level (verdict='success',
    HITL-confirmed disagreement, manual marking from the dashboard);
    the storage layer just records the promotion + replay-bundle
    artefact path."""

    __tablename__ = "findings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.id", ondelete="CASCADE"), unique=True, index=True
    )
    severity: Mapped[str] = mapped_column(String(16), default="med", index=True)
    title: Mapped[str] = mapped_column(String(256), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    promoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    replay_bundle_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    run: Mapped[Run] = relationship(back_populates="findings")
    trace: Mapped[Trace] = relationship(back_populates="finding")


class HitlItem(Base):
    """Entry in the human-in-the-loop queue. Created by the judge
    layer when ensemble disagreement crosses ``disagreement_threshold``
    (see ``advtest_ext.judges.ensemble.EnsembleAsJudge``)."""

    __tablename__ = "hitl_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="CASCADE"), index=True
    )
    trace_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("traces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    enqueued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    reason: Mapped[str] = mapped_column(Text, default="")
    judge_disagreement: Mapped[float] = mapped_column(Float, default=0.0)
    sample_reason: Mapped[str] = mapped_column(String(32), default="", index=True)
    """Which stratified slice put this in the queue: ``uniform`` |
    ``disagreement`` (``core.sampling``). Empty for legacy/ensemble
    enqueues. Flows into the eventual ``GoldLabel.sample_reason`` so PPI
    can keep the uniform slice identifiable for its unbiased estimate."""
    uncertainty: Mapped[float] = mapped_column(Float, default=0.0)
    """The trace's review-priority score at enqueue time (``core.uncertainty``)."""
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    """``pending`` | ``confirmed`` | ``rejected``."""
    resolution: Mapped[str] = mapped_column(Text, default="")
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Run] = relationship(back_populates="hitl_items")


class GoldLabel(Base):
    """One immutable expert-labelled example — the measurement spine's
    ground truth (see ``docs/FISSION_MEASUREMENT_SPINE.md`` §2).

    Each reviewed trace becomes ONE row that serves double duty:
    calibration ground truth for the PPI metric (ASR ± CI) AND future
    training data for the flywheel's L1/L2. Two invariants make it
    trustworthy:

    * **Frozen predictor.** ``oracle_verdict`` / ``oracle_score`` /
      ``oracle_layers_fired`` capture what the oracle said *at sample
      time*. The flywheel changes the oracle between runs, so PPI must
      score each run with the predictor that actually labelled it —
      hence these are snapshotted here, never recomputed.
    * **Self-contained + permanent.** ``response_snapshot`` and
      ``behavior_id`` are denormalised so the label stays valid even if
      the source Trace is later mutated, and ``trace_id`` / ``run_id``
      are ``ON DELETE SET NULL`` (not CASCADE) so deleting a run never
      destroys the ground truth it produced. A gold label outlives its
      source by design.

    ``sample_reason`` records *why* the trace was drawn for review
    (``disagreement`` vs ``uniform``); PPI needs the uniform slice to
    stay identifiable to make an unbiased population estimate.
    """

    __tablename__ = "gold_labels"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("traces.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    behavior_id: Mapped[str] = mapped_column(String(128), index=True)
    """Denormalised from the source Trace for per-behaviour calibration
    and so the label survives the Trace being deleted."""

    # --- Oracle prediction, frozen at sample time ---
    oracle_verdict: Mapped[str] = mapped_column(String(32))
    """success | partial | refused | error - what L0-L2 said when sampled."""
    oracle_score: Mapped[float] = mapped_column(Float, default=0.0)
    oracle_layers_fired: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Which signals / judge fired (for the frozen L0↔expert audit that
    catches reward-hacking when L1/L2 later train)."""
    response_snapshot: Mapped[str] = mapped_column(Text, default="")
    """The exact scored text, frozen — the label stays valid even if the
    Trace's ``raw_response`` is later overwritten."""

    # --- Sampling provenance (load-bearing for unbiased PPI) ---
    sample_reason: Mapped[str] = mapped_column(String(32), index=True)
    """disagreement | uniform. The uniform slice is what makes the ASR
    estimate an honest population claim."""

    # --- Expert label (the ground truth) ---
    expert_verdict: Mapped[str] = mapped_column(String(32), index=True)
    """success | partial | refused — the human's decision."""
    expert_confidence: Mapped[int] = mapped_column(Integer, default=3)
    """1-5; down-weights shaky labels."""
    reviewer_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    """For inter-rater agreement (κ) and accountability."""
    review_seconds: Mapped[int] = mapped_column(Integer, default=0)
    """Tracks the ≤30s review budget."""
    reason: Mapped[str] = mapped_column(Text, default="")
    """Why the expert confirmed/overruled — sharpens judge prompts later."""
    blind: Mapped[bool] = mapped_column(Boolean, default=False)
    """True when the oracle verdict was hidden during review (the
    anchoring-bias guard — compare blind vs pre-labelled override rates)."""

    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    frozen: Mapped[bool] = mapped_column(Boolean, default=True)
    """Immutable once reviewed (audit integrity)."""


class CalibrationExample(Base):
    """One labelled example from a PUBLIC adversarial benchmark, used as
    the oracle's cold-start calibration/validation set
    (``docs/FISSION_MEASUREMENT_SPINE.md`` §7, refinement 2).

    Distinct from :class:`GoldLabel`: a gold label is an in-house trace
    an expert reviewed (it carries a frozen *oracle prediction* to
    compare against), whereas a calibration example is an *external*
    input with a known ground-truth verdict but no oracle prediction
    yet — you run your oracle over it to measure agreement (F1 / ECE)
    before you have spent any in-house labelling budget.

    ``expected_verdict`` is the source's ground-truth label mapped onto
    Fission's verdict vocabulary; ``label_raw`` keeps the original value
    for audit, and ``label_meta`` carries source-specific context
    (guard, level, target model) so slices can be calibrated per stratum.
    """

    __tablename__ = "calibration_examples"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    dataset: Mapped[str] = mapped_column(String(64), index=True)
    """Registry slug (configs/calibration_datasets.yaml), e.g. ``lakera_gandalf_rct``."""
    attack_type: Mapped[str] = mapped_column(String(32), index=True)
    """direct_pi | indirect_pi | jailbreak | evasion."""
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    """Row id in the source dataset, when it has one (for dedup/traceback)."""
    input_text: Mapped[str] = mapped_column(Text)
    """The adversarial prompt / injected content the oracle will score."""
    response_text: Mapped[str] = mapped_column(Text, default="")
    """The model response, when the source ships one (e.g. JailbreakBench)."""
    expected_verdict: Mapped[str] = mapped_column(String(32), index=True)
    """Ground truth mapped to success | refused | partial."""
    label_raw: Mapped[str] = mapped_column(String(128), default="")
    """The original source label value, kept verbatim for audit."""
    label_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """Source-specific strata (guard, level, target model, ...)."""
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


class CalibrationResult(Base):
    """One oracle prediction over a :class:`CalibrationExample`, written by
    the offline calibration runner (``scripts/run_calibration.py``).

    Persisting predictions (rather than re-running the LLM judge on every
    dashboard load) lets the Calibration view render F1 / ECE from stored
    rows. ``batch`` groups a single run of the runner so successive
    oracle versions can be compared. ``correct`` is denormalised from
    ``predicted_verdict`` vs the example's ground truth for fast rollups.
    """

    __tablename__ = "calibration_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    example_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("calibration_examples.id", ondelete="CASCADE"), index=True
    )
    batch: Mapped[str] = mapped_column(String(64), default="", index=True)
    judge_name: Mapped[str] = mapped_column(String(64), index=True)
    judge_version: Mapped[str] = mapped_column(String(64), default="")
    predicted_verdict: Mapped[str] = mapped_column(String(32), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    """The judge's P(success) in [0, 1] — feeds the calibration (ECE) curve."""
    correct: Mapped[bool] = mapped_column(Boolean, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


# ----------------------------------------------------------------------
# Phase-1 data shapes for the Fission-integration plan. SeedRow,
# RecipeRow, RecipeStepRow back the in-flight Seed / Recipe / Step
# dataclasses from ``advtest_ext.core.{seed,recipe}``. SQLAlchemy
# class names suffix ``Row`` to avoid colliding with the dataclass
# names — repository helpers handle the bidirectional mapping.
#
# Tables are entirely additive. Existing Run / Trace / Finding
# rows have no dependency on these; configs that don't ingest
# Seeds simply leave the new tables empty.
# ----------------------------------------------------------------------


class SeedRow(Base):
    """One ingested Seed (human-chat or dataset-entry) awaiting or
    backing one Recipe. See :class:`advtest_ext.core.seed.Seed`."""

    __tablename__ = "seeds"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(512))
    source_format: Mapped[str] = mapped_column(String(64), index=True)
    raw_artefact_path: Mapped[str] = mapped_column(String(1024))
    taxonomy_hints: Mapped[list[str]] = mapped_column(JSON, default=list)
    submitted_by: Mapped[str] = mapped_column(String(128), index=True)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    recipes: Mapped[list[RecipeRow]] = relationship(
        back_populates="seed", cascade="all, delete-orphan"
    )


class RecipeRow(Base):
    """One Recipe in the catalogue. See
    :class:`advtest_ext.core.recipe.Recipe`. Storage-row name is
    ``RecipeRow`` to disambiguate from the dataclass.

    Uniqueness on ``content_hash`` is enforced at insert time by
    repository helpers, not by a DB-level unique constraint —
    Recipes within the same lineage (different ``version`` of the
    same seed-derived Recipe) intentionally share a content_hash
    when they're identical; the version differentiates them."""

    __tablename__ = "recipes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seed_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("seeds.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(512))
    content_hash: Mapped[str] = mapped_column(String(128), index=True)
    rediscovered_count: Mapped[int] = mapped_column(Integer, default=0)
    signals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    mutation_surface: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    taxonomy: Mapped[dict[str, list[str]]] = mapped_column(JSON, default=dict)
    # ``draft`` | ``approved`` | ``rejected`` — operator-review
    # gate from D5 of the Fission spec. Default ``approved`` so
    # mechanically-built Recipes (dataset-entry-derived) don't
    # require review friction; the Extractor sets ``draft`` for
    # LLM-produced human-chat Recipes.
    draft_state: Mapped[str] = mapped_column(String(16), default="approved", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)

    seed: Mapped[SeedRow] = relationship(back_populates="recipes")
    steps: Mapped[list[RecipeStepRow]] = relationship(
        back_populates="recipe",
        cascade="all, delete-orphan",
        order_by="RecipeStepRow.position",
    )


class RecipeStepRow(Base):
    """One Step within a Recipe. Position ordering is explicit so
    Steps re-load in their declared sequence regardless of insert
    order. See :class:`advtest_ext.core.recipe.Step`."""

    __tablename__ = "recipe_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("recipes.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    step_id: Mapped[str] = mapped_column(String(128))
    intent: Mapped[str] = mapped_column(Text)
    surface: Mapped[dict[str, Any]] = mapped_column(JSON)
    operation: Mapped[str] = mapped_column(String(128), index=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    success_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    on_fail: Mapped[str] = mapped_column(String(32), default="abort")

    recipe: Mapped[RecipeRow] = relationship(back_populates="steps")


class GuideVariant(Base):
    """A proven winning variant of a guide, harvested from a real run.

    Not a seed (raw inbox) and not a new top-level guide (corpus clutter): a
    variant is parented to the guide it descends from (``guide_id``) and carries
    the winning trajectory as its seed chain, so it is directly replayable while
    keeping its lineage. Deduped by ``id`` (a content hash of the trajectory).
    ``status``: candidate → verified_by_reproduction → canonical.
    """

    __tablename__ = "guide_variants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    guide_id: Mapped[str] = mapped_column(String(128), index=True)
    behavior_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(48), default="candidate", index=True)
    seed_chain: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    """The winning turns [{n, attacker, target}] — replayed as the variant's opening moves."""
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    """run_id, trace_id, verified_signals, worked_on, reverify_* — audit trail."""
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utc_now)


__all__ = [
    "Base",
    "CalibrationExample",
    "CalibrationResult",
    "Finding",
    "GoldLabel",
    "GuideVariant",
    "HitlItem",
    "JudgeScore",
    "RecipeRow",
    "RecipeStepRow",
    "Run",
    "SeedRow",
    "SignalRecord",
    "Trace",
]
