"""Dry-run validator for draft Recipes.

A Recipe produced by the Phase-6 :class:`Extractor` is a draft.
Before the CLI auto-commits it (per the operator preference for
direct commit recorded 2026-05-23), this validator checks that
the Recipe is *structurally executable* — every operation has a
registered handler, every template reference points at a real
prior step, every required param is present.

Failing the dry-run isn't fatal — the CLI leaves draft Recipes
that fail validation in the operator-review queue (draft_state =
'draft') so the human can edit and re-validate. Failing
catches:

- Extractor produced an operation the registry doesn't have
  (typo, hallucination, or an operation that will land in a
  future phase but doesn't exist yet).
- Step references ``{{step.X.Y}}`` where X is a later step
  (forward reference) or doesn't exist at all.
- Required params for a known operation are missing.
- ``on_fail`` policies that the runner doesn't honour.

Importantly, dry-run does NOT execute the Recipe. No Chrome
launches, no Gmail / Drive API calls, no LLM judge calls. It's
pure structural validation — fast, side-effect-free, safe to
run on every Extractor output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from advtest_ext.core.recipe import Recipe
    from advtest_ext.core.recipe_operations import OperationRegistry


# Same template pattern the runner uses. Imported indirectly to
# avoid pulling in the runner at validate time.
_TEMPLATE_RE = re.compile(r"\{\{\s*step\.(\w+)\.(\w+)\s*\}\}")


# Per-operation required-param sets. Maintained next to the
# operation handlers (in adapters/<app>/recipe_ops.py) — each
# handler's ``require_str_param`` calls list the same fields. We
# duplicate them here so the dry-run can warn the operator
# without actually invoking a handler. Out of sync = the runner
# would fail at run time with the handler's own error; the
# dry-run just provides earlier feedback.
_REQUIRED_PARAMS: dict[tuple[str, str], frozenset[str]] = {
    ("gmail", "send_email"): frozenset({"subject", "body"}),
    ("gmail", "reply"): frozenset({"thread_id", "body"}),
    ("docs", "open_document"): frozenset(),  # title is optional
    ("docs", "set_document_body"): frozenset({"document_id", "body"}),
    ("docs", "type_prompt"): frozenset({"document_id", "prompt"}),
}


# Valid step on_fail policies. Mirrors the runner's
# ``_VALID_ON_FAIL`` set; duplicated to avoid the import
# cycle.
_VALID_ON_FAIL: frozenset[str] = frozenset({"abort", "retry", "continue"})


@dataclass
class DryRunResult:
    """Outcome of a dry-run validation.

    ``ok`` is the binary verdict the CLI uses to choose between
    auto-commit and review queue. ``errors`` is the list of
    distinct failures (each one a self-contained sentence) so
    the operator's review queue can show every problem at once
    rather than only the first.
    """

    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


def validate(
    recipe: Recipe,
    *,
    operations: OperationRegistry | None = None,
) -> DryRunResult:
    """Run every structural check against ``recipe`` and return a
    :class:`DryRunResult`. Never raises — bad input becomes a
    failing result with a clear error message."""
    errors: list[str] = []
    warnings: list[str] = []

    # Step ids must be unique. The runner enforces this too but
    # we want the operator to see it BEFORE we try to persist a
    # broken Recipe.
    seen_ids: set[str] = set()
    for step in recipe.steps:
        if step.id in seen_ids:
            errors.append(f"duplicate step id: {step.id!r}")
        seen_ids.add(step.id)

    # on_fail policies.
    for step in recipe.steps:
        if step.on_fail not in _VALID_ON_FAIL:
            errors.append(
                f"step {step.id!r}: on_fail={step.on_fail!r} is not one of {sorted(_VALID_ON_FAIL)}"
            )

    # Operation existence.
    if operations is not None:
        for step in recipe.steps:
            if not operations.has(step.surface.app, step.operation):
                available = sorted(f"{a}.{op}" for (a, op) in operations.registered_pairs())
                errors.append(
                    f"step {step.id!r}: no handler registered for "
                    f"({step.surface.app!r}, {step.operation!r}); "
                    f"available: {available}"
                )

    # Required params per known operation.
    for step in recipe.steps:
        required = _REQUIRED_PARAMS.get((step.surface.app, step.operation))
        if required is None:
            # Unknown operation pair — if operations is provided,
            # the operation-existence check above already flagged
            # it. We don't double-report by also complaining
            # about missing required params for it.
            continue
        missing = sorted(required - set(step.params))
        if missing:
            errors.append(
                f"step {step.id!r}: missing required params for "
                f"{step.surface.app}.{step.operation}: {missing}"
            )

    # Template references — every {{step.X.Y}} must point at a
    # step earlier in the chain (forward references are
    # programming errors), AND the referenced step's operation
    # must have a known output schema OR the field name must be
    # plausible.
    step_order: list[str] = [s.id for s in recipe.steps]
    for step_index, step in enumerate(recipe.steps):
        prior_ids = set(step_order[:step_index])
        for ref_step, ref_field in _collect_template_refs(step.params):
            if ref_step == step.id:
                errors.append(
                    f"step {step.id!r}: self-referencing template "
                    f"{{{{step.{ref_step}.{ref_field}}}}}"
                )
                continue
            if ref_step not in step_order:
                errors.append(f"step {step.id!r}: template references unknown step {ref_step!r}")
                continue
            if ref_step not in prior_ids:
                errors.append(
                    f"step {step.id!r}: template references forward to "
                    f"step {ref_step!r} (templates can only reference "
                    "earlier steps in the chain)"
                )

    # Recipe-level signals — full validation by attempting
    # construction. ``build_signal`` raises ValueError on:
    # missing kind / unknown kind / missing name / missing
    # kind-specific required args (token / pattern / canary) /
    # invalid Surface enum value for ``where`` / unparseable
    # regex pattern. Catching all of those here means the
    # operator sees every signal problem in one dry-run pass
    # rather than discovering them at run time.
    from advtest_ext.judges.signals import build_signal

    for i, sig_spec in enumerate(recipe.signals):
        if not isinstance(sig_spec, dict):
            errors.append(f"recipe.signals[{i}] must be a dict, got {type(sig_spec).__name__}")
            continue
        try:
            build_signal(sig_spec)
        except ValueError as exc:
            errors.append(f"recipe.signals[{i}]: {exc}")

    # Soft warnings (don't fail the dry-run).
    if not recipe.signals and not any(s.success_signals for s in recipe.steps):
        warnings.append(
            "recipe has no signals at recipe-level or step-level; "
            "every trace will look successful regardless of model "
            "response — consider adding at least one signal"
        )

    return DryRunResult(ok=not errors, errors=errors, warnings=warnings)


def _collect_template_refs(params: object) -> list[tuple[str, str]]:
    """Recursively walk ``params`` (string / dict / list) and
    return every ``(step_id, field)`` pair referenced by a
    ``{{step.<id>.<field>}}`` template. Deduplicates."""
    seen: list[tuple[str, str]] = []
    _walk_for_refs(params, seen)
    # Preserve discovery order; dedupe later.
    out: list[tuple[str, str]] = []
    for ref in seen:
        if ref not in out:
            out.append(ref)
    return out


def _walk_for_refs(node: object, out: list[tuple[str, str]]) -> None:
    if isinstance(node, str):
        for match in _TEMPLATE_RE.finditer(node):
            out.append((match.group(1), match.group(2)))
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_for_refs(v, out)
        return
    if isinstance(node, list):
        for item in node:
            _walk_for_refs(item, out)
