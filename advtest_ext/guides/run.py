"""Run a Guide end-to-end: assemble the existing orchestrator machinery around
a normalized :class:`~advtest_ext.guides.models.Guide` (slice 3 of Guides).

Two layers, deliberately split so the assembly logic is testable without live
infrastructure:

* :func:`build_guide_run` — **pure assembly.** Takes already-resolved components
  (a target, providers, judges) and returns a configured :class:`Orchestrator`.
  No I/O, no network; unit-testable with a stub target + fakes. This is where a
  Guide actually becomes a run: ``map_guide`` → the guide's ``Behavior`` +
  configured ``TapCrescendoAttack`` + the guide's signal stack, all handed to
  the same orchestrator the CLI uses.
* :func:`run_guide` — the **live convenience.** Resolves the attacker / judges /
  target from the provider-config YAML via the CLI factories, then runs. It
  drives a real browser + LLMs, so it is exercised by ``scripts/run_guide.py``
  (a manual run), not by the unit tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from advtest_ext.core.models import Budget
from advtest_ext.core.orchestrator import GridOrchestrator, Orchestrator
from advtest_ext.guides.models import Guide
from advtest_ext.guides.to_tap_crescendo import map_guide

if TYPE_CHECKING:  # pragma: no cover - typing only
    from advtest_ext.core.models import RunResult
    from advtest_ext.document.target import DocumentTarget
    from advtest_ext.judges.base import JudgeBase
    from advtest_ext.providers.base import Provider

# tap_crescendo is a multi-turn attack, so the target must speak multi-turn.
# Map a guide's app onto the run's target adapter. Docs + Sheets use their
# multi-turn side-panel adapters (tap_crescendo is multi-turn). gmail maps to
# its single-turn base adapter — pass ``target_name`` explicitly for a
# multi-turn gmail run until a multi-turn gmail adapter exists.
_APP_TO_TARGET_NAME: dict[str, str] = {
    "google_docs": "google_docs_multiturn",
    "google_sheets": "google_sheets_multiturn",
    "gmail": "gmail",
}


def default_target_name(guide: Guide) -> str:
    """The target-adapter name for a guide's ``target.app``. Raises for apps
    with no default adapter (``outlook`` / ``tool_use_agent`` / ``agentdojo``);
    pass ``target_name`` explicitly for those."""
    app = guide.target.app
    try:
        return _APP_TO_TARGET_NAME[app]
    except KeyError:
        raise ValueError(
            f"no default target adapter for app {app!r}; pass target_name "
            "explicitly (tap_crescendo needs a multi-turn-capable target)"
        ) from None


def build_guide_run(
    guide: Guide,
    *,
    target: DocumentTarget,
    attacker: Provider,
    attack_judge: JudgeBase,
    orchestrator_judge: JudgeBase,
    confirm_judge: JudgeBase | None = None,
    budget: Budget | None = None,
    output_dir: Path | None = None,
    session_factory: Any = None,
    max_concurrency: int = 1,
    review_sample_budget: int = 0,
    review_uniform_fraction: float = 0.6,
    review_seed: int = 0,
    orchestrator_cls: type[Orchestrator] = GridOrchestrator,
    **attack_overrides: Any,
) -> Orchestrator:
    """Assemble a runnable :class:`Orchestrator` for ``guide`` (pure — no I/O).

    ``attack_judge`` drives the attack's per-turn escalation gradient (fast
    heuristic); ``orchestrator_judge`` scores each persisted trace and gates
    findings; ``confirm_judge`` (optional) is the LLM check a node must pass to
    converge. The guide's ``success.signals`` become the orchestrator's signal
    stack. ``attack_overrides`` tune the tree/node knobs (``max_depth``,
    ``branching_factor``, ``node_max_turns``, …)."""
    plan = map_guide(guide)
    attack = plan.build_attack(
        attacker=attacker,
        judge=attack_judge,
        confirm_judge=confirm_judge,
        **attack_overrides,
    )
    return orchestrator_cls(
        target=target,
        attacks=[attack],
        behaviors=[plan.behavior],
        judge=orchestrator_judge,
        budget=budget or Budget(),
        output_dir=output_dir,
        max_concurrency=max_concurrency,
        session_factory=session_factory,
        # Raw specs (not the built signals) — the orchestrator builds them at
        # startup, exactly as it does for a CLI RunConfig's ``signals:`` block.
        signal_specs=list(guide.success.signals),
        # Measurement-spine review sampling: at run completion, stratified-
        # sample traces into the HITL queue so a live guide run feeds the
        # gold set → PPI. 0 (default) keeps the legacy behaviour.
        review_sample_budget=review_sample_budget,
        review_uniform_fraction=review_uniform_fraction,
        review_seed=review_seed,
    )


async def run_guide(
    guide: Guide,
    *,
    provider_config: str | Path,
    target_name: str | None = None,
    target_options: dict[str, Any] | None = None,
    orchestrator_judge_name: str = "retrieval_verdict",
    confirm_with_llm_judge: bool = True,
    budget: Budget | None = None,
    output_dir: str | Path | None = None,
    session_factory: Any = None,
    review_sample_budget: int = 0,
    review_uniform_fraction: float = 0.6,
    review_seed: int = 0,
    **attack_overrides: Any,
) -> RunResult:
    """Resolve live components from ``provider_config`` (the providers YAML) and
    run ``guide`` end-to-end. Mirrors the tap_crescendo CLI factory's judge
    choices: a retrieval judge for the per-turn gradient and the LLM ipi_verdict
    judge for confirmation. Drives a real target — use via ``scripts/run_guide.py``.
    """
    # Imported lazily: these pull the live target/provider stack, which the pure
    # assembly path (and its tests) must not require.
    from advtest_ext._cli_factories import (
        _ipi_verdict_judge_factory,
        _resolve_judge,
        _resolve_target,
        _retrieval_judge_from_opts,
    )
    from advtest_ext._cli_providers import _load_provider_role

    cfg_path = Path(provider_config)
    attacker = _load_provider_role(
        cfg_path,
        "CLAUDE_ATTACKER",
        missing_role_hint="add a CLAUDE_ATTACKER role (provider_type: anthropic, model_id: …)",
    )
    target = _resolve_target(target_name or default_target_name(guide), target_options)
    orchestrator = build_guide_run(
        guide,
        target=target,
        attacker=attacker,
        attack_judge=_retrieval_judge_from_opts({}),
        orchestrator_judge=_resolve_judge(orchestrator_judge_name),
        confirm_judge=_ipi_verdict_judge_factory() if confirm_with_llm_judge else None,
        budget=budget,
        output_dir=Path(output_dir) if output_dir is not None else None,
        session_factory=session_factory,
        review_sample_budget=review_sample_budget,
        review_uniform_fraction=review_uniform_fraction,
        review_seed=review_seed,
        **attack_overrides,
    )
    return await orchestrator.run()


__all__ = ["build_guide_run", "default_target_name", "run_guide"]
