"""Operation registry for the Phase-5 Recipe Runner.

A Recipe Step names a Surface (app, region, account_role) and an
``operation`` string. The Recipe Runner uses this registry to map
``(app, operation)`` pairs to async handler callables — when Step 4
says ``operation: type_prompt`` on surface ``docs/sidebar``, the
runner looks up handler ``("docs", "type_prompt")`` and invokes it
with the resolved session, rendered params, and accumulated
inter-step context.

Decoupling the runner from concrete adapter calls keeps three
things clean:

- **Adapter-agnostic core.** The runner doesn't import
  ``GmailTarget`` / ``GoogleDocsTarget`` — it asks the registry.
  Future adapters (Calendar, Slides, the Gemini app) plug in by
  registering handlers; no runner changes needed.
- **Operation extensibility.** Operators can register custom
  operations for their own recipes without modifying the runner.
- **Testability.** The runner is tested against a fake registry
  with stub handlers — no Playwright, no real APIs.

Phase 5 of the :doc:`red_team_report_infra_plan`. The bundled
operation handlers (send_email, reply, open_document, type_prompt
— what V1 needs) land in a follow-up phase; this module ships
just the registry + protocol so the runner can drive any handler
an operator wires in.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.core.account_pool import AccountSlot
    from advtest_ext.core.recipe import Step


@dataclass
class OperationContext:
    """Everything an operation handler may read from.

    Constructed by the runner per Step. Template substitution has
    already been applied to ``params`` so handlers see fully-
    rendered values, not ``{{step.x.y}}`` literals.
    """

    step: Step
    """The Step being executed. Handlers read ``step.intent`` /
    ``step.surface`` / ``step.operation`` for context but should
    use ``params`` (already-rendered) for the actual call args."""

    params: dict[str, Any]
    """Step.params with all ``{{step.<id>.<field>}}`` references
    substituted. Handler-facing — never raw."""

    session: Any
    """The per-account session for the Step's account_role, or
    ``None`` when the Step doesn't need a UI session (e.g. an API-
    only operation like a Drive file creation). Resolved by the
    runner via the SessionPool."""

    account_slot: AccountSlot | None
    """The AccountSlot bound to the Step's account_role, or ``None``
    for single-account legacy Runs. Handlers that need the
    profile_dir or label read it from here."""

    accumulated_context: dict[str, Any] = field(default_factory=dict)
    """Outputs from all prior Steps, keyed by Step.id. Read-only
    from the handler's perspective — the runner manages
    accumulation."""


@dataclass
class OperationResult:
    """What a handler returns.

    ``outputs`` becomes ``accumulated_context[step.id]`` so later
    Steps can reference it via ``{{step.<id>.<field>}}``.
    ``response_text`` is the model's textual reply when the
    operation interacts with Gemini — the Recipe Runner uses the
    LAST Step's response as the trace's ``raw_response`` field
    so existing dashboard surfaces render unchanged.
    """

    outputs: dict[str, Any] = field(default_factory=dict)
    response_text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


# Async callable signature handlers must satisfy.
OperationHandler = Callable[[OperationContext], Awaitable[OperationResult]]


class OperationNotImplementedError(KeyError):
    """Raised by :meth:`OperationRegistry.get` when no handler is
    registered for the requested ``(app, operation)`` pair. The
    Recipe Runner converts this into a clean per-Step failure
    rather than letting it propagate as a stack trace."""


class OperationRegistry:
    """Pluggable map of ``(app, operation)`` → handler callable.

    Handlers register either via the :meth:`register` decorator or
    by direct mapping assignment for tests. Lookups raise
    :class:`OperationNotImplementedError` for unknown pairs so the
    runner can fail the offending Step cleanly with a clear
    message.
    """

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], OperationHandler] = {}

    def register(
        self,
        app: str,
        operation: str,
    ) -> Callable[[OperationHandler], OperationHandler]:
        """Decorator: register a handler for ``(app, operation)``.

        Usage::

            registry = OperationRegistry()

            @registry.register("docs", "type_prompt")
            async def type_prompt(ctx: OperationContext) -> OperationResult:
                ...
        """

        def decorator(handler: OperationHandler) -> OperationHandler:
            key = (app, operation)
            if key in self._handlers:
                raise ValueError(f"duplicate handler for ({app!r}, {operation!r})")
            self._handlers[key] = handler
            return handler

        return decorator

    def get(self, app: str, operation: str) -> OperationHandler:
        """Look up a handler. Raises :class:`OperationNotImplementedError`
        when no handler is registered — never returns ``None``."""
        key = (app, operation)
        if key not in self._handlers:
            available = sorted(f"{a}.{op}" for (a, op) in self._handlers)
            raise OperationNotImplementedError(
                f"no handler registered for ({app!r}, {operation!r}); available: {available}"
            )
        return self._handlers[key]

    def has(self, app: str, operation: str) -> bool:
        return (app, operation) in self._handlers

    def __len__(self) -> int:
        return len(self._handlers)

    def registered_pairs(self) -> list[tuple[str, str]]:
        """List of every registered ``(app, operation)`` pair, in
        registration order. Mostly diagnostic — useful for the
        dashboard's pluggable-operations view."""
        return list(self._handlers)


def require_str_param(params: dict[str, Any], key: str, operation: str) -> str:
    """Pull a required string param from a handler's already-rendered
    ``params``. Returns a clean ``ValueError`` when the operator
    forgot the field — not a ``KeyError`` stack trace at the API
    layer.

    Every adapter's recipe handlers want this shape of validation;
    centralising it here means a consistent error message across
    apps and one place to evolve the contract.
    """
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"operation {operation!r} requires param {key!r} to be a non-empty string")
    return value
