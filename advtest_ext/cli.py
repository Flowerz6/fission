"""``advtest`` command-line entry point.

Usage:

    poetry run advtest run --config configs/<name>.yaml \\
        [--strategy grid|mcts] [--output-dir ./out]

The CLI's job is the boundary work the orchestrator deliberately
doesn't do: load a ``RunConfig`` from YAML, resolve attack / target
/ judge name strings to concrete instances, and dispatch to the
chosen orchestrator.

Name resolution lives in :mod:`advtest_ext._cli_factories` (the
``_ATTACK_REGISTRY`` / ``_TARGET_REGISTRY`` / ``_JUDGE_REGISTRY`` and
the ``_resolve_*`` helpers); provider/config resolution lives in
:mod:`advtest_ext._cli_providers`. Adding a new attack or target means
appending an entry to the relevant registry there. Names that the
orchestrator can't construct without runtime arguments (PAIR,
Crescendo, TAP — they need an attacker Provider) are intentionally
absent from the CLI registry; use those from notebook/programmatic
runs where you can plumb the Provider directly.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import typer
import yaml

from advtest_ext._cli_calibrate import register as _register_calibrate
from advtest_ext._cli_factories import (
    _resolve_attack,
    _resolve_behaviors,
    _resolve_judge,
    _resolve_target,
)
from advtest_ext._cli_providers import (
    _resolve_db_url,
)
from advtest_ext._cli_rescore import register as _register_rescore
from advtest_ext._cli_run_recipe import register as _register_run_recipe
from advtest_ext.core.models import (
    Budget,
    RunConfig,
    RunResult,
    SummaryStats,
)
from advtest_ext.core.orchestrator import (
    GridOrchestrator,
    MctsOrchestrator,
    Orchestrator,
    summarise,
)


def _build_account_pool(
    pool_spec: list[dict[str, Any]] | None,
) -> object | None:
    """Resolve a ``RunConfig.account_pool`` spec into a concrete
    :class:`advtest_ext.core.account_pool.AccountPool`.

    Returns ``None`` when the operator didn't configure a pool —
    the orchestrator then keeps single-account legacy behaviour
    (every target uses its own legacy
    ``~/.advtest/playwright-profile`` path).

    Bad slot definitions (duplicate role, unknown role string,
    missing profile_dir) raise :class:`typer.BadParameter` so the
    CLI fails the invocation up-front rather than crashing
    mid-run.
    """
    if not pool_spec:
        return None
    from pydantic import ValidationError

    from advtest_ext.core.account_pool import AccountPool, AccountSlot

    slots: list[AccountSlot] = []
    for i, raw in enumerate(pool_spec):
        if not isinstance(raw, dict):
            raise typer.BadParameter(
                f"account_pool[{i}] must be a mapping, got {type(raw).__name__}"
            )
        try:
            slots.append(AccountSlot(**raw))
        except ValidationError as exc:
            raise typer.BadParameter(f"account_pool[{i}] is invalid: {exc}") from exc
    try:
        return AccountPool(slots=slots)
    except ValidationError as exc:
        raise typer.BadParameter(f"account_pool: {exc}") from exc


def _build_orchestrator(
    *,
    strategy: str,
    config: RunConfig,
    output_dir: Path | None,
    session_factory: object | None = None,
    # Per-phase overrides: when building one phase of a multi-phase run,
    # the target/attacks/behaviors come from the phase, and run_id +
    # phase tag are shared across phases. Default None → single-phase
    # build straight from the top-level config (unchanged behaviour).
    target_name: str | None = None,
    target_options: dict[str, Any] | None = None,
    attack_names: list[str] | None = None,
    attack_options: dict[str, dict[str, Any]] | None = None,
    behavior_names: list[str] | None = None,
    run_id: str | None = None,
    phase: dict[str, Any] | None = None,
) -> Orchestrator:
    # Resolve from the phase overrides when given, else the top-level config.
    t_name = target_name if target_name is not None else config.target_name
    t_opts = target_options if target_options is not None else config.target_options
    a_names = attack_names if attack_names is not None else config.attacks
    a_opts = attack_options if attack_options is not None else config.attack_options
    b_names = behavior_names if behavior_names is not None else config.behaviors

    if t_name is None:
        raise ValueError(
            "no target resolved: set `target_name` on the config (single-phase) "
            "or on the phase (multi-phase)"
        )
    target = _resolve_target(t_name, t_opts)
    attacks = [_resolve_attack(name, a_opts.get(name)) for name in a_names]
    behaviors = _resolve_behaviors(b_names)
    if not config.judges:
        raise typer.BadParameter("RunConfig.judges must list at least one judge")
    # The orchestrator takes a single judge — wrap multiple into an
    # ensemble at the CLI boundary if/when more than one is named.
    judge = _resolve_judge(config.judges[0])

    # Phase-4 account pool. Built here so a malformed slot
    # (duplicate role, missing profile_dir, etc.) fails the CLI
    # invocation rather than the first multi-account Run. None
    # when the operator didn't configure a pool — the orchestrator
    # then keeps single-account legacy behaviour.
    account_pool = _build_account_pool(config.account_pool)

    common_kwargs: dict[str, object] = {
        "target": target,
        "attacks": attacks,
        "behaviors": behaviors,
        "judge": judge,
        "budget": config.budget,
        "output_dir": output_dir,
        "max_concurrency": config.max_concurrency,
        "session_factory": session_factory,
        # Phase-3 typed signal stack. Forwarded as raw spec dicts;
        # the orchestrator constructs Signal instances at startup
        # so a bad spec fails the config load, not mid-run.
        "signal_specs": config.signals,
        # Phase-4 account pool. Held by the orchestrator; not yet
        # passed into targets — that wiring lands with Phase 5.
        "account_pool": account_pool,
        "run_id": run_id,
        "phase": phase,
        # Measurement-spine review sampling. 0 (default) → off, so a
        # plain run is unchanged; a spine config opts in.
        "review_sample_budget": config.review_sample_budget,
        "review_uniform_fraction": config.review_uniform_fraction,
        "review_seed": config.review_seed,
    }
    if strategy == "grid":
        return GridOrchestrator(**common_kwargs)  # type: ignore[arg-type]
    if strategy == "mcts":
        return MctsOrchestrator(**common_kwargs)  # type: ignore[arg-type]
    raise typer.BadParameter(f"unknown strategy {strategy!r}; expected one of: grid, mcts")


# ----------------------------------------------------------------------
# RunConfig loader
# ----------------------------------------------------------------------


def load_run_config(path: Path) -> RunConfig:
    """Load a YAML file into a validated ``RunConfig``. Surfaces
    Pydantic validation errors as ``typer.BadParameter`` so the CLI
    prints a clean message rather than a stack trace."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise typer.BadParameter(f"{path}: top level must be a YAML mapping")
    # Allow ``budget`` to be either nested or flat; normalise to nested
    # so RunConfig.model_validate accepts it cleanly.
    budget = raw.get("budget")
    if isinstance(budget, dict):
        raw["budget"] = Budget.model_validate(budget).model_dump()
    if "output_dir" in raw and not isinstance(raw["output_dir"], (str, Path)):
        raise typer.BadParameter(f"{path}: output_dir must be a path string")
    return RunConfig.model_validate(raw)


