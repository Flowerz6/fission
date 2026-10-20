"""Repeated-trial replay — the pilot's true M/N (schema field 7).

Within one run the attacker's attempts are refinement *iterations*, not
independent trials, so they can't establish a success rate. A replay takes
one winning injection and re-runs it ``n_trials`` times in **fresh target
sessions** (each ``rollout`` is a clean episode), counting how many produce
the verified effect. Because it only calls the :class:`AttackTarget`
rollout seam, it works against any backend (offline / AgentDojo / live
Workspace).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from advtest_ext.storage.database import make_engine, make_session_factory, session_scope
from advtest_ext.storage.models import StrategyReplay
from advtest_ext.strategy.models import Sample
from advtest_ext.strategy.orchestrator import RunResult
from advtest_ext.strategy.persist import lineage_id_for
from advtest_ext.strategy.targets import AttackTarget


@dataclass
class ReplayStat:
    sample_key: str
    injection: str
    trials_n: int
    successes_m: int
    verdicts: list[bool] = field(default_factory=list)
    lineage_id: str = ""
    target_model: str = ""

    @property
    def asr(self) -> float:
        return self.successes_m / self.trials_n if self.trials_n else 0.0


async def replay_candidate(
    target: AttackTarget, sample: Sample, injection: str, *, n_trials: int = 3
) -> ReplayStat:
    """Re-run ``injection`` against ``sample`` ``n_trials`` times; count hard
    hits. Each rollout is a fresh target episode."""
    verdicts: list[bool] = []
    for _ in range(max(1, n_trials)):
        res = await target.rollout(sample, injection)
        verdicts.append(bool(res.security))
    return ReplayStat(
        sample_key=sample.sample_key,
        injection=injection,
        trials_n=len(verdicts),
        successes_m=sum(verdicts),
        verdicts=verdicts,
    )


async def replay_run(
    result: RunResult,
    target: AttackTarget,
    samples: list[Sample],
    *,
    n_trials: int = 3,
    successful_only: bool = True,
    target_model: str = "",
) -> list[ReplayStat]:
    """Replay the winning injection of each (successful) outcome."""
    by_key = {s.sample_key: s for s in samples}
    stats: list[ReplayStat] = []
    for outcome in result.outcomes:
        if successful_only and outcome.status != "hit":
            continue
        sample = by_key.get(outcome.sample_key)
        if sample is None or not outcome.attempts:
            continue
        idx = outcome.hit_iter if outcome.hit_iter is not None else len(outcome.attempts) - 1
        winning = outcome.attempts[idx]
        if not winning.injection:
            continue
        stat = await replay_candidate(target, sample, winning.injection, n_trials=n_trials)
        stat.lineage_id = lineage_id_for(outcome)
        stat.target_model = target_model
        stats.append(stat)
    return stats


def persist_replays(
    stats: list[ReplayStat],
    run_id: str,
    *,
    session: Session | None = None,
    db_url: str | None = None,
) -> int:
    """Write replay stats as ``strategy_replays`` rows. Returns the count."""

    def _write(sess: Session) -> None:
        for st in stats:
            sess.add(
                StrategyReplay(
                    run_id=run_id,
                    sample_key=st.sample_key,
                    lineage_id=st.lineage_id,
                    injection=st.injection,
                    trials_n=st.trials_n,
                    successes_m=st.successes_m,
                    asr=st.asr,
                    verdicts=list(st.verdicts),
                    target_model=st.target_model,
                )
            )

    if session is not None:
        _write(session)
    else:
        with session_scope(make_session_factory(make_engine(db_url))) as sess:
            _write(sess)
    return len(stats)


def new_replay_batch_id() -> str:
    return f"replay-{uuid.uuid4().hex[:12]}"
