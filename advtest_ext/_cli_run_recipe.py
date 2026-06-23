"""``advtest run-recipe`` command + its module-local helpers.

Lives in its own module so :mod:`advtest_ext.cli` stays focused on
the registries / factories / name resolution that the
``advtest run`` command needs. Recipe execution is a separate
operator surface (Phase 5) with its own helper cluster — signal
evaluation, trace persistence, account-pool derivation, session
factory, dataclass reconstruction from ORM rows.

Wire-up:

  from advtest_ext._cli_run_recipe import register
  register(app)  # attaches `run-recipe` to the Typer app

The command body and the seven helpers stay private to this
module; ``register`` is the only public surface.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from typing import Any

import typer


def register(app: typer.Typer) -> None:
    """Attach the ``run-recipe`` command to ``app``.

    Called once at CLI module load time. The command function is
    defined inside this register so its typer-decoration runs against
    the caller's app, not a module-local app.
    """

    @app.command(name="run-recipe")
    def run_recipe(
        recipe_id: str = typer.Argument(
            ...,
            help="The Recipe id (from the catalogue) to execute.",
        ),
        db_url: str | None = typer.Option(
            None,
            "--db-url",
            help=(
                "SQLAlchemy URL for the catalogue + run-result database. "
                "Same resolution rules as ``advtest run`` (CLI flag → "
                "ADVTEST_DB_URL env → sqlite:///./advtest.db)."
            ),
        ),
    ) -> None:
        """Phase-5 — execute one catalogued Recipe end-to-end.

        Loads the Recipe from the dashboard database, runs its Steps
        in order via the Recipe Runner, and prints a JSON summary of
        the outcome. Unlike ``advtest run`` (which iterates attack-
        plugin x behaviour cross-products), ``run-recipe`` is the
        multi-step path that V1-shaped human-chat Recipes need.

        This command is the operator-facing surface for Phase 5; the
        bundled operation handlers (send_email, type_prompt, etc.)
        land in a follow-up phase. Until then, Recipes referencing
        unregistered operations fail per-Step with a clear "no
        handler registered for (app, operation)" message — that's
        the expected behaviour today, not a bug.
        """
        # Lazy import to avoid a circular dep at module-load time —
        # cli imports this module to call register(), and this
        # function call from inside the command body happens after
        # cli is fully loaded.
        from advtest_ext._cli_providers import _resolve_db_url
        from advtest_ext.storage import (
            get_recipe,
            make_engine,
            make_session_factory,
            session_scope,
        )

        resolved_db = _resolve_db_url(cli_flag=db_url, config_value=None)
        if not resolved_db:
            raise typer.BadParameter(
                "run-recipe requires a database to load the Recipe from; "
                "pass --db-url or set ADVTEST_DB_URL"
            )
        engine = make_engine(resolved_db)
        factory = make_session_factory(engine)

        with session_scope(factory) as s:
            recipe_row = get_recipe(s, recipe_id)
            if recipe_row is None:
                raise typer.BadParameter(f"recipe {recipe_id!r} not found in catalogue")
            # Reconstruct the dataclass shape the runner expects from
            # the SQL row. Done at the boundary so the runner itself
            # stays free of any ORM imports.
            recipe = _recipe_from_row(recipe_row)

        # Default registry includes the Phase-5.1 bundled handlers
        # (gmail.send_email, gmail.reply, docs.open_document,
        # docs.type_prompt). Operators with custom operations can
        # extend it before passing to the runner, but the V1 chain
        # works out of the box.
        from advtest_ext.adapters._ui_mode_common import UiModeSessionPool
        from advtest_ext.core.builtin_operations import default_operations
        from advtest_ext.core.recipe_runner import RecipeRunner

        operations = default_operations()

        # SessionPool lifecycle: opened only when a Recipe's Steps
        # reference distinct account_roles AND the operator hasn't
        # passed an external pool. Closed in the finally clause so
        # Chrome contexts don't leak if the runner raises.
        account_pool = _build_account_pool_for_recipe(recipe)
        session_pool = (
            UiModeSessionPool(account_pool, _ui_mode_session_factory)
            if account_pool is not None
            else None
        )

        # Drive the entire async workload (runner + session-pool teardown
        # + signal evaluation) through ONE asyncio.run. Separate
        # asyncio.run calls each create-and-destroy their own event loop,
        # which leaves Playwright's Chromium connection bound to a closed
        # loop on the second call — Chrome then can't be shut down and
        # the CLI hangs at about:blank. One loop, one teardown.
        async def _drive() -> tuple[Any, list[dict[str, Any]]]:
            runner = RecipeRunner(
                recipe=recipe,
                operations=operations,
                account_pool=account_pool,
                session_pool=session_pool,
            )
            try:
                run_result = await runner.run()
                sig_records = await _evaluate_recipe_signals(recipe=recipe, result=run_result)
            finally:
                if session_pool is not None:
                    await session_pool.close()
            return run_result, sig_records

        result, signal_records = asyncio.run(_drive())

        # Persist the run as a Trace + SignalRecord rows so the
        # dashboard surfaces it the same way orchestrator-driven runs
        # appear.
        n_fired, n_signals = _persist_recipe_run(
            factory=factory,
            recipe=recipe,
            result=result,
            signal_records=signal_records,
        )

        typer.echo(
            json.dumps(
                {
                    "recipe_id": result.recipe_id,
                    "trace_id": result.trace_id,
                    "completed": result.completed,
                    "abort_reason": result.abort_reason,
                    "steps": [
                        {
                            "step_id": o.step_id,
                            "operation": o.operation,
                            "fired": o.fired,
                            "attempts": o.attempts,
                            "error": o.error,
                        }
                        for o in result.step_observations
                    ],
                    "final_response_excerpt": result.final_response_text[:300],
                    "signals": {"fired": n_fired, "total": n_signals},
                },
                indent=2,
            )
        )


# ----------------------------------------------------------------------
# Module-local helpers used by run_recipe. Public-ish to the package
# (no underscore prefix would still keep them private since this module
# is itself underscore-prefixed); kept private for the same reason —
# the only intended caller is the command above.
# ----------------------------------------------------------------------


async def _evaluate_recipe_signals(
    *,
    recipe: Any,
    result: Any,
) -> list[dict[str, Any]]:
    """Evaluate the Recipe's declared signal stack against the
    Run's outputs and return record dicts ready for persistence.

    Lives inside the same event loop as the runner so signal
    evaluation doesn't fight Playwright cleanup. Each signal
    runs in a try/except — a broken signal records as not-fired
    with an error reasoning string rather than aborting the
    whole stack.
    """
    from advtest_ext.core.models import Behavior
    from advtest_ext.core.orchestrator import _build_signal_stack
    from advtest_ext.judges.signals import SignalContext

    signals = _build_signal_stack(list(recipe.signals or []))
    if not signals:
        return []

    behavior = Behavior(
        id=f"recipe:{recipe.seed_id}",
        description=recipe.name,
        severity="high",
        success_criteria="signal-stack derived",
        source_citation=recipe.seed_id,
        category="instruction_override",
    )
    # Lightweight stub trace for signal evaluation: the SignalContext
    # only reads raw_response / raw_request / raw_response_data, so
    # we don't need a full AttackTrace here — that gets constructed
    # in _persist_recipe_run for the DB write.
    stub = _SignalEvalTrace(
        raw_request=_payload_text(result, recipe),
        raw_response=result.final_response_text,
        raw_response_data={"document_body": result.document_body},
    )
    ctx = SignalContext(trace=stub, behavior=behavior)  # type: ignore[arg-type]
    records: list[dict[str, Any]] = []
    for signal in signals:
        try:
            res = await signal.evaluate(context=ctx)
            records.append(
                {
                    "name": signal.name,
                    "kind": type(signal).KIND,
                    "fired": bool(res.fired),
                    "reasoning": res.reasoning,
                    "evidence": dict(res.evidence),
                }
            )
        except Exception as exc:
            records.append(
                {
                    "name": signal.name,
                    "kind": type(signal).KIND,
                    "fired": False,
                    "reasoning": f"signal raised {type(exc).__name__}: {exc}",
                    "evidence": {"error": True},
                }
            )
    return records


class _SignalEvalTrace:
    """Minimal duck-typed trace stub the SignalContext reads from
    during in-loop signal evaluation. Real AttackTrace construction
    happens in _persist_recipe_run for the DB row."""

    def __init__(
        self,
        *,
        raw_request: str,
        raw_response: str,
        raw_response_data: dict[str, Any],
    ) -> None:
        self.raw_request = raw_request
        self.raw_response = raw_response
        self.raw_response_data = raw_response_data


def _payload_text(result: Any, recipe: Any) -> str:
    """The operator-meaningful request text — first type_prompt step
    or a synthetic descriptor fallback. Used both for the stub
    signal-eval trace and the persisted AttackTrace so the dashboard
    shows the same payload in both places."""
    for o in result.step_observations:
        if o.operation == "type_prompt":
            text = o.outputs.get("rendered_prompt") or o.outputs.get("prompt") or ""
            if text:
                return str(text)
    return f"recipe:{recipe.id}"


def _persist_recipe_run(
    *,
    factory: Any,
    recipe: Any,
    result: Any,
    signal_records: list[dict[str, Any]],
) -> tuple[int, int]:
    """Persist one Recipe Run as a Trace plus pre-evaluated signal
    records.

    Returns ``(n_fired, n_signals)`` for the operator-visible
    JSON summary. Builds an :class:`AttackTrace` synthesised
    from the Recipe + RunResult, upserts a Run row keyed by a
    per-invocation id, and writes everything inside a single
    session. Signal evaluation already happened inside the
    runner's event loop (see :func:`_evaluate_recipe_signals`);
    this function is purely sync DB work.
    """
    from advtest_ext.core.models import AttackTrace, Payload
    from advtest_ext.storage import (
        insert_signal_records,
        insert_trace,
        session_scope,
        upsert_run,
    )

    primary_app = (
        result.step_observations[0].surface.get("app", "google_docs")
        if result.step_observations
        else "google_docs"
    )
    run_id = f"recipe-run-{uuid.uuid4().hex[:8]}"
    payload_text = _payload_text(result, recipe)
    payload = Payload(
        id=f"payload-{result.trace_id}",
        text=payload_text,
        generator_name="recipe_runner",
    )
    behavior_id = f"recipe:{recipe.seed_id}"

    n_fired = sum(1 for r in signal_records if r["fired"])
    # Verdict reflects the signal stack: any fire → success, all
    # clear with at least one signal → refused, no signals → None
    # (pre-judging, matches orchestrator default). Aborted runs
    # override to "error" regardless of what fired.
    if not result.completed:
        verdict: Any = "error"
    elif not signal_records:
        verdict = None
    elif n_fired > 0:
        verdict = "success"
    else:
        verdict = "refused"

    trace = AttackTrace(
        id=result.trace_id,
        run_id=run_id,
        behavior_id=behavior_id,
        payload=payload,
        target_name=primary_app,
        raw_request=payload_text,
        raw_response=result.final_response_text,
        raw_response_data={
            "document_body": result.document_body,
            "recipe_id": recipe.id,
            "step_observations": [
                {
                    "step_id": o.step_id,
                    "operation": o.operation,
                    "fired": o.fired,
                    "error": o.error,
                    "attempts": o.attempts,
                }
                for o in result.step_observations
            ],
        },
        verdict=verdict,
        metadata={"recipe": {"id": recipe.id, "seed_id": recipe.seed_id}},
    )

    with session_scope(factory) as s:
        upsert_run(
            s,
            run_id=run_id,
            target_name=primary_app,
            strategy="recipe_runner",
        )
        insert_trace(s, run_id=run_id, trace=trace)
        if signal_records:
            insert_signal_records(s, trace_id=trace.id, records=signal_records)

    return n_fired, len(signal_records)


def _build_account_pool_for_recipe(recipe: Any) -> Any:
    """Derive an :class:`AccountPool` from a Recipe by introspecting
    the account_roles its Steps reference.

    For Phase 5.1 v1 the pool resolves role → profile_dir from
    well-known operator paths:

    - ``recipient`` → ``ADVTEST_PLAYWRIGHT_PROFILE_DIR`` or
      ``~/.advtest/playwright-profile`` (legacy default)
    - ``attacker_sender`` → ``ADVTEST_PROFILE_ATTACKER_SENDER``
      or ``~/.advtest/profile-attacker-sender``
    - ``bystander`` → ``ADVTEST_PROFILE_BYSTANDER`` or
      ``~/.advtest/profile-bystander``

    Returns ``None`` when the Recipe references only ``recipient``
    (single-account legacy path — no pool needed; handlers fall
    back to the default profile inside ``ui_mode_invoke``).

    Future enhancement: load the pool from a config file instead
    of env vars + conventions, so operators can run multiple
    distinct account-pool configurations against the same
    Recipe.
    """
    from advtest_ext.core.account_pool import AccountPool, AccountSlot

    roles = {step.surface.account_role for step in recipe.steps}
    if roles <= {"recipient"}:
        return None  # legacy single-profile path

    profile_defaults = {
        "recipient": (
            os.environ.get("ADVTEST_PLAYWRIGHT_PROFILE_DIR")
            or str(Path.home() / ".advtest" / "playwright-profile")
        ),
        "attacker_sender": (
            os.environ.get("ADVTEST_PROFILE_ATTACKER_SENDER")
            or str(Path.home() / ".advtest" / "profile-attacker-sender")
        ),
        "bystander": (
            os.environ.get("ADVTEST_PROFILE_BYSTANDER")
            or str(Path.home() / ".advtest" / "profile-bystander")
        ),
    }
    slots = [
        AccountSlot(role=role, profile_dir=Path(profile_defaults[role]))
        for role in sorted(roles)
        if role in profile_defaults
    ]
    return AccountPool(slots=slots)


def _ui_mode_session_factory(profile_dir: Path) -> Any:
    """Session factory for the UiModeSessionPool. Used by
    ``run-recipe`` to construct per-account
    :class:`UiModeSession` instances on demand.

    Lives at module level (not lambda) so the type signature is
    inspectable and tests can monkey-patch it cleanly.
    """
    from advtest_ext.adapters.google_docs.ui_mode import UiModeSession

    return UiModeSession(profile_dir=profile_dir)


def _recipe_from_row(row: Any) -> Any:
    """Reconstruct a :class:`advtest_ext.core.recipe.Recipe` from a
    :class:`advtest_ext.storage.models.RecipeRow`. Bridges the
    ORM/dataclass boundary so the runner doesn't import any ORM
    types directly."""
    from advtest_ext.core.recipe import MutationSurface, Recipe, Step
    from advtest_ext.core.surface import Surface

    steps = [
        Step(
            id=step_row.step_id,
            intent=step_row.intent,
            surface=Surface(**step_row.surface),
            operation=step_row.operation,
            params=dict(step_row.params or {}),
            success_signals=list(step_row.success_signals or []),
            on_fail=step_row.on_fail,
        )
        for step_row in row.steps
    ]
    return Recipe(
        id=row.id,
        seed_id=row.seed_id,
        version=row.version,
        name=row.name,
        content_hash=row.content_hash,
        rediscovered_count=row.rediscovered_count,
        steps=steps,
        signals=list(row.signals or []),
        mutation_surface=MutationSurface(**(row.mutation_surface or {})),
        taxonomy=dict(row.taxonomy or {}),
        created_at=row.created_at,
    )
