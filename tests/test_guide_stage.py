from __future__ import annotations

from typing import Any

import pytest

from advtest_ext.core.recipe_operations import OperationRegistry, OperationResult
from advtest_ext.guides import Guide
from advtest_ext.guides.stage import (
    bind_setup_target_options,
    build_setup_recipe,
    stage_guide,
    verify_setup_result,
)


def _guide() -> Guide:
    return Guide.model_validate(
        {
            "id": "staged-guide",
            "objective": "test staging",
            "target": {"app": "gmail"},
            "success": {"rubric": "done"},
            "setup_steps": [
                {
                    "id": "plant",
                    "intent": "plant carrier",
                    "app": "gmail",
                    "account_role": "attacker_sender",
                    "operation": "send_email",
                    "params": {"to": "{{victim_email}}", "subject": "s", "body": "b"},
                }
            ],
            "provenance": {"source": "test"},
        }
    )


def test_build_setup_recipe_renders_operator_variables() -> None:
    recipe = build_setup_recipe(_guide(), {"victim_email": "victim@example.test"})
    assert recipe.steps[0].params["to"] == "victim@example.test"
    assert recipe.steps[0].surface.account_role == "attacker_sender"


def test_build_setup_recipe_rejects_missing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADVTEST_VICTIM_EMAIL", raising=False)  # no preset → still fails loud
    with pytest.raises(ValueError, match="victim_email"):
        build_setup_recipe(_guide())


def test_build_setup_recipe_resolves_victim_email_from_env_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dashboard launch passes no variables; the standing ADVTEST_VICTIM_EMAIL
    preset must resolve {{victim_email}} so the run isn't blocked."""
    monkeypatch.setenv("ADVTEST_VICTIM_EMAIL", "preset@example.test")
    recipe = build_setup_recipe(_guide())
    assert recipe.steps[0].params["to"] == "preset@example.test"


def test_explicit_variable_overrides_env_preset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADVTEST_VICTIM_EMAIL", "preset@example.test")
    recipe = build_setup_recipe(_guide(), {"victim_email": "explicit@example.test"})
    assert recipe.steps[0].params["to"] == "explicit@example.test"


async def test_stage_guide_executes_registered_setup_operation() -> None:
    seen: list[dict[str, Any]] = []
    registry = OperationRegistry()

    @registry.register("gmail", "send_email")
    async def send(ctx: Any) -> OperationResult:
        seen.append(ctx.params)
        return OperationResult(outputs={"thread_id": "t1"})

    result = await stage_guide(
        _guide(),
        variables={"victim_email": "victim@example.test"},
        operations=registry,
    )
    assert result is not None and result.completed
    assert seen == [{"to": "victim@example.test", "subject": "s", "body": "b"}]
    verify_setup_result(_guide(), result)
    assert bind_setup_target_options(_guide(), result)["thread_id"] == "t1"
