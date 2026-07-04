"""Storage: SQLAlchemy-backed run/trace/finding persistence + JSONL
backup + replay-bundle ZIPs.

* ``models`` — SQLAlchemy 2.0 ORM models (Run, Trace, Finding,
  HitlItem, JudgeScore). Postgres-compatible schema; SQLite default.
* ``database`` — engine + session factory.
* ``repository`` — insert / query helpers used by the orchestrator
  and the dashboard. Hides ``session.execute(select(...))``
  boilerplate.
* ``jsonl`` — append-only JSONL backup for grep-friendly access.
* ``replay`` — per-finding ZIP bundle (the
  reproducibility-infrastructure deliverable; FOUNDATIONS.md "open
  problems" #4).

Reproducibility infrastructure is one of our headline contributions —
every Finding ships a self-contained replay bundle.
"""

from advtest_ext.storage.artifacts import (
    ArtifactStore,
    GcsArtifactStore,
    LocalArtifactStore,
    artifact_store_from_env,
)
from advtest_ext.storage.database import (
    make_engine,
    make_session_factory,
    session_scope,
)
from advtest_ext.storage.jsonl import JsonlTraceWriter, read_jsonl_traces
from advtest_ext.storage.models import (
    Base,
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
from advtest_ext.storage.replay import build_replay_bundle, read_replay_bundle
from advtest_ext.storage.repository import (
    asr_heatmap,
    asr_heatmap_formatted,
    enqueue_hitl,
    finalize_run,
    get_finding,
    get_recipe,
    get_run,
    get_seed,
    get_trace,
    insert_recipe,
    insert_seed,
    insert_signal_records,
    insert_trace,
    list_finding_behavior_ids,
    list_finding_run_ids,
    list_findings,
    list_hitl,
    list_recipes,
    list_runs,
    list_seeds,
    list_signals_for_trace,
    list_traces_for_run,
    promote_finding,
    update_trace_verdict,
    upsert_run,
)

__citations__: list[str] = []  # original infrastructure work

__all__ = [
    "ArtifactStore",
    "Base",
    "Finding",
    "GcsArtifactStore",
    "HitlItem",
    "JsonlTraceWriter",
    "JudgeScore",
    "LocalArtifactStore",
    "RecipeRow",
    "RecipeStepRow",
    "Run",
    "SeedRow",
    "SignalRecord",
    "Trace",
    "artifact_store_from_env",
    "asr_heatmap",
    "asr_heatmap_formatted",
    "build_replay_bundle",
    "enqueue_hitl",
    "finalize_run",
    "get_finding",
    "get_recipe",
    "get_run",
    "get_seed",
    "get_trace",
    "insert_recipe",
    "insert_seed",
    "insert_signal_records",
    "insert_trace",
    "list_finding_behavior_ids",
    "list_finding_run_ids",
    "list_findings",
    "list_hitl",
    "list_recipes",
    "list_runs",
    "list_seeds",
    "list_signals_for_trace",
    "list_traces_for_run",
    "make_engine",
    "make_session_factory",
    "promote_finding",
    "read_jsonl_traces",
    "read_replay_bundle",
    "session_scope",
    "update_trace_verdict",
    "upsert_run",
]
