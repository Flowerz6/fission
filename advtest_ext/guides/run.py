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

import asyncio
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
    "google_slides": "google_slides_multiturn",
    "google_calendar": "google_calendar_multiturn",
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
    attackers: list[Provider] | None = None,
    planner: Provider | None = None,
    planners: list[Provider] | None = None,
    attack_judge: JudgeBase,
    orchestrator_judge: JudgeBase,
    confirm_judge: JudgeBase | None = None,
    checker: Provider | None = None,
    checkers: list[Provider] | None = None,
    budget: Budget | None = None,
    output_dir: Path | None = None,
    session_factory: Any = None,
    max_concurrency: int = 1,
    review_sample_budget: int = 0,
    review_uniform_fraction: float = 0.6,
    review_seed: int = 0,
    run_id: str | None = None,
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
    # Guide runs keep exploring after a win: the tree should not stop on the first
    # successful node, it should branch on to find MORE successes (and, in doing so,
    # exercise the guide's mutation cells). Callers can re-enable early-exit by
    # passing stop_on_success=True.
    attack_overrides.setdefault("stop_on_success", False)
    # G1 hard-invariant gate OFF by default: the tree branches freely and the
    # oracle (signals + judge) alone decides success. The gate was rejecting every
    # proposed mutation and killing the run before it could branch; free-form
    # exploration maximises seed/mutation discovery. Re-enable per run by passing
    # hard_invariant_validation=True (CLI: --invariant-gate).
    attack_overrides.setdefault("hard_invariant_validation", False)
    attack_overrides.setdefault("semantic_validation_required", False)
    artifact_root = output_dir
    if artifact_root is not None:
        attack_overrides.setdefault("archive_path", str(artifact_root / "tap_archive.json"))
        attack_overrides.setdefault("checkpoint_path", str(artifact_root / "tap_checkpoint.json"))
        attack_overrides.setdefault(
            "search_report_path", str(artifact_root / "tap_search_report.json")
        )
        attack_overrides.setdefault(
            "partial_calibration_path", str(artifact_root / "partial_signal_calibration.json")
        )
        attack_overrides.setdefault(
            "judge_calibration_path", str(artifact_root / "judge_calibration.json")
        )
        persistence_scope = ":".join(
            [
                guide.id,
                type(target).__name__,
                str(getattr(attacker, "model_id", "unknown")),
            ]
        )
        attack_overrides.setdefault("archive_scope", persistence_scope)
        attack_overrides.setdefault("checkpoint_scope", persistence_scope)
    attack = plan.build_attack(
        attacker=attacker,
        attackers=attackers,
        planner=planner,
        planners=planners,
        judge=attack_judge,
        confirm_judge=confirm_judge,
        checker=checker,
        checkers=checkers,
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
        run_id=run_id,
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
    confirm_judge_name: str = "decomposed_ipi",
    attacker_roles: list[str] | None = None,
    planner_role: str | None = None,
    planner_roles: list[str] | None = None,
    checker_role: str | None = "CHECKER_MODEL",
    checker_roles: list[str] | None = None,
    verifier_quorum: int | None = None,
    budget: Budget | None = None,
    output_dir: str | Path | None = None,
    session_factory: Any = None,
    review_sample_budget: int = 0,
    review_uniform_fraction: float = 0.6,
    review_seed: int = 0,
    run_id: str | None = None,
    execute_setup: bool = True,
    setup_variables: dict[str, str] | None = None,
    setup_operations: Any = None,
    setup_account_pool: Any = None,
    setup_session_pool: Any = None,
    setup_settle_s: float = 5.0,
    mutation_cleanup_callback: Any = None,
    **attack_overrides: Any,
) -> RunResult:
    """Resolve live components from ``provider_config`` (the providers YAML) and
    run ``guide`` end-to-end. Mirrors the tap_crescendo CLI factory's judge
    choices: a retrieval judge for the per-turn gradient and an LLM confirm
    judge (``confirm_judge_name``, default ``decomposed_ipi``; pass ``jury`` for
    a panel vote). Drives a real target — use via ``scripts/run_guide.py``.
    """
    # Imported lazily: these pull the live target/provider stack, which the pure
    # assembly path (and its tests) must not require.
    from advtest_ext._cli_factories import (
        _resolve_judge,
        _resolve_target,
        _retrieval_judge_from_opts,
    )
    from advtest_ext._cli_providers import _load_provider_role

    options = dict(target_options or {})
    setup_result = None
    if execute_setup and guide.setup_steps:
        from advtest_ext.guides.stage import bind_setup_target_options, stage_guide

        setup_result = await stage_guide(
            guide,
            variables=setup_variables,
            operations=setup_operations,
            account_pool=setup_account_pool,
            session_pool=setup_session_pool,
        )
        options = bind_setup_target_options(guide, setup_result, options)
        if guide.target.app == "gmail" and "thread_id" not in options:
            options["thread_id"] = "inbox"
        # Flat settle is a crude fallback. When the guide configures an adaptive
        # readiness probe (setup_verification.probe_prompt), verify_target_setup
        # below polls with backoff until the plant is retrievable, so the blind
        # sleep is redundant and only adds latency — skip it in that case.
        adaptive_probe = bool(guide.setup_verification and guide.setup_verification.probe_prompt)
        if setup_settle_s > 0 and not adaptive_probe:
            await asyncio.sleep(setup_settle_s)

    cfg_path = Path(provider_config)
    attacker = _load_provider_role(
        cfg_path,
        "CLAUDE_ATTACKER",
        missing_role_hint="add a CLAUDE_ATTACKER role (provider_type: anthropic, model_id: …)",
    )
    extra_attackers = [
        _load_provider_role(
            cfg_path,
            role,
            missing_role_hint=f"add a {role} role or remove it from attacker_roles",
        )
        for role in (attacker_roles or [])
        if role != "CLAUDE_ATTACKER"
    ]
    planner = attacker
    if planner_role and planner_role != "CLAUDE_ATTACKER":
        planner = _load_provider_role(
            cfg_path,
            planner_role,
            missing_role_hint=f"add a {planner_role} planner role",
        )
    extra_planners = [
        _load_provider_role(
            cfg_path,
            role,
            missing_role_hint=f"add a {role} planner role or remove it from planner_roles",
        )
        for role in (planner_roles or [])
        if role not in {"CLAUDE_ATTACKER", planner_role}
    ]
    checker = None
    if checker_role:
        try:
            checker = _load_provider_role(
                cfg_path,
                checker_role,
                missing_role_hint=("add a family-disjoint verifier role or pass checker_role=None"),
            )
        except Exception:
            checker = None
    extra_checkers = [
        _load_provider_role(
            cfg_path,
            role,
            missing_role_hint=f"add a {role} verifier role or remove it from checker_roles",
        )
        for role in (checker_roles or [])
        if role != checker_role
    ]
    semantic_required = bool(
        attack_overrides.get(
            "semantic_validation_required",
            bool(guide.mutation_surface or guide.mutation_operators or guide.invariants),
        )
    )
    if semantic_required and checker is None and not extra_checkers:
        raise RuntimeError(
            "typed Guide exploration requires an independent checker; "
            "configure CHECKER_MODEL or pass checker_role"
        )
    target = _resolve_target(target_name or default_target_name(guide), options or None)
    if execute_setup and (guide.setup_steps or guide.setup_verification):
        from advtest_ext.guides.stage import verify_target_setup

        await verify_target_setup(guide, target, setup_result)
    if guide.mutation_operators:
        from advtest_ext.guides.mutation_runtime import GuideMutationMaterializer

        attack_overrides.setdefault(
            "mutation_materializer",
            GuideMutationMaterializer(
                guide=guide,
                target=target,
                variables=setup_variables,
                operations=setup_operations,
                account_pool=setup_account_pool,
                session_pool=setup_session_pool,
                cleanup_callback=mutation_cleanup_callback,
            ),
        )
    if verifier_quorum is not None:
        attack_overrides.setdefault("verifier_quorum", verifier_quorum)
    orchestrator = build_guide_run(
        guide,
        target=target,
        attacker=attacker,
        attackers=extra_attackers,
        planner=planner,
        planners=extra_planners,
        attack_judge=_retrieval_judge_from_opts({}),
        orchestrator_judge=_resolve_judge(orchestrator_judge_name),
        confirm_judge=_resolve_judge(confirm_judge_name) if confirm_with_llm_judge else None,
        checker=checker,
        checkers=extra_checkers,
        budget=budget,
        output_dir=Path(output_dir) if output_dir is not None else None,
        session_factory=session_factory,
        review_sample_budget=review_sample_budget,
        review_uniform_fraction=review_uniform_fraction,
        review_seed=review_seed,
        **attack_overrides,
        run_id=run_id,
    )
    # Label the orchestrator with the guide so a winning node can be harvested as a
    # proven VARIANT of this guide (parented, not a loose seed / new playbook).
    orchestrator._guide_id = guide.id
    try:
        result = await orchestrator.run()
        # Tag the run with the guide it came from so the dashboard can list
        # only same-guide runs (e.g. the authenticity check compares apples to
        # apples). Best-effort: a tagging failure never sinks a completed run.
        if session_factory is not None:
            try:
                from advtest_ext.storage import session_scope, set_run_guide

                with session_scope(session_factory) as s:
                    set_run_guide(s, run_id=result.run_id, guide_id=guide.id)
            except Exception:  # pragma: no cover - best-effort tagging
                pass
            # Update the durable seed-reliability ledger from this run's traces
            # (execution-only validity + discard rule). Best-effort: a ledger
            # failure never sinks a completed run.
            try:
                from advtest_ext.core.reliability import recompute_seed_reliability
                from advtest_ext.storage import session_scope

                with session_scope(session_factory) as s:
                    recompute_seed_reliability(s, guide.id, run_id=result.run_id)
            except Exception:  # pragma: no cover - best-effort ledger
                pass
        return result
    finally:
        # The setup/mutation session pool (attacker's browser contexts) is
        # driven throughout the run by staging and the mutation materializer,
        # so it can only be torn down here — inside the same event loop, after
        # the run completes. Closing it in the caller (after asyncio.run) would
        # bind Playwright's Chromium to a closed loop and hang teardown.
        if setup_session_pool is not None:
            close = getattr(setup_session_pool, "close", None)
            if close is not None:
                await close()


__all__ = ["build_guide_run", "default_target_name", "run_guide"]
