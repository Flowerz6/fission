"""Tests for the Phase-5 Recipe Runner.

Pure logic — no Playwright, no real adapters. Operation handlers
are tiny coroutines that record their invocations + return
fixed OperationResults so the runner's loop, template
substitution, retry/abort semantics, and observation capture can
be asserted in isolation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from advtest_ext.adapters._ui_mode_common import UiModeSessionPool
from advtest_ext.core.account_pool import AccountPool, AccountSlot
from advtest_ext.core.recipe import Recipe, Step
from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationNotImplementedError,
    OperationRegistry,
    OperationResult,
)
from advtest_ext.core.recipe_runner import (
    RecipeRunner,
    TemplateRenderError,
    render_templates,
)
from advtest_ext.core.surface import Surface


def _surface(app: str = "docs", role: str = "recipient") -> Surface:
    return Surface(app=app, region="sidebar", account_role=role)  # type: ignore[arg-type]


def _step(
    step_id: str = "s1",
    *,
    app: str = "docs",
    operation: str = "type_prompt",
    params: dict[str, Any] | None = None,
    on_fail: str = "abort",
    role: str = "recipient",
) -> Step:
    return Step(
        id=step_id,
        intent=f"test {step_id}",
        surface=_surface(app=app, role=role),
        operation=operation,
        params=params or {},
        on_fail=on_fail,
    )


def _recipe(steps: list[Step]) -> Recipe:
    return Recipe(
        id="r-test",
        seed_id="s-test",
        name="test recipe",
        content_hash="sha256:test",
        steps=steps,
    )


# ----------------------------------------------------------------------
# Template substitution
# ----------------------------------------------------------------------


def test_render_templates_substitutes_strings() -> None:
    out = render_templates(
        {"to": "{{ step.plant.thread_id }}", "subject": "static"},
        {"plant": {"thread_id": "abc-123"}},
    )
    assert out == {"to": "abc-123", "subject": "static"}


def test_render_templates_recurses_into_dicts_and_lists() -> None:
    out = render_templates(
        {
            "outer": {"inner": "{{ step.a.value }}"},
            "list": ["plain", "{{ step.a.value }}", 42],
        },
        {"a": {"value": "X"}},
    )
    assert out["outer"]["inner"] == "X"
    assert out["list"] == ["plain", "X", 42]


def test_render_templates_passes_non_strings_unchanged() -> None:
    out = render_templates(
        {"num": 42, "bool": True, "none": None, "str": "no template"},
        {},
    )
    assert out == {"num": 42, "bool": True, "none": None, "str": "no template"}


def test_render_templates_handles_whitespace_inside_braces() -> None:
    out = render_templates({"x": "{{step.a.v}}"}, {"a": {"v": "1"}})
    assert out["x"] == "1"
    out2 = render_templates({"x": "{{  step.a.v  }}"}, {"a": {"v": "2"}})
    assert out2["x"] == "2"


def test_render_templates_unknown_step_raises() -> None:
    with pytest.raises(TemplateRenderError, match="unknown step"):
        render_templates({"x": "{{ step.missing.f }}"}, {})


def test_render_templates_unknown_field_raises() -> None:
    with pytest.raises(TemplateRenderError, match="unknown field"):
        render_templates({"x": "{{ step.a.missing }}"}, {"a": {"v": "1"}})


# ----------------------------------------------------------------------
# OperationRegistry
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registry_decorator_registers_and_dispatches() -> None:
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _handler(ctx: OperationContext) -> OperationResult:
        return OperationResult(response_text="ok")

    handler = reg.get("docs", "type_prompt")
    res = await handler(
        OperationContext(
            step=_step(),
            params={},
            session=None,
            account_slot=None,
        )
    )
    assert res.response_text == "ok"


def test_registry_rejects_duplicate_registration() -> None:
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _h1(ctx: OperationContext) -> OperationResult:
        return OperationResult()

    with pytest.raises(ValueError, match="duplicate handler"):

        @reg.register("docs", "type_prompt")
        async def _h2(ctx: OperationContext) -> OperationResult:
            return OperationResult()


def test_registry_get_unknown_raises_operation_not_implemented() -> None:
    reg = OperationRegistry()
    with pytest.raises(OperationNotImplementedError, match="no handler"):
        reg.get("nowhere", "do_nothing")


def test_registry_has_and_pairs() -> None:
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _h(ctx: OperationContext) -> OperationResult:
        return OperationResult()

    assert reg.has("docs", "type_prompt")
    assert not reg.has("gmail", "send_email")
    assert reg.registered_pairs() == [("docs", "type_prompt")]
    assert len(reg) == 1


# ----------------------------------------------------------------------
# RecipeRunner — validation
# ----------------------------------------------------------------------


def test_runner_rejects_empty_recipe() -> None:
    with pytest.raises(ValueError, match="no steps"):
        RecipeRunner(recipe=_recipe([]), operations=OperationRegistry())


def test_runner_rejects_duplicate_step_ids() -> None:
    with pytest.raises(ValueError, match="duplicate step id"):
        RecipeRunner(
            recipe=_recipe([_step("dup"), _step("dup")]),
            operations=OperationRegistry(),
        )


def test_runner_rejects_invalid_on_fail() -> None:
    with pytest.raises(ValueError, match="on_fail="):
        RecipeRunner(
            recipe=_recipe([_step(on_fail="not_a_policy")]),
            operations=OperationRegistry(),
        )


# ----------------------------------------------------------------------
# RecipeRunner — happy path
# ----------------------------------------------------------------------


def _registry_with_passthrough() -> OperationRegistry:
    """Registry whose handler echoes params into outputs + sets a
    response text. Lets tests exercise step ordering + template
    substitution without needing per-test handlers."""
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _passthrough(ctx: OperationContext) -> OperationResult:
        return OperationResult(
            outputs=dict(ctx.params),
            response_text=str(ctx.params.get("prompt", "")),
        )

    return reg


@pytest.mark.asyncio
async def test_runner_executes_single_step() -> None:
    recipe = _recipe([_step(params={"prompt": "hello"})])
    runner = RecipeRunner(recipe=recipe, operations=_registry_with_passthrough())
    result = await runner.run()
    assert result.completed is True
    assert result.abort_reason is None
    assert result.final_response_text == "hello"
    assert len(result.step_observations) == 1
    obs = result.step_observations[0]
    assert obs.fired is True
    assert obs.error is None
    assert obs.attempts == 1


@pytest.mark.asyncio
async def test_runner_passes_outputs_between_steps_via_template() -> None:
    """Step 2 reads a value Step 1 wrote — proves the
    template-substitution pass uses live accumulated context."""
    recipe = _recipe(
        [
            _step("first", params={"prompt": "thread_42"}),
            _step("second", params={"prompt": "got {{ step.first.prompt }}"}),
        ]
    )
    runner = RecipeRunner(recipe=recipe, operations=_registry_with_passthrough())
    result = await runner.run()
    assert result.completed is True
    assert result.step_observations[1].response_text == "got thread_42"


@pytest.mark.asyncio
async def test_runner_uses_last_step_response_as_final() -> None:
    recipe = _recipe(
        [
            _step("a", params={"prompt": "first"}),
            _step("b", params={"prompt": "second"}),
            _step("c", params={"prompt": "third"}),
        ]
    )
    runner = RecipeRunner(recipe=recipe, operations=_registry_with_passthrough())
    result = await runner.run()
    assert result.final_response_text == "third"


# ----------------------------------------------------------------------
# RecipeRunner — failure paths
# ----------------------------------------------------------------------


def _registry_with_explosive_handler() -> OperationRegistry:
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _boom(ctx: OperationContext) -> OperationResult:
        raise RuntimeError("kaboom")

    return reg


@pytest.mark.asyncio
async def test_runner_abort_stops_recipe() -> None:
    recipe = _recipe(
        [
            _step("a", on_fail="abort"),
            _step("b", on_fail="abort"),
        ]
    )
    runner = RecipeRunner(recipe=recipe, operations=_registry_with_explosive_handler())
    result = await runner.run()
    assert result.completed is False
    assert "kaboom" in (result.abort_reason or "")
    # Only step "a" was attempted.
    assert len(result.step_observations) == 1
    assert result.step_observations[0].fired is False


@pytest.mark.asyncio
async def test_runner_continue_proceeds_past_failed_step() -> None:
    recipe = _recipe(
        [
            _step("a", on_fail="continue"),
            _step("b", on_fail="abort"),
        ]
    )

    # Mix: step a uses the explosive registry, step b uses a
    # passthrough. Both register under "docs/type_prompt" so we
    # need different operations.
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _passthrough(ctx: OperationContext) -> OperationResult:
        if ctx.step.id == "a":
            raise RuntimeError("a fails")
        return OperationResult(response_text="b succeeded")

    runner = RecipeRunner(recipe=recipe, operations=reg)
    result = await runner.run()
    assert result.completed is True  # b ran, recipe finished
    assert result.step_observations[0].fired is False
    assert result.step_observations[1].fired is True
    assert result.final_response_text == "b succeeded"


@pytest.mark.asyncio
async def test_runner_retry_retries_then_succeeds() -> None:
    """``on_fail: retry`` retries the handler up to the bounded
    cap. Test handler that fails twice then succeeds; runner
    should record attempts=3."""
    calls = {"n": 0}
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _flaky(ctx: OperationContext) -> OperationResult:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError(f"transient {calls['n']}")
        return OperationResult(response_text="finally")

    recipe = _recipe([_step(on_fail="retry")])
    runner = RecipeRunner(recipe=recipe, operations=reg)
    result = await runner.run()
    assert result.completed is True
    assert result.step_observations[0].attempts == 3
    assert result.step_observations[0].fired is True


@pytest.mark.asyncio
async def test_runner_retry_gives_up_after_cap() -> None:
    """Always-failing handler under ``on_fail: retry`` exhausts the
    retry cap and the step is recorded as failed."""
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _always_fails(ctx: OperationContext) -> OperationResult:
        raise RuntimeError("permanent")

    recipe = _recipe([_step(on_fail="retry")])
    runner = RecipeRunner(recipe=recipe, operations=reg)
    result = await runner.run()
    assert result.completed is True  # retry doesn't abort the recipe
    assert result.step_observations[0].fired is False
    assert result.step_observations[0].attempts == 3  # _MAX_RETRIES_PER_STEP


@pytest.mark.asyncio
async def test_runner_handler_soft_error_recorded() -> None:
    """Handler that returns OperationResult(error="...") should be
    treated as a soft failure — not raised, recorded as
    ``fired=False`` with the error message preserved."""
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _soft_fail(ctx: OperationContext) -> OperationResult:
        return OperationResult(error="guardrail tripped")

    recipe = _recipe([_step(on_fail="continue")])
    runner = RecipeRunner(recipe=recipe, operations=reg)
    result = await runner.run()
    assert result.step_observations[0].fired is False
    assert result.step_observations[0].error == "guardrail tripped"


@pytest.mark.asyncio
async def test_runner_template_render_error_is_per_step_failure() -> None:
    """A template that references an unknown step is a clean
    per-step failure, not a runner crash."""
    recipe = _recipe(
        [
            _step("a", params={"prompt": "{{ step.does_not_exist.field }}"}, on_fail="abort"),
        ]
    )
    runner = RecipeRunner(recipe=recipe, operations=_registry_with_passthrough())
    result = await runner.run()
    assert result.completed is False
    assert "template render failed" in (result.step_observations[0].error or "")


@pytest.mark.asyncio
async def test_runner_missing_operation_is_per_step_failure() -> None:
    """A Step naming an unregistered operation fails cleanly."""
    recipe = _recipe([_step(operation="not_registered", on_fail="abort")])
    runner = RecipeRunner(recipe=recipe, operations=OperationRegistry())
    result = await runner.run()
    assert result.completed is False
    assert "no handler registered" in (result.step_observations[0].error or "")


# ----------------------------------------------------------------------
# Account/session resolution
# ----------------------------------------------------------------------


class _FakeSession:
    """Same shape as in test_account_pool.py — records its profile dir."""

    def __init__(self, profile_dir: Path) -> None:
        self.profile_dir = profile_dir
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_runner_resolves_session_via_pool(tmp_path: Path) -> None:
    """When a session_pool is configured, the handler's context
    receives the per-account session AND slot."""
    pool = AccountPool(
        slots=[
            AccountSlot(role="recipient", profile_dir=tmp_path / "rcp"),
        ]
    )
    sp = UiModeSessionPool(pool, _FakeSession)
    captured: dict[str, Any] = {}
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _capturer(ctx: OperationContext) -> OperationResult:
        captured["session"] = ctx.session
        captured["slot"] = ctx.account_slot
        return OperationResult()

    recipe = _recipe([_step(role="recipient")])
    runner = RecipeRunner(
        recipe=recipe,
        operations=reg,
        account_pool=pool,
        session_pool=sp,
    )
    await runner.run()
    assert isinstance(captured["session"], _FakeSession)
    assert captured["session"].profile_dir == tmp_path / "rcp"
    assert captured["slot"].role == "recipient"


@pytest.mark.asyncio
async def test_runner_passes_none_session_when_no_pool() -> None:
    """Backward compat: no pool → handler sees ``session=None``,
    ``account_slot=None``. The handler is expected to either work
    against a default (legacy single-profile) path or fail
    cleanly with a meaningful message."""
    captured: dict[str, Any] = {}
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _capturer(ctx: OperationContext) -> OperationResult:
        captured["session"] = ctx.session
        captured["slot"] = ctx.account_slot
        return OperationResult()

    recipe = _recipe([_step()])
    runner = RecipeRunner(recipe=recipe, operations=reg)
    await runner.run()
    assert captured["session"] is None
    assert captured["slot"] is None


@pytest.mark.asyncio
async def test_runner_session_lookup_for_unbound_role_fails_step(
    tmp_path: Path,
) -> None:
    """Recipe references a role that isn't in the AccountPool — the
    step's handler invocation surfaces the KeyError as a step
    failure with a meaningful message, doesn't crash the runner."""
    pool = AccountPool(
        slots=[
            AccountSlot(role="recipient", profile_dir=tmp_path / "rcp"),
        ]
    )
    sp = UiModeSessionPool(pool, _FakeSession)
    reg = OperationRegistry()

    @reg.register("docs", "type_prompt")
    async def _h(ctx: OperationContext) -> OperationResult:
        return OperationResult()

    recipe = _recipe([_step(role="attacker_sender", on_fail="abort")])
    runner = RecipeRunner(
        recipe=recipe,
        operations=reg,
        account_pool=pool,
        session_pool=sp,
    )
    result = await runner.run()
    assert result.completed is False
    assert result.step_observations[0].fired is False
    assert "no slot bound to role" in (result.step_observations[0].error or "")
