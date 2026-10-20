"""Persist a strategy-loop :class:`RunResult` into fission storage.

Writes one ``strategy_runs`` row plus one ``strategy_attempts`` row per
attacker iteration, wiring two lineage relations:

* **lineage_id** (per attempt) groups a variant family by its origin — the
  routed strategy id, or ``cold_start:<sample_key>`` for a from-scratch
  discovery. This is schema field 8 of the pilot: it lets a big family of
  related variants be traced to one seed and not over-counted.
* **parent_id** self-joins the refinement chain within a sample (iter N's
  parent is iter N-1), the same shape as ``Trace.parent_id``.

Reuses the existing engine/session wiring (SQLite by default, Postgres via
``ADVTEST_DB_URL``); the two tables are created by ``make_engine``'s
``create_all`` like every other table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.storage.models import StrategyAttempt, StrategyRun
from advtest_ext.strategy.models import Sample
from advtest_ext.strategy.orchestrator import RunResult, SampleOutcome

COLD_START_PREFIX = "cold_start:"


def _sample_ctx(sample: Sample) -> dict:
    """The sample fields the pilot exporter needs (carrier, goal, journey
    signals), stashed on each attempt row so the DB is self-sufficient for
    export without re-reading the source JSONL."""
    return {
        "suite_name": sample.suite_name,
        "user_task_prompt": sample.user_task_prompt,
        "injection_task_goal": sample.injection_task_goal,
        "context_with_placeholder": sample.context_with_placeholder,
        "injection_candidates": list(sample.injection_candidates),
    }


def lineage_id_for(outcome: SampleOutcome) -> str:
    """The family a sample's variants descend from: the primary routed
    strategy, or a cold-start lineage rooted at the sample."""
    for sid in outcome.strategy_ids:
        if not sid.startswith("_template"):  # skip the cold-start placeholder
            return sid
    return f"{COLD_START_PREFIX}{outcome.sample_key}"


def _rows_for_outcome(
    run_id: str, outcome: SampleOutcome, *, target_model: str, ctx: dict
) -> list[StrategyAttempt]:
    lineage = lineage_id_for(outcome)
    rows: list[StrategyAttempt] = []
    for att in outcome.attempts:
        rows.append(
            StrategyAttempt(
                run_id=run_id,
                sample_key=outcome.sample_key,
                lineage_id=lineage,
                strategy_ids=list(outcome.strategy_ids),
                status=outcome.status,
                iter=att.iter,
                analysis=att.analysis,
                injection=att.injection,
                utility=att.utility,
                security=att.security,
                final_output=att.final_output,
                target_model=target_model,
                extra_metadata=dict(ctx),
            )
        )
    return rows


def persist_run(
    result: RunResult,
    *,
    session: Session | None = None,
    db_url: str | None = None,
    target: str = "",
    target_model: str = "",
    threat_model: str = "black_box",
    library_dir: str | None = None,
    config: dict | None = None,
    run_id: str | None = None,
    samples: list[Sample] | None = None,
) -> str:
    """Persist ``result`` and return the new ``strategy_runs`` id.

    Pass an open ``session`` (e.g. in tests), or omit it to open one against
    ``db_url`` / ``ADVTEST_DB_URL`` / the default SQLite file.
    """
    run_id = run_id or f"strat-{uuid.uuid4().hex[:12]}"
    ctx_by_key = {s.sample_key: _sample_ctx(s) for s in (samples or [])}

    def _write(sess: Session) -> None:
        run = StrategyRun(
            id=run_id,
            finished_at=datetime.now(UTC),
            target=target,
            target_model=target_model,
            threat_model=threat_model,
            library_dir=library_dir,
            n_samples=len(result.outcomes),
            hits=result.hits,
            asr=result.asr,
            config=config or {},
            summary={
                "n_samples": len(result.outcomes),
                "hits": result.hits,
                "asr": result.asr,
                "n_attempts": sum(len(o.attempts) for o in result.outcomes),
                "lineages": sorted({lineage_id_for(o) for o in result.outcomes}),
            },
        )
        sess.add(run)
        sess.flush()  # run row must exist before FK'd attempts

        for outcome in result.outcomes:
            prev_id: int | None = None
            ctx = ctx_by_key.get(outcome.sample_key, {})
            for row in _rows_for_outcome(
                run_id, outcome, target_model=target_model, ctx=ctx
            ):
                row.parent_id = prev_id  # refinement chain within the sample
                sess.add(row)
                sess.flush()  # assign row.id so the next iter can point at it
                prev_id = row.id

    if session is not None:
        _write(session)
    else:
        engine = make_engine(db_url)
        with session_scope(make_session_factory(engine)) as sess:
            _write(sess)
    return run_id