# ----------------------------------------------------------------------
# Run loop
# ----------------------------------------------------------------------


async def _run_async(
    *,
    strategy: str,
    config: RunConfig,
    output_dir: Path | None,
    session_factory: object | None,
) -> RunResult:
    # Single-phase (the common case): build one orchestrator straight
    # from the top-level config and run it. Unchanged behaviour.
    if not config.phases:
        orchestrator = _build_orchestrator(
            strategy=strategy,
            config=config,
            output_dir=output_dir,
            session_factory=session_factory,
        )
        return await orchestrator.run()

    # Multi-phase: run each phase sequentially under ONE shared run_id.
    # Each phase is its own orchestrator (own target/attacks/behaviors)
    # but writes traces tagged with the phase so the dashboard renders
    # one tree per phase, in order. The aggregated RunResult spans all
    # phases.
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    n = len(config.phases)
    all_traces: list[Any] = []
    last_result: RunResult | None = None
    for i, phase in enumerate(config.phases):
        phase_tag = {"index": i, "label": phase.label, "total": n}
        orch = _build_orchestrator(
            strategy=strategy,
            config=config,
            output_dir=output_dir,
            session_factory=session_factory,
            target_name=phase.target_name,
            target_options=phase.target_options,
            attack_names=phase.attacks,
            attack_options=phase.attack_options,
            behavior_names=phase.behaviors,
            run_id=run_id,
            phase=phase_tag,
        )
        last_result = await orch.run()
        all_traces.extend(last_result.traces)

    # Aggregate across phases so the persisted RunResult covers the whole
    # run, not just the last phase.
    assert last_result is not None  # n >= 1 guaranteed by the validator
    return RunResult(
        run_id=run_id,
        config=config,
        traces=all_traces,
        summary_stats=summarise(all_traces),
    )


# ----------------------------------------------------------------------
# Typer app
# ----------------------------------------------------------------------


