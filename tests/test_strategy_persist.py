"""Persistence + lineage for strategy-loop runs.

All against an in-memory SQLite engine (the shared storage wiring) — no
external DB. Asserts the run/attempt rows, the lineage grouping (routed
strategy vs. cold-start), and the per-sample parent-id refinement chain.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.storage.models import StrategyAttempt, StrategyRun
from advtest_ext.strategy.models import Attempt
from advtest_ext.strategy.orchestrator import RunResult, SampleOutcome
from advtest_ext.strategy.persist import lineage_id_for, persist_run


def _outcome_hit() -> SampleOutcome:
    return SampleOutcome(
        sample_key="google_docs/user_task_1/exfil",
        strategy_ids=["google_docs_attack_strategy_docs_demo_gemini"],
        status="hit",
        hit_iter=1,
        attempts=[
            Attempt(iter=0, analysis="a0", injection="i0", utility=True, security=False),
            Attempt(iter=1, analysis="a1", injection="i1", utility=True, security=True),
        ],
    )


def _outcome_coldstart_miss() -> SampleOutcome:
    return SampleOutcome(
        sample_key="workspace/user_task_0/injection_task_0",
        strategy_ids=["_template_cold_start"],
        status="miss",
        hit_iter=None,
        attempts=[Attempt(iter=0, analysis="a", injection="i", utility=False, security=False)],
    )


def test_lineage_id_routed_vs_cold_start() -> None:
    assert lineage_id_for(_outcome_hit()) == "google_docs_attack_strategy_docs_demo_gemini"
    assert lineage_id_for(_outcome_coldstart_miss()) == (
        "cold_start:workspace/user_task_0/injection_task_0"
    )


@pytest.fixture
def factory():
    engine = make_engine("sqlite://")  # in-memory, StaticPool
    return make_session_factory(engine)


def test_persist_writes_run_and_attempts(factory) -> None:
    result = RunResult(outcomes=[_outcome_hit(), _outcome_coldstart_miss()])
    with session_scope(factory) as s:
        run_id = persist_run(result, session=s, target="agentdojo", target_model="gpt-5-nano")

    with session_scope(factory) as s:
        run = s.get(StrategyRun, run_id)
        assert run is not None
        assert run.n_samples == 2 and run.hits == 1
        assert run.asr == 0.5
        assert run.target_model == "gpt-5-nano"
        assert run.finished_at is not None

        rows = list(s.scalars(select(StrategyAttempt).where(StrategyAttempt.run_id == run_id)))
        assert len(rows) == 3  # 2 + 1 attempts

        # Lineage: the hit sample's rows carry the routed strategy id; the
        # cold-start sample's row carries the cold_start lineage.
        lineages = {r.sample_key: r.lineage_id for r in rows}
        assert lineages["google_docs/user_task_1/exfil"] == (
            "google_docs_attack_strategy_docs_demo_gemini"
        )
        assert lineages["workspace/user_task_0/injection_task_0"].startswith("cold_start:")


def test_persist_builds_parent_chain(factory) -> None:
    result = RunResult(outcomes=[_outcome_hit()])
    with session_scope(factory) as s:
        run_id = persist_run(result, session=s)

    with session_scope(factory) as s:
        rows = sorted(
            s.scalars(
                select(StrategyAttempt).where(
                    StrategyAttempt.sample_key == "google_docs/user_task_1/exfil"
                )
            ),
            key=lambda r: r.iter,
        )
        assert [r.iter for r in rows] == [0, 1]
        # iter 0 has no parent; iter 1's parent is iter 0's row id.
        assert rows[0].parent_id is None
        assert rows[1].parent_id == rows[0].id
        # The winning attempt's security flag persisted.
        assert rows[1].security is True
        del run_id  # (quiet lints: run_id proves the write happened)


def test_persist_default_engine_roundtrip(tmp_path) -> None:
    # Exercise the no-session path against a file DB (not the project default).
    db = f"sqlite:///{tmp_path / 'strat.db'}"
    run_id = persist_run(RunResult(outcomes=[_outcome_hit()]), db_url=db)
    factory = make_session_factory(make_engine(db))
    with session_scope(factory) as s:
        assert s.get(StrategyRun, run_id) is not None
        assert s.scalar(select(StrategyAttempt).where(StrategyAttempt.security.is_(True)))
