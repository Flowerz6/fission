"""``RecipeRunner`` — execute a :class:`~advtest_ext.core.recipe.Recipe`
end-to-end and return one trace + signal-stack verdict.

The runner is the sibling-path complement to the
existing :class:`advtest_ext.core.orchestrator.Orchestrator`.
Where the orchestrator iterates attack plugins x behaviours
x techniques, the runner iterates one Recipe's Steps in
declared order — that's the right shape for multi-step,
multi-account, multi-app red-team chains (V1 in the Google
Red Team Report).

What the runner does per Step:

1. Render ``step.params`` templates against the accumulated
   inter-step context (``{{step.<id>.<field>}}`` substitution).
2. Look up the operation handler in the
   :class:`OperationRegistry` for ``(step.surface.app,
   step.operation)``.
3. Resolve the Step's ``account_role`` to a UI session via
   the :class:`UiModeSessionPool` (when one's configured).
4. Invoke the handler with a fully-populated
   :class:`OperationContext`.
5. Stash the handler's ``outputs`` under the Step's id in
   the accumulated context so later Steps can reference it.
6. Honour ``step.on_fail`` (``abort`` / ``retry`` /
   ``continue``) on handler exceptions.

After all Steps run, the runner evaluates the Recipe's
top-level signal stack against the FINAL Step's response
text and returns a :class:`RecipeRunResult`.

Backward compatibility: this is an entirely separate
execution path from the orchestrator. Existing
``advtest run`` invocations don't touch this module. The
new ``advtest run-recipe`` CLI subcommand does.

Phase 5 of the :doc:`red_team_report_infra_plan`. Concrete
operation handlers ship in a follow-up — this phase lands
the runner skeleton + registry pattern + CLI surface so
the V1 chain becomes mechanically representable.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationNotImplementedError,
    OperationRegistry,
    OperationResult,
)

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.adapters._ui_mode_common import UiModeSessionPool
    from advtest_ext.core.account_pool import AccountPool
    from advtest_ext.core.recipe import Recipe, Step


# Match ``{{ step.<id>.<field> }}`` with arbitrary internal
# whitespace. Identifiers are word chars (matches the Step.id /
# OperationResult.outputs key conventions). Anything more exotic
# (nested fields, jinja-style filters) is deliberately out of
# scope — operators who need that build their own renderer and
# wire it into a custom OperationHandler.
_TEMPLATE_RE = re.compile(r"\{\{\s*step\.(\w+)\.(\w+)\s*\}\}")


# Step.on_fail policies the runner honours. The dataclass allows
# any string here; the runner enforces this enumeration at run
# time so a typo in YAML produces a clean error.
_VALID_ON_FAIL = frozenset({"abort", "retry", "continue"})

# Hard cap on retry attempts when ``on_fail: retry``. Conservative
# — Phase 5 v1 doesn't expose this as a knob; bounded retries
# protect against handlers that infinitely loop on a transient
# error. Operators can override per-step in a future enhancement.
_MAX_RETRIES_PER_STEP = 3


# ----------------------------------------------------------------------
# Result dataclasses
# ----------------------------------------------------------------------


@dataclass
class StepObservation:
    """What happened during one Step's execution. Persisted on the
    Trace under ``extra_metadata["recipe"]["step_observations"]``
    so the dashboard can render a per-Step timeline."""

    step_id: str
    operation: str
    surface: dict[str, Any]
    fired: bool  # whether the operation handler completed without abort
    response_text: str
    outputs: dict[str, Any]
    raw: dict[str, Any]
    error: str | None
    attempts: int
    """Number of handler invocations made (1 = first try; >1 means
    ``on_fail: retry`` re-invoked the handler)."""


@dataclass
class RecipeRunResult:
    """End-state of one Recipe execution.

    ``trace_id`` is a normal trace id — the runner persists a
    single trace per Recipe Run for back-compat with all existing
    dashboard surfaces (heatmap, findings, replay bundles). Per-
    Step observations live inside that trace's metadata so the
    Recipe-detail page can render a step timeline without a new
    table.
    """

    recipe_id: str
    trace_id: str
    completed: bool  # True iff every Step ran without aborting
    final_response_text: str
    step_observations: list[StepObservation] = field(default_factory=list)
    accumulated_context: dict[str, Any] = field(default_factory=dict)
    abort_reason: str | None = None
    document_body: str = ""
    """Editor body text captured by the last UI step that opened a
    Google Doc. Sourced from Playwright (``_read_doc_body_text``);
    empty when no UI step ran. The CLI persists this on the
    Trace's ``raw_response_data['document_body']`` so
    ``SignalContext.document_body`` can score against it."""


class RecipeStepAborted(Exception):  # noqa: N818 — surfaces as control flow, not an error
    """Raised when a Step fails AND its ``on_fail`` policy is
    ``abort``. Caught by the runner to populate
    ``RecipeRunResult.abort_reason``."""


# ----------------------------------------------------------------------
# Template substitution
# ----------------------------------------------------------------------


class TemplateRenderError(ValueError):
    """A Step's params reference an inter-step variable that
    hasn't been populated. The runner raises this with a clear
    message naming the missing key."""


def render_templates(
    params: dict[str, Any],
    accumulated_context: dict[str, Any],
) -> dict[str, Any]:
    """Substitute ``{{step.<id>.<field>}}`` references in every
    string value (recursively for dicts and lists) of ``params``.

    Non-string values pass through unchanged. Missing references
    raise :class:`TemplateRenderError` — the runner converts that
    into a per-Step failure so the operator sees which Step's
    template failed and which key was missing.
    """
    rendered = _render(params, accumulated_context)
    assert isinstance(rendered, dict)
    return rendered


def _render(value: Any, ctx: dict[str, Any]) -> Any:
    if isinstance(value, str):
        return _render_string(value, ctx)
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(item, ctx) for item in value]
    return value


def _render_string(value: str, ctx: dict[str, Any]) -> str:
    def substitute(match: re.Match[str]) -> str:
        step_id, field_name = match.group(1), match.group(2)
        if step_id not in ctx:
            raise TemplateRenderError(
                f"template references unknown step {step_id!r}; "
                f"available steps so far: {sorted(ctx)}"
            )
        step_outputs = ctx[step_id]
        if not isinstance(step_outputs, dict) or field_name not in step_outputs:
            available = sorted(step_outputs) if isinstance(step_outputs, dict) else []
            raise TemplateRenderError(
                f"template references unknown field {field_name!r} "
                f"on step {step_id!r}; available: {available}"
            )
        return str(step_outputs[field_name])

    return _TEMPLATE_RE.sub(substitute, value)


# ----------------------------------------------------------------------
# The runner
# ----------------------------------------------------------------------


class RecipeRunner:
    """Drive one Recipe to completion."""

    def __init__(
        self,
        *,
        recipe: Recipe,
        operations: OperationRegistry,
        account_pool: AccountPool | None = None,
        session_pool: UiModeSessionPool | None = None,
    ) -> None:
        # Validate up-front so the operator sees configuration
        # errors before any Step runs. The cost is one pass over
        # the Recipe at construction time.
        self._validate(recipe)
        self.recipe = recipe
        self.operations = operations
        self.account_pool = account_pool
        self.session_pool = session_pool

    @staticmethod
    def _validate(recipe: Recipe) -> None:
        if not recipe.steps:
            raise ValueError("recipe has no steps to run")
        for step in recipe.steps:
            if step.on_fail not in _VALID_ON_FAIL:
                raise ValueError(
                    f"step {step.id!r}: on_fail={step.on_fail!r} is invalid; "
                    f"must be one of {sorted(_VALID_ON_FAIL)}"
                )
        seen_ids: set[str] = set()
        for step in recipe.steps:
            if step.id in seen_ids:
                raise ValueError(f"duplicate step id {step.id!r} in recipe; ids must be unique")
            seen_ids.add(step.id)

    async def run(self) -> RecipeRunResult:
        """Execute every Step in order. Stops early on ``abort``;
        otherwise runs to completion regardless of per-Step
        verdicts."""
        accumulated: dict[str, Any] = {}
        observations: list[StepObservation] = []
        final_response = ""
        document_body = ""
        aborted_reason: str | None = None

        for step in self.recipe.steps:
            obs = await self._run_one_step(step, accumulated)
            observations.append(obs)
            # Carry handler outputs forward even on failed steps —
            # operators sometimes want a downstream Step to react to
            # an earlier failure (e.g. log the partial state).
            accumulated[step.id] = obs.outputs
            if obs.response_text:
                final_response = obs.response_text
            # UI handlers (ui_mode_invoke) stash the captured editor
            # body on OperationResult.raw["document_body"]. Take the
            # most recent non-empty reading so signals score against
            # the editor's final visible state.
            body = obs.raw.get("document_body") if isinstance(obs.raw, dict) else None
            if isinstance(body, str) and body:
                document_body = body
            if obs.error is not None and step.on_fail == "abort":
                aborted_reason = f"step {step.id!r} aborted the recipe: {obs.error}"
                break

        return RecipeRunResult(
            recipe_id=self.recipe.id,
            # Trace id is allocated here; the persistence layer
            # (CLI subcommand) reuses it when writing the row.
            trace_id=f"trace-{uuid.uuid4().hex[:12]}",
            completed=aborted_reason is None,
            final_response_text=final_response,
            step_observations=observations,
            accumulated_context=accumulated,
            abort_reason=aborted_reason,
            document_body=document_body,
        )

    async def _run_one_step(
        self,
        step: Step,
        accumulated: dict[str, Any],
    ) -> StepObservation:
        """Run one Step with on_fail-aware retry. Always returns a
        :class:`StepObservation`; never raises out to the caller."""
        attempts = 0
        last_error: str | None = None
        last_result: OperationResult = OperationResult()

        max_attempts = _MAX_RETRIES_PER_STEP if step.on_fail == "retry" else 1
        while attempts < max_attempts:
            attempts += 1
            try:
                rendered_params = render_templates(step.params, accumulated)
            except TemplateRenderError as exc:
                last_error = f"template render failed: {exc}"
                # Template errors are deterministic; retrying
                # doesn't help. Break out of the retry loop.
                break

            try:
                handler = self.operations.get(step.surface.app, step.operation)
            except OperationNotImplementedError as exc:
                last_error = str(exc)
                break

            try:
                session, account_slot = await self._resolve_session(step)
            except KeyError as exc:
                # Recipe references an unbound role. Surface as a
                # per-Step failure with the AccountPool's helpful
                # "available roles: [...]" message intact.
                last_error = f"session resolution failed: {exc}"
                break
            ctx = OperationContext(
                step=step,
                params=rendered_params,
                session=session,
                account_slot=account_slot,
                accumulated_context=accumulated,
            )

            try:
                last_result = await handler(ctx)
            except Exception as exc:
                last_error = f"handler raised {type(exc).__name__}: {exc}"
                continue  # let the retry loop decide whether to try again

            # Handler returned cleanly. Check whether it self-
            # reported an error (a soft failure the handler wants
            # the runner to honour, e.g. a guardrail-triggered
            # refusal without raising).
            if last_result.error is not None:
                last_error = last_result.error
                continue

            # Success.
            last_error = None
            break

        return StepObservation(
            step_id=step.id,
            operation=step.operation,
            surface=step.surface.model_dump(),
            fired=last_error is None,
            response_text=last_result.response_text,
            outputs=dict(last_result.outputs),
            raw=dict(last_result.raw),
            error=last_error,
            attempts=attempts,
        )

    async def _resolve_session(
        self,
        step: Step,
    ) -> tuple[Any, Any]:
        """Look up the per-account session + account slot for a
        Step.

        The two are resolved independently. When an ``account_pool``
        is configured the Step's role is resolved to its slot even if
        there is no ``session_pool`` — API-based handlers
        (e.g. gmail.send_email) need the slot's ``token_cache`` to act
        as that account without ever opening a browser session. The
        UI ``session`` is resolved only when a ``session_pool`` exists.
        Returns ``(None, None)`` when neither is configured (legacy
        single-account path)."""
        role = step.surface.account_role
        slot = self.account_pool.get_slot(role) if self.account_pool is not None else None
        session = (
            await self.session_pool.session_for(role) if self.session_pool is not None else None
        )
        return session, slot