app = typer.Typer(
    add_completion=False,
    help="advtest CLI: drive attacks against a target, with budget and persistence.",
)
# ``run-recipe`` lives in its own module to keep this file focused on
# the registries / factories / name resolution that ``advtest run``
# needs; the helper cluster (signal evaluation, trace persistence,
# account-pool derivation) travels with it.
_register_run_recipe(app)
_register_rescore(app)
_register_calibrate(app)


@app.callback()
def _root() -> None:
    """Top-level callback forces sub-command resolution so ``advtest run``
    (and not ``advtest`` with implicit single-command elision) is the
    invocation shape — matches the documented contract and makes room
    for future commands (``advtest replay``, ``advtest summarise``)
    without breaking compatibility."""


@app.command()
def run(
    config: Path = typer.Option(  # noqa: B008
        ...,
        "--config",
        "-c",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to a RunConfig YAML.",
    ),
    strategy: str = typer.Option(
        "grid",
        "--strategy",
        "-s",
        help="Orchestration strategy: grid (default) or mcts.",
    ),
    output_dir: Path | None = typer.Option(  # noqa: B008
        None,
        "--output-dir",
        "-o",
        help=(
            "Override the run-result output directory (defaults to the "
            "value in the RunConfig YAML)."
        ),
    ),
    db_url: str | None = typer.Option(
        None,
        "--db-url",
        help=(
            "SQLAlchemy URL for the dashboard database. Resolution "
            "order: this flag, RunConfig.db_url, ADVTEST_DB_URL env, "
            "sqlite:///./advtest.db. Pass --db-url '' to disable the "
            "DB sink entirely (file-only run)."
        ),
    ),
    attacker_model: str | None = typer.Option(
        None,
        "--attacker-model",
        help=(
            "Override the CLAUDE_ATTACKER model_id from the providers YAML "
            "for this run (e.g. claude-opus-4-8). Used by crescendo / "
            "tap_chain / pair_indirect / tap_indirect."
        ),
    ),
    judge_model: str | None = typer.Option(
        None,
        "--judge-model",
        help="Override the CLAUDE_JUDGE model_id from the providers YAML for this run.",
    ),
    target_model: str | None = typer.Option(
        None,
        "--target-model",
        help="Override the GEMINI_TARGET model_id from the providers YAML for this run.",
    ),
) -> None:
    """Run a configured attack run against a target.

    Persists per-trace JSON under ``<output_dir>/<run_id>/traces/``
    and the aggregate ``<output_dir>/<run_id>/run_result.json``.
    Also writes Run / Trace / Finding rows to the configured
    SQLAlchemy database (default ``sqlite:///./advtest.db``) so
    ``advtest_dashboard`` shows the run as it streams.
    """
    from advtest_ext.storage import make_engine, make_session_factory

    # Model overrides are read at provider-load time via env (see
    # _apply_model_override), so set them before building anything.
    for value, env_key in (
        (attacker_model, "ADVTEST_ATTACKER_MODEL"),
        (judge_model, "ADVTEST_JUDGE_MODEL"),
        (target_model, "ADVTEST_TARGET_MODEL"),
    ):
        if value:
            os.environ[env_key] = value

    cfg = load_run_config(config)
    out = output_dir if output_dir is not None else cfg.output_dir
    resolved_db = _resolve_db_url(cli_flag=db_url, config_value=cfg.db_url)
    session_factory = make_session_factory(make_engine(resolved_db)) if resolved_db else None
    result = asyncio.run(
        _run_async(
            strategy=strategy,
            config=cfg,
            output_dir=out,
            session_factory=session_factory,
        )
    )
    # Post-run strategy digest: feed any signal-verified strategy_loop
    # winners back into the markdown strategy library. Off unless
    # ADVTEST_DIGEST_STRATEGY is set; best-effort — never fails the run.
    strategy_digest: dict[str, Any] | None = None
    if session_factory is not None:
        from advtest_ext.strategy.harvest import digest_enabled, maybe_digest_run

        if digest_enabled():
            from advtest_ext._cli_providers import _resolve_provider_config_path

            try:
                cfg_path = _resolve_provider_config_path(
                    missing_roles_hint="a CLAUDE_DIGESTER role"
                )
                report = asyncio.run(
                    maybe_digest_run(session_factory, result.run_id, config_path=cfg_path)
                )
                if report is not None:
                    strategy_digest = {
                        "hits": report.n_hits,
                        "created": report.created,
                        "appended": report.appended,
                        "widened": report.widened,
                    }
            except Exception:  # pragma: no cover - best-effort boundary
                strategy_digest = None
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "strategy": strategy,
                "total_traces": result.summary_stats.total_traces,
                "successful_traces": result.summary_stats.successful_traces,
                "overall_asr": round(result.summary_stats.overall_asr, 4),
                "output_dir": str(out / result.run_id) if out is not None else None,
                "db_url": resolved_db or None,
                "strategy_digest": strategy_digest,
            },
            indent=2,
        )
    )


