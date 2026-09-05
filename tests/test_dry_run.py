"""Tests for the Phase-6 dry-run Recipe validator."""

from __future__ import annotations

from typing import Any

from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.core.dry_run import DryRunResult, validate
from advtest_ext.core.recipe import Recipe, Step
from advtest_ext.core.surface import Surface


def _surface(app: str = "docs", role: str = "recipient") -> Surface:
    return Surface(app=app, region="sidebar", account_role=role)  # type: ignore[arg-type]


def _step(
    step_id: str = "s",
    *,
    app: str = "docs",
    operation: str = "type_prompt",
    params: dict[str, Any] | None = None,
    on_fail: str = "abort",
    role: str = "recipient",
) -> Step:
    return Step(
        id=step_id,
        intent="t",
        surface=_surface(app=app, role=role),
        operation=operation,
        params=params or {},
        on_fail=on_fail,
    )


def _recipe(steps: list[Step], signals: list[dict[str, Any]] | None = None) -> Recipe:
    return Recipe(
        id="r",
        seed_id="s",
        name="test",
        content_hash="x",
        steps=steps,
        signals=signals or [],
    )


# ----------------------------------------------------------------------
# Basic structural checks
# ----------------------------------------------------------------------


def test_validate_passes_minimal_recipe() -> None:
    recipe = _recipe(
        [
            _step("s1", operation="type_prompt", params={"document_id": "d1", "prompt": "hi"}),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert isinstance(result, DryRunResult)
    assert result.ok is True
    assert result.errors == []


def test_validate_detects_duplicate_step_ids() -> None:
    recipe = _recipe(
        [
            _step("dup", params={"document_id": "d", "prompt": "p"}),
            _step("dup", params={"document_id": "d", "prompt": "p"}),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("duplicate step id" in e for e in result.errors)


def test_validate_detects_invalid_on_fail() -> None:
    # Construct directly — Step model accepts any string, the
    # validator catches the bad policy.
    recipe = _recipe(
        [
            _step("a", params={"document_id": "d", "prompt": "p"}, on_fail="something_weird"),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("on_fail=" in e for e in result.errors)


# ----------------------------------------------------------------------
# Operation existence
# ----------------------------------------------------------------------


def test_validate_detects_missing_operation() -> None:
    recipe = _recipe(
        [
            # Valid app, made-up operation — the registry has no
            # handler for ("docs", "do_nothing").
            _step("a", app="docs", operation="do_nothing"),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("no handler registered" in e for e in result.errors)


def test_validate_without_operations_skips_existence_check() -> None:
    """Validator should still surface other errors when operations
    aren't provided — they're an optional input."""
    recipe = _recipe(
        [
            _step("a", app="docs", operation="anything"),
        ]
    )
    result = validate(recipe, operations=None)
    # No operations registry → no "no handler" error.
    assert all("no handler" not in e for e in result.errors)


# ----------------------------------------------------------------------
# Required-param checks
# ----------------------------------------------------------------------


def test_validate_detects_missing_required_params() -> None:
    recipe = _recipe(
        [
            _step("a", operation="type_prompt", params={"prompt": "p"}),  # missing document_id
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("missing required params" in e and "document_id" in e for e in result.errors)


def test_validate_passes_when_all_required_params_present() -> None:
    recipe = _recipe(
        [
            _step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"}),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is True


def test_validate_optional_params_not_required() -> None:
    """``docs.open_document`` has no required params (title is
    optional). Empty params should still pass."""
    recipe = _recipe(
        [
            _step("a", app="docs", operation="open_document", params={}),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is True


# ----------------------------------------------------------------------
# Template-reference checks
# ----------------------------------------------------------------------


def test_validate_detects_forward_template_reference() -> None:
    """Step 1 referencing Step 2's output is invalid — templates
    can only reference earlier steps."""
    recipe = _recipe(
        [
            _step(
                "first",
                operation="type_prompt",
                params={"document_id": "{{ step.second.doc_id }}", "prompt": "p"},
            ),
            _step("second", operation="open_document"),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("forward to step" in e for e in result.errors)


def test_validate_detects_self_referencing_template() -> None:
    recipe = _recipe(
        [
            _step(
                "a",
                operation="type_prompt",
                params={"document_id": "{{ step.a.doc }}", "prompt": "p"},
            ),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("self-referencing" in e for e in result.errors)


def test_validate_detects_unknown_step_reference() -> None:
    recipe = _recipe(
        [
            _step(
                "a",
                operation="type_prompt",
                params={"document_id": "{{ step.does_not_exist.x }}", "prompt": "p"},
            ),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("unknown step" in e for e in result.errors)


def test_validate_accepts_valid_backward_reference() -> None:
    recipe = _recipe(
        [
            _step("open_doc", app="docs", operation="open_document"),
            _step(
                "type",
                operation="type_prompt",
                params={
                    "document_id": "{{ step.open_doc.document_id }}",
                    "prompt": "p",
                },
            ),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is True


# ----------------------------------------------------------------------
# Signal-spec checks
# ----------------------------------------------------------------------


def test_validate_detects_unknown_signal_kind() -> None:
    recipe = _recipe(
        [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
        signals=[{"kind": "made_up", "name": "x"}],
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("unknown signal kind" in e for e in result.errors)


def test_validate_detects_signal_missing_name() -> None:
    recipe = _recipe(
        [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
        signals=[{"kind": "token_appears", "token": "x"}],  # no name
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("missing 'name'" in e for e in result.errors)


def test_validate_accepts_well_shaped_signal_spec() -> None:
    recipe = _recipe(
        [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
        signals=[{"kind": "token_appears", "name": "x", "token": "y"}],
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is True


# ----------------------------------------------------------------------
# Signal-spec kind-specific validation — added 2026-05-25 after
# V2's extracted Recipe contained a ``where: document_body``
# signal (invalid Surface enum value) that the loose dry-run
# missed.
# ----------------------------------------------------------------------


def test_validate_detects_invalid_signal_where() -> None:
    """A non-existent Surface value should fail dry-run. Originally
    written to catch V2's ``where: document_body`` bug — that
    surface is now valid (Phase 6.x added it as a stub), so this
    uses a clearly-invalid value instead."""
    recipe = _recipe(
        [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
        signals=[
            {
                "kind": "token_appears",
                "name": "bad_where",
                "token": "x",
                "where": "made_up_surface_value",
            }
        ],
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("unknown surface" in e for e in result.errors)


def test_validate_detects_missing_signal_token() -> None:
    """token_appears requires ``token`` — missing must surface as a
    dry-run error rather than failing at run time."""
    recipe = _recipe(
        [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
        signals=[{"kind": "token_appears", "name": "no_token"}],
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("token_appears" in e for e in result.errors)


def test_validate_detects_unparseable_regex() -> None:
    """A regex_match signal with a malformed pattern must fail
    dry-run, not crash at the first trace."""
    recipe = _recipe(
        [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
        signals=[
            {
                "kind": "regex_match",
                "name": "bad_pattern",
                "pattern": "(unbalanced",
            }
        ],
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    assert any("invalid regex pattern" in e for e in result.errors)


def test_validate_accepts_signal_with_explicit_valid_where() -> None:
    """Sanity check that every documented Surface value passes."""
    for where in (
        "ui_response",
        "full_response",
        "request",
        "intercepted_traffic",
        "document_body",
    ):
        recipe = _recipe(
            [_step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"})],
            signals=[
                {
                    "kind": "token_appears",
                    "name": "ok",
                    "token": "x",
                    "where": where,
                }
            ],
        )
        result = validate(recipe, operations=default_operations())
        assert result.ok is True, f"valid where {where!r} should pass; errors: {result.errors}"


# ----------------------------------------------------------------------
# Warnings (don't fail dry-run but worth surfacing)
# ----------------------------------------------------------------------


def test_validate_warns_on_no_signals() -> None:
    recipe = _recipe(
        [
            _step("a", operation="type_prompt", params={"document_id": "d", "prompt": "p"}),
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is True  # warning doesn't fail
    assert any("no signals" in w for w in result.warnings)


# ----------------------------------------------------------------------
# DryRunResult truthiness
# ----------------------------------------------------------------------


def test_dry_run_result_is_truthy_when_ok() -> None:
    assert bool(DryRunResult(ok=True)) is True
    assert bool(DryRunResult(ok=False)) is False


def test_validate_aggregates_multiple_errors() -> None:
    """Should report ALL errors at once, not just the first."""
    recipe = _recipe(
        [
            # Valid app (Pydantic constraint), unknown operation,
            # invalid on_fail.
            _step("dup", app="docs", operation="missing", on_fail="weird"),
            _step("dup", operation="type_prompt", params={"prompt": "p"}),  # missing document_id
        ]
    )
    result = validate(recipe, operations=default_operations())
    assert result.ok is False
    # At least: duplicate id, no handler, invalid on_fail, missing param.
    assert len(result.errors) >= 3
