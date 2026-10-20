"""The in-process iterative-attack runner (PIMiner's route -> step -> rollout).

For each sample: route to the top-K strategies, then iterate the attacker
against the target until a hard hit (``security``) or ``n_max`` iters.
After each sample, a one-paragraph *experience* entry is appended and fed
into later samples' prompts — the intra-dataset level of PIMiner's
three-level memory. The long-term level is the strategy library; the
intra-sample level is each sample's attempt history.

The per-iteration loop itself lives in :mod:`advtest_ext.strategy.loop`
so the attack-plugin path can share it; this module is the standalone
binding over the :class:`~advtest_ext.strategy.targets.AttackTarget`
protocol. No subprocesses, no ``run_dir`` — state is held in memory and
returned as :class:`RunResult`. Persisting a run into fission's
storage/export and running the post-run digester are separate slices that
consume a :class:`RunResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from advtest_ext.strategy.attacker import Attacker
from advtest_ext.strategy.loop import iterate_sample, load_strategies, select_strategy_doc
from advtest_ext.strategy.models import Attempt, Sample
from advtest_ext.strategy.router import ColdStartRouter, Router
from advtest_ext.strategy.targets import AttackTarget


@dataclass
class SampleOutcome:
    sample_key: str
    strategy_ids: list[str]
    status: str  # "hit" | "miss"
    hit_iter: int | None
    attempts: list[Attempt] = field(default_factory=list)


@dataclass
class RunResult:
    outcomes: list[SampleOutcome] = field(default_factory=list)

    @property
    def hits(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "hit")

    @property
    def asr(self) -> float:
        return self.hits / len(self.outcomes) if self.outcomes else 0.0


def _experience_entry(sample: Sample, outcome: SampleOutcome) -> str:
    verb = "HIT" if outcome.status == "hit" else "MISS"
    last = outcome.attempts[-1] if outcome.attempts else None
    inj = (last.injection if last else "").strip().replace("\n", " ")
    if len(inj) > 400:
        inj = inj[:400] + " ..."
    note = (last.analysis if last else "").strip().replace("\n", " ")
    if len(note) > 300:
        note = note[:300] + " ..."
    return (
        f"- [{verb}] {sample.sample_key} (goal: {sample.injection_task_goal}) "
        f"via {outcome.strategy_ids} in {len(outcome.attempts)} iter(s). "
        f"Final injection: {inj!r}. Closing analysis: {note!r}."
    )


class IterativeAttack:
    def __init__(
        self,
        *,
        target: AttackTarget,
        attacker: Attacker,
        router: Router | None = None,
        library_dir: Path | str | None = None,
        n_max: int = 10,
        threat_model: str = "black_box",
    ) -> None:
        self._target = target
        self._attacker = attacker
        self._router = router or ColdStartRouter()
        self._library_dir = library_dir
        self._n_max = n_max
        self._threat_model = threat_model

    async def run(self, samples: list[Sample]) -> RunResult:
        strategies = load_strategies(self._library_dir)
        experiences: list[str] = []
        result = RunResult()

        for sample in samples:
            ids, strategy_doc = await select_strategy_doc(
                sample,
                router=self._router,
                strategies=strategies,
                library_dir=self._library_dir,
            )

            history: list[Attempt] = []
            experience = "\n".join(experiences)

            # Drive the shared core; it appends every attempt (incl. no-op
            # turns) to ``history`` in place and stops after the first hit.
            async for _step in iterate_sample(
                sample=sample,
                strategy_doc=strategy_doc,
                attacker=self._attacker,
                rollout=self._target.rollout,
                n_max=self._n_max,
                threat_model=self._threat_model,
                experience=experience,
                history=history,
            ):
                pass

            if history and history[-1].security:
                status, hit_iter = "hit", history[-1].iter
            else:
                status, hit_iter = "miss", None

            outcome = SampleOutcome(
                sample_key=sample.sample_key,
                strategy_ids=ids,
                status=status,
                hit_iter=hit_iter,
                attempts=history,
            )
            result.outcomes.append(outcome)
            experiences.append(_experience_entry(sample, outcome))

        return result
