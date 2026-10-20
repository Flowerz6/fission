"""Export persisted strategy-loop runs into the pilot ``PilotDataset``.

Reads ``strategy_runs`` / ``strategy_attempts`` (see
:mod:`advtest_ext.strategy.persist`) and emits the same 8-field
``PilotDataset`` the TAP/guide exporter produces (``delivery/schema.py``),
so strategy-loop results land in the Google deliverable through one shared
format and one shared journey/harm catalogue.

Grouping: one :class:`PilotAttack` per (lineage_id, sample_key) — a
distinct attacked case. A group with any hard-verified hit goes to
``successful`` (its winning injection is the attack string); otherwise it
is a ``plausible_unsuccessful`` candidate (the pilot wants these too).

**M/N caveat.** Within one run the attempts are refinement *iterations*,
not independent repeated trials. So ``success_rate`` here reports "of the
N distinct injections tried on this case, M produced the verified effect"
— a real ratio, but NOT the pilot's repeated-trial M/N. True M/N comes
from the replay slice; this exporter marks that in ``verdict_breakdown``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from advtest_ext.delivery.harm_taxonomy import (
    PILOT_HARM_TYPES,
    classify_harm,
    normalize_severity,
)
from advtest_ext.delivery.journeys import JOURNEY_CATALOGUE, resolve_journey
from advtest_ext.delivery.schema import (
    AttackMetadata,
    DatasetCounts,
    PilotAttack,
    PilotDataset,
    PoisonedPayload,
    SuccessRate,
)
from advtest_ext.storage.models import StrategyAttempt, StrategyReplay, StrategyRun

_PLACEHOLDER_RE = re.compile(r"\{INJECTION:[^}]+\}")

# AgentDojo suite -> the (app, surface) the journey resolver understands.
_SUITE_APP = {
    "workspace": ("google_docs", "summarize"),
    "slack": (None, None),
    "banking": (None, None),
    "travel": (None, None),
}


@dataclass
class _Group:
    lineage_id: str
    sample_key: str
    rows: list[StrategyAttempt]

    @property
    def ran(self) -> list[StrategyAttempt]:
        # Iterations that actually produced an injection + rollout (skip the
        # no-op rows the orchestrator records when the attacker emits nothing).
        return [r for r in self.rows if r.injection]

    @property
    def winner(self) -> StrategyAttempt | None:
        return next((r for r in self.ran if r.security), None)

    @property
    def ctx(self) -> dict:
        return self.rows[0].extra_metadata or {} if self.rows else {}


def _poisoned_payload(group: _Group, attack_string: str) -> PoisonedPayload:
    ctx = group.ctx
    carrier = ctx.get("context_with_placeholder", "")
    suite = ctx.get("suite_name", "")
    # The delivered payload is the carrier with the injection substituted in.
    full = _PLACEHOLDER_RE.sub(lambda _m: attack_string, carrier) if carrier else attack_string
    from advtest_ext.delivery.schema import PayloadPart

    structured = []
    if carrier:
        structured.append(PayloadPart(name="carrier", text=carrier))
    structured.append(PayloadPart(name="injection", text=attack_string))
    return PoisonedPayload(payload_kind=suite or "agentdojo", full_text=full, structured=structured)


def _build_attack(
    group: _Group,
    run_ids: list[str],
    seq: int,
    replay: StrategyReplay | None = None,
) -> PilotAttack:
    ctx = group.ctx
    winner = group.winner
    rep = winner or (group.ran[-1] if group.ran else group.rows[-1])
    attack_string = rep.injection
    outcome = "successful" if winner is not None else "plausible_unsuccessful"

    if replay is not None:
        # Prefer the real repeated-trial M/N over the within-run iteration count.
        n, m = replay.trials_n, replay.successes_m
        breakdown = {"hit": m, "miss": n - m, "repeated_trial": 1}
    else:
        n = len(group.ran)
        m = sum(1 for r in group.ran if r.security)
        breakdown = {"hit": m, "miss": n - m, "within_run_iterations_not_repeated_trials": 1}

    harm = classify_harm(
        behavior_id=group.sample_key,
        attack_name="piminer",
        guide_objective=ctx.get("injection_task_goal"),
        guide_tags=[s for s in (rep.strategy_ids or []) if isinstance(s, str)],
    )
    app, surface = _SUITE_APP.get(ctx.get("suite_name", ""), (None, None))
    journey = resolve_journey(
        target_name=rep.target_model or "agentdojo", guide_app=app, guide_surface=surface
    )
    strat_tags = [s for s in (rep.strategy_ids or []) if isinstance(s, str)]

    return PilotAttack(
        id=f"STRAT-{seq:04d}",
        lineage_id=group.lineage_id,
        outcome=outcome,
        harm_type=harm.harm_type,
        harm_subtype=harm.harm_subtype,
        harm_rationale=harm.rationale,
        severity=normalize_severity(harm.severity),
        poisoned_payload=_poisoned_payload(group, attack_string),
        attack_string=attack_string,
        user_journey_id=journey,
        user_supplied_prompt=ctx.get("user_task_prompt"),
        success_rate=SuccessRate(
            trials_n=n,
            successes_m=m,
            asr=(m / n if n else 0.0),
            verdict_breakdown=breakdown,
        ),
        metadata=AttackMetadata(
            strategy="piminer",
            taxonomy_tags=strat_tags + list(harm.extra_tags),
            owasp=harm.owasp,
            mitre_atlas=harm.mitre_atlas,
            promptware_class=harm.promptware_class,
            distinct_payload_variants=len({r.injection for r in group.ran}),
            max_conversation_turns=1,
        ),
        source_run_ids=run_ids,
    )


def _count(successful: list[PilotAttack], plausible: list[PilotAttack]) -> DatasetCounts:
    by_harm: dict[str, int] = defaultdict(int)
    by_journey: dict[str, int] = defaultdict(int)
    by_sev: dict[str, int] = defaultdict(int)
    for a in successful:
        by_harm[a.harm_type] += 1
        by_journey[a.user_journey_id] += 1
        by_sev[a.severity] += 1
    return DatasetCounts(
        successful=len(successful),
        plausible_unsuccessful=len(plausible),
        by_harm_type=dict(by_harm),
        by_journey=dict(by_journey),
        by_severity=dict(by_sev),
    )


def build_dataset_from_strategy_runs(
    session: Session, *, run_ids: list[str] | None = None
) -> PilotDataset:
    """Build a :class:`PilotDataset` from persisted strategy runs (all runs
    when ``run_ids`` is None)."""
    stmt = select(StrategyAttempt)
    if run_ids is not None:
        stmt = stmt.where(StrategyAttempt.run_id.in_(run_ids))
    rows = list(session.scalars(stmt))

    grouped: dict[tuple[str, str], list[StrategyAttempt]] = defaultdict(list)
    for r in rows:
        grouped[(r.lineage_id, r.sample_key)].append(r)

    all_run_ids = run_ids or sorted(
        {rid for (rid,) in session.execute(select(StrategyRun.id))}
    )

    # Real repeated-trial M/N, when a replay was run. Keyed by sample_key;
    # prefer the replay with the most trials if several exist.
    replay_stmt = select(StrategyReplay)
    if run_ids is not None:
        replay_stmt = replay_stmt.where(StrategyReplay.run_id.in_(run_ids))
    replay_by_key: dict[str, StrategyReplay] = {}
    for rp in session.scalars(replay_stmt):
        cur = replay_by_key.get(rp.sample_key)
        if cur is None or rp.trials_n > cur.trials_n:
            replay_by_key[rp.sample_key] = rp

    successful: list[PilotAttack] = []
    plausible: list[PilotAttack] = []
    for seq, ((lineage, sample_key), grp_rows) in enumerate(sorted(grouped.items()), 1):
        grp = _Group(lineage_id=lineage, sample_key=sample_key, rows=sorted(grp_rows, key=lambda r: r.iter))
        if not grp.rows:
            continue
        attack = _build_attack(grp, all_run_ids, seq, replay=replay_by_key.get(sample_key))
        (successful if attack.outcome == "successful" else plausible).append(attack)

    return PilotDataset(
        generated_from_run_ids=all_run_ids,
        user_journeys=dict(JOURNEY_CATALOGUE),
        harm_taxonomy=list(PILOT_HARM_TYPES),
        counts=_count(successful, plausible),
        successful=successful,
        plausible_unsuccessful=plausible,
    )