@app.command(name="ingest-seed")
def ingest_seed(
    path: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the source artefact (today: .docx red-team report).",
    ),
    provider_name: str = typer.Option(
        "CLAUDE_ATTACKER",
        "--provider",
        help=(
            "Name of the Provider in ADVTEST_PROVIDER_CONFIG to use "
            "for the Recipe Extractor. Same naming convention as "
            "attack / judge providers — operators typically point "
            "this at a Claude model with a tool-use-capable context."
        ),
    ),
    db_url: str | None = typer.Option(
        None,
        "--db-url",
        help="SQLAlchemy URL for the catalogue database.",
    ),
    auto_commit: bool = typer.Option(
        True,
        "--auto-commit/--review",
        help=(
            "When the dry-run passes, auto-commit the Recipe to the "
            "catalogue with draft_state='approved'. Pass --review "
            "to leave every Recipe as a draft for operator review "
            "regardless of dry-run outcome."
        ),
    ),
) -> None:
    """Phase-6 — ingest a red-team artefact and extract Recipes.

    Flow:

    1. Load the artefact (docx today; future formats land as
       sibling loaders in ``core/loaders/``).
    2. For each Seed produced by the loader:
       a. Persist the Seed to the catalogue.
       b. Call the :class:`Extractor` agent (Claude) to draft a
          Recipe from the Seed.
       c. Run the :func:`dry_run.validate` structural validator.
       d. If dry-run passes AND --auto-commit, commit the Recipe
          with draft_state='approved'. Otherwise leave it as
          'draft' for operator review on the dashboard.
    3. Print a JSON summary listing every Seed + the outcome
       (committed / drafted / extraction-failed).

    Prerequisites:
        - ADVTEST_PROVIDER_CONFIG points to a provider YAML with
          ``CLAUDE_ATTACKER`` defined (or whatever ``--provider``
          names).
        - ANTHROPIC_API_KEY or equivalent set per that provider.
        - Catalogue database reachable via --db-url / env /
          default sqlite:///./advtest.db.
    """
    import asyncio

    from advtest_ext.agents import Extractor, ExtractorError
    from advtest_ext.core.builtin_operations import default_operations
    from advtest_ext.core.dry_run import validate
    from advtest_ext.core.loaders import load_docx
    from advtest_ext.providers.factory import load_provider_from_yaml
    from advtest_ext.storage import (
        insert_recipe,
        insert_seed,
        make_engine,
        make_session_factory,
        session_scope,
    )

    resolved_db = _resolve_db_url(cli_flag=db_url, config_value=None)
    if not resolved_db:
        raise typer.BadParameter(
            "ingest-seed requires a database; pass --db-url or set ADVTEST_DB_URL"
        )

    # Pick the loader by file extension. Today: only .docx.
    suffix = path.suffix.lower()
    if suffix == ".docx":
        seeds = load_docx(path)
    else:
        raise typer.BadParameter(f"unsupported source format: {suffix!r}; loaders today: .docx")

    typer.echo(f"Loaded {len(seeds)} seed(s) from {path.name}")

    # Build the Extractor's dependencies once. Operations registry
    # is the same default the Recipe Runner uses — Extracted
    # Recipes' operations will be runnable when handlers exist.
    operations = default_operations()
    provider_config_path = os.environ.get("ADVTEST_PROVIDER_CONFIG")
    if not provider_config_path:
        raise typer.BadParameter(
            "ADVTEST_PROVIDER_CONFIG env var must point to a provider "
            "YAML (same one used by ``advtest run``)"
        )
    provider = load_provider_from_yaml(provider_config_path, provider_name)
    extractor = Extractor(provider=provider, operations=operations)

    engine = make_engine(resolved_db)
    factory = make_session_factory(engine)

    # Run every Seed's extraction inside ONE event loop so the
    # provider's underlying async HTTP client (httpx) has a live
    # loop to clean up against at the end. Calling
    # ``asyncio.run`` per-seed produced the "Event loop is closed"
    # cleanup race observed 2026-05-25 where httpx's background
    # close-task fired after the per-seed loop had already shut
    # down. Per-iteration storage writes stay synchronous.
    async def _extract_all() -> list[dict[str, Any]]:
        outcomes: list[dict[str, Any]] = []
        for seed in seeds:
            outcome: dict[str, Any] = {
                "seed_id": seed.id,
                "title": seed.title,
                "extracted": False,
                "dry_run_ok": None,
                "committed": False,
                "draft_state": None,
                "errors": [],
            }
            with session_scope(factory) as s:
                insert_seed(s, seed=seed)

            try:
                recipe = await extractor.extract(seed)
            except ExtractorError as exc:
                outcome["errors"].append(f"extractor: {exc}")
                outcomes.append(outcome)
                continue

            outcome["extracted"] = True
            outcome["recipe_id"] = recipe.id

            dry = validate(recipe, operations=operations)
            outcome["dry_run_ok"] = dry.ok
            outcome["errors"].extend(dry.errors)
            if dry.warnings:
                outcome["warnings"] = list(dry.warnings)

            committed_state = "draft"
            if auto_commit and dry.ok:
                committed_state = "approved"
            outcome["draft_state"] = committed_state

            with session_scope(factory) as s:
                recipe_row = insert_recipe(s, recipe=recipe)
                # insert_recipe is idempotent on id; we set
                # draft_state explicitly here because Recipe (the
                # dataclass) doesn't carry that field — it's a
                # storage-only concern.
                recipe_row.draft_state = committed_state
                outcome["committed"] = True

            outcomes.append(outcome)
        return outcomes

    outcomes = asyncio.run(_extract_all())

    typer.echo(
        json.dumps(
            {
                "source": str(path),
                "total_seeds": len(seeds),
                "outcomes": outcomes,
            },
            indent=2,
            default=str,
        )
    )


