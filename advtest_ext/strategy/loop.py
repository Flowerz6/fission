"""Target-agnostic core of the iterative-attack loop.

The per-sample *route -> step -> rollout* machinery, factored out of
:mod:`advtest_ext.strategy.orchestrator` so more than one caller can drive
the same loop:

  * :class:`~advtest_ext.strategy.orchestrator.IterativeAttack` — the
    standalone, in-process runner. Binds ``rollout`` to the
    :class:`~advtest_ext.strategy.targets.AttackTarget` protocol
    (offline / agentdojo / workspace) and collects a :class:`RunResult`.
  * the ``strategy_loop`` attack plugin (``advtest_ext/attacks/``) — binds
    ``rollout`` to a live fission ``DocumentTarget`` and yields an
    ``AttackAttempt`` per step into the main orchestrator.

The core knows nothing about either caller. It takes a strategy doc, a
:class:`Sample`, an :class:`Attacker`, and a ``rollout`` callable, and
yields one :class:`Step` per attacker iteration — so the standalone
runner and the plugin stay one loop with two bindings, not two copies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from advtest_ext.strategy.attacker import Attacker
from advtest_ext.strategy.library import Strategy, cold_start_doc, discover
from advtest_ext.strategy.models import Attempt, RolloutResult, Sample
from advtest_ext.strategy.promptbuild import build_user_message
from advtest_ext.strategy.prompts import ATTACKER_SYSTEM
from advtest_ext.strategy.router import COLD_START_ID, ColdStartRouter, Router

Rollout = Callable[[Sample, str], Awaitable[RolloutResult]]
"""One injection attempt -> its outcome. ``AttackTarget.rollout`` satisfies
this signature directly; the plugin supplies a ``DocumentTarget``-backed
adapter that conforms to the same shape."""


@dataclass
class Step:
    """One attacker iteration surfaced to a caller.

    ``rollout`` is ``None`` when the attacker produced no parseable
    injection this iteration — a no-op turn still recorded in history (so
    the next prompt sees it) but with no target interaction. Callers that
    mirror real target traffic (e.g. the plugin yielding an
    ``AttackAttempt`` per response) should skip steps whose ``rollout`` is
    ``None``; callers that only need the final attempt list can ignore the
    distinction.
    """

    attempt: Attempt
    rollout: RolloutResult | None


def load_strategies(library_dir: Path | str | None) -> list[Strategy]:
    """Discover the markdown strategy library, or ``[]`` when no dir is set."""
    return discover(library_dir) if library_dir else []


def assemble_strategy_doc(
    ids: list[str],
    strategies: list[Strategy],
    library_dir: Path | str | None,
) -> str:
    """Render the routed strategy ids into the attacker's strategy section.

    ``COLD_START_ID`` expands to the cold-start template; known ids expand
    to their library text. Never returns an empty doc — an empty routing
    result falls back to the cold-start guidance.
    """
    by_id = {s.id: s for s in strategies}
    docs: list[str] = []
    for sid in ids:
        if sid == COLD_START_ID:
            docs.append(
                "### Cold-start (no fitting strategy) — attack from scratch, "
                "mirroring this structure\n\n" + cold_start_doc(library_dir)
            )
        elif sid in by_id:
            docs.append(f"### Strategy `{sid}`\n\n{by_id[sid].text}")
    if not docs:  # defensive: router validated ids, but never ship an empty doc
        docs.append("### Cold-start — attack from scratch\n\n" + cold_start_doc(library_dir))
    return "\n\n---\n\n".join(docs)


async def select_strategy_doc(
    sample: Sample,
    *,
    router: Router | None,
    strategies: list[Strategy],
    library_dir: Path | str | None,
) -> tuple[list[str], str]:
    """Route ``sample`` over ``strategies`` and assemble the strategy doc.

    Returns ``(routed_ids, strategy_doc)``. A ``None`` router cold-starts.
    """
    router = router or ColdStartRouter()
    ids = await router.route(sample, strategies)
    return ids, assemble_strategy_doc(ids, strategies, library_dir)


async def iterate_sample(
    *,
    sample: Sample,
    strategy_doc: str,
    attacker: Attacker,
    rollout: Rollout,
    n_max: int = 10,
    threat_model: str = "black_box",
    experience: str = "",
    history: list[Attempt] | None = None,
) -> AsyncIterator[Step]:
    """Iterate ``attacker`` against ``rollout`` until a security hit or
    ``n_max`` iterations, yielding one :class:`Step` per iteration.

    ``history`` (when given) is appended in place, so after iterating the
    caller holds the full attempt list — including no-op turns — which is
    the intra-sample memory the prompt builder folds into the next turn.
    The loop stops right after yielding the first hit.
    """
    hist = history if history is not None else []
    for it in range(n_max):
        user = build_user_message(
            strategy_doc,
            sample,
            hist,
            threat_model=threat_model,
            experience=experience,
        )
        try:
            analysis, injection = await attacker.propose(system=ATTACKER_SYSTEM, user=user)
        except ValueError as exc:
            # The attacker returned nothing parseable (e.g. a 5.x model that
            # spent its budget on thinking, or refused). Record a no-op
            # iteration and keep going rather than crashing the whole run.
            attempt = Attempt(
                iter=it,
                analysis=f"(no parseable injection: {exc})",
                injection="",
                utility=False,
                security=False,
            )
            hist.append(attempt)
            yield Step(attempt=attempt, rollout=None)
            continue
        res = await rollout(sample, injection)
        attempt = Attempt(
            iter=it,
            analysis=analysis,
            injection=injection,
            utility=res.utility,
            security=res.security,
            final_output=res.final_output,
            trajectory=res.trajectory,
        )
        hist.append(attempt)
        yield Step(attempt=attempt, rollout=res)
        if res.security:
            break
