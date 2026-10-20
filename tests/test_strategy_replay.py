"""Repeated-trial replay + its M/N flowing into the export (offline)."""

from __future__ import annotations

import pytest

from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.strategy.export import build_dataset_from_strategy_runs
from advtest_ext.strategy.models import Attempt, RolloutResult, Sample
from advtest_ext.strategy.orchestrator import RunResult, SampleOutcome
from advtest_ext.strategy.persist import persist_run
from advtest_ext.strategy.replay import persist_replays, replay_candidate, replay_run


class _FlakyTarget:
    """Hits on a fixed subset of trial indices — models nondeterminism."""

    def __init__(self, hit_on: set[int]) -> None:
        self._hit_on = hit_on
        self._i = 0

    async def rollout(self, sample: Sample, injection: str) -> RolloutResult:
        hit = self._i in self._hit_on
        self._i += 1
        return RolloutResult(utility=True, security=hit, final_output=f"trial {self._i}")


def _sample() -> Sample:
    return Sample(
        suite_name="workspace",
        user_task_id="user_task_1",
        injection_task_id="exfil",
        user_task_prompt="Summarize.",
        injection_task_goal="Exfiltrate the body.",
        context_with_placeholder="body {INJECTION:doc}",
        injection_candidates=["doc"],
    )


def _hit_result() -> RunResult:
    return RunResult(
        outcomes=[
            SampleOutcome(
                sample_key="workspace/user_task_1/exfil",
                strategy_ids=["s1"],
                status="hit",
                hit_iter=0,
                attempts=[Attempt(iter=0, analysis="a", injection="WIN", utility=True, security=True)],
            )
        ]
    )


async def test_replay_candidate_counts_hits() -> None:
    target = _FlakyTarget(hit_on={0, 2})  # 2 of 3 trials hit
    stat = await replay_candidate(target, _sample(), "WIN", n_trials=3)
    assert stat.trials_n == 3
    assert stat.successes_m == 2
    assert stat.verdicts == [True, False, True]
    assert stat.asr == pytest.approx(2 / 3)


async def test_replay_run_only_replays_winners() -> None:
    result = RunResult(
        outcomes=[
            _hit_result().outcomes[0],
            SampleOutcome(
                sample_key="workspace/user_task_2/x",
                strategy_ids=["_template_cold_start"],
                status="miss",
                hit_iter=None,
                attempts=[Attempt(iter=0, analysis="a", injection="n", utility=False, security=False)],
            ),
        ]
    )
    stats = await replay_run(result, _FlakyTarget(hit_on={0, 1, 2}), [_sample()], n_trials=3)
    assert len(stats) == 1  # only the hit sample replayed
    assert stats[0].sample_key == "workspace/user_task_1/exfil"
    assert stats[0].successes_m == 3
    assert stats[0].lineage_id == "s1"


@pytest.fixture
def session():
    engine = make_engine("sqlite://")
    with session_scope(make_session_factory(engine)) as s:
        yield s


async def test_replay_mn_overrides_within_run_count_in_export(session) -> None:
    run_id = persist_run(_hit_result(), session=session, samples=[_sample()])
    session.flush()

    # Without a replay, export uses the within-run count: n=1, m=1.
    ds0 = build_dataset_from_strategy_runs(session)
    sr0 = ds0.successful[0].success_rate
    assert (sr0.trials_n, sr0.successes_m) == (1, 1)
    assert "within_run_iterations_not_repeated_trials" in sr0.verdict_breakdown

    # Replay 5x against a flaky target (3 hits), persist, re-export.
    stats = await replay_run(_hit_result(), _FlakyTarget(hit_on={0, 2, 4}), [_sample()], n_trials=5)
    persist_replays(stats, run_id, session=session)
    session.flush()

    ds1 = build_dataset_from_strategy_runs(session)
    sr1 = ds1.successful[0].success_rate
    assert (sr1.trials_n, sr1.successes_m) == (5, 3)  # real repeated-trial M/N
    assert sr1.asr == pytest.approx(3 / 5)
    assert sr1.verdict_breakdown.get("repeated_trial") == 1