@app.command(name="import-run")
def import_run(
    path: Path = typer.Argument(  # noqa: B008
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        help=("Run directory produced by `advtest run` (``<output_dir>/<run_id>/``)."),
    ),
    db_url: str | None = typer.Option(
        None,
        "--db-url",
        help=(
            "SQLAlchemy URL to import into. Defaults to ADVTEST_DB_URL "
            "or sqlite:///./advtest.db, matching `advtest run`."
        ),
    ),
) -> None:
    """Import an on-disk run directory into the dashboard database.

    Useful when a run was produced before the orchestrator's DB
    sink existed, or to copy a run between environments. Idempotent
    on ``trace.id`` and ``run_id``: a re-import updates the Run row
    and skips already-present traces rather than failing on the
    primary key.
    """
    from advtest_ext.storage import (
        finalize_run,
        insert_trace,
        make_engine,
        make_session_factory,
        promote_finding,
        session_scope,
        upsert_run,
    )

    resolved = _resolve_db_url(cli_flag=db_url, config_value=None)
    if not resolved:
        raise typer.BadParameter("--db-url '' disables the DB sink, but import-run requires one")
    rr_path = path / "run_result.json"
    if not rr_path.exists():
        raise typer.BadParameter(
            f"no run_result.json under {path} — pass the run directory "
            "(<output_dir>/<run_id>/), not the parent."
        )

    raw = json.loads(rr_path.read_text())
    run_result = RunResult.model_validate(raw)
    factory = make_session_factory(make_engine(resolved))

    n_inserted = 0
    n_promoted = 0
    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id=run_result.run_id,
            # Phased runs carry no single top-level target; label them so the
            # dashboard row isn't blank (per-phase targets live in the config).
            target_name=run_result.config.target_name or "(multi-phase)",
            strategy="imported",
            config=run_result.config,
            output_dir=str(path.parent.resolve()),
        )
        for trace in run_result.traces:
            insert_trace(s, run_id=run_result.run_id, trace=trace)
            n_inserted += 1
        finalize_run(
            s,
            run_id=run_result.run_id,
            summary=run_result.summary_stats or SummaryStats(),
        )
        # Auto-promote successful traces to Findings — same policy
        # the live orchestrator uses, so an imported run looks
        # identical to one produced live.
        for trace in run_result.traces:
            if trace.verdict != "success":
                continue
            top = max((s_.score for s_ in trace.judge_scores), default=0.0)
            promote_finding(
                s,
                run_id=run_result.run_id,
                trace_id=trace.id,
                severity="med",
                title=(f"{trace.payload.generator_name} → {trace.behavior_id}"),
                description=f"Imported finding (top judge score {top:.2f}).",
                replay_bundle_path=None,
            )
            n_promoted += 1

    typer.echo(
        json.dumps(
            {
                "run_id": run_result.run_id,
                "imported_traces": n_inserted,
                "promoted_findings": n_promoted,
                "db_url": resolved,
            },
            indent=2,
        )
    )


def main() -> None:  # pragma: no cover - thin wrapper for entry-point installation
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
