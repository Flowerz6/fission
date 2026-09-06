"""Execute a Guide's out-of-band setup steps before adaptive search."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from advtest_ext.core.builtin_operations import default_operations
from advtest_ext.core.recipe import MutationSurface, Recipe, Step
from advtest_ext.core.recipe_runner import RecipeRunner, RecipeRunResult
from advtest_ext.core.surface import Surface
from advtest_ext.document.document import AbstractDocument
from advtest_ext.guides.models import Guide

if TYPE_CHECKING:
    from advtest_ext.adapters._ui_mode_common import UiModeSessionPool
    from advtest_ext.core.account_pool import AccountPool
    from advtest_ext.core.recipe_operations import OperationRegistry
    from advtest_ext.core.surface import AccountRole, App, Region

_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_APP = {"google_docs": "docs", "google_sheets": "sheets"}
_ROLE = {
    "recipient": "recipient",
    "victim": "recipient",
    "self": "recipient",
    "attacker": "attacker_sender",
    "attacker_sender": "attacker_sender",
    "bystander": "bystander",
}

# Per-role operator conventions for resolving a concrete account: a Chrome
# ``profile_dir`` (UI/side-panel ops) and a ``token_cache`` OAuth JSON (API ops
# like gmail.send_email). Each is ``(env_var, default_under_~/.advtest)``. Set
# the env var to point a role at a specific signed-in account; the defaults let
# the common two-account setup work with zero config once the profiles/tokens
# are placed at the conventional paths. Mirrors
# ``_cli_run_recipe._build_account_pool_for_recipe`` and extends it with tokens.
_ROLE_PROFILE_ENV: dict[str, tuple[str, str]] = {
    "recipient": ("ADVTEST_PLAYWRIGHT_PROFILE_DIR", "playwright-profile"),
    "attacker_sender": ("ADVTEST_PROFILE_ATTACKER_SENDER", "profile-attacker-sender"),
    "bystander": ("ADVTEST_PROFILE_BYSTANDER", "profile-bystander"),
}
_ROLE_TOKEN_ENV: dict[str, tuple[str, str]] = {
    "recipient": ("ADVTEST_OAUTH_TOKEN_CACHE", "oauth-token.json"),
    "attacker_sender": ("ADVTEST_OAUTH_TOKEN_ATTACKER_SENDER", "oauth-token-attacker-sender.json"),
    "bystander": ("ADVTEST_OAUTH_TOKEN_BYSTANDER", "oauth-token-bystander.json"),
}
_ADVTEST_HOME = Path.home() / ".advtest"


def setup_roles(guide: Guide) -> set[str]:
    """The distinct (mapped) account roles the guide's setup steps drive.

    Empty when the guide has no setup steps. Raises for an unknown source
    role — the same failure ``build_setup_recipe`` would raise, surfaced
    earlier so pool construction fails loudly and consistently."""
    roles: set[str] = set()
    for step in guide.setup_steps:
        role = _ROLE.get(step.account_role)
        if role is None:
            raise ValueError(f"unsupported setup account role {step.account_role!r}")
        roles.add(role)
    return roles


def build_setup_account_pool(guide: Guide) -> AccountPool | None:
    """Derive an :class:`AccountPool` for a guide's setup steps from the
    per-role operator conventions above.

    Returns ``None`` when the setup needs no distinct second account (no
    setup steps, or only the ``recipient`` role) — the legacy single-account
    path then applies and handlers fall back to the default identity. When a
    step drives the ``attacker_sender`` (or ``bystander``) role, a pool is
    built binding each referenced role to its conventional profile + OAuth
    token, so e.g. gmail.send_email plants an email AS the attacker account
    while the side-panel target stays the recipient."""
    from advtest_ext.core.account_pool import AccountPool, AccountSlot

    roles = setup_roles(guide)
    if roles <= {"recipient"}:
        return None
    slots: list[AccountSlot] = []
    for role in sorted(roles):
        prof_env, prof_default = _ROLE_PROFILE_ENV[role]
        tok_env, tok_default = _ROLE_TOKEN_ENV[role]
        profile_dir = Path(os.environ.get(prof_env) or (_ADVTEST_HOME / prof_default))
        token_cache = Path(os.environ.get(tok_env) or (_ADVTEST_HOME / tok_default))
        slots.append(
            AccountSlot(
                role=cast("AccountRole", role),
                profile_dir=profile_dir,
                token_cache=token_cache,
                label=None,
            )
        )
    return AccountPool(slots=slots)


def _render_variables(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render_variables(item, variables) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_variables(item, variables) for item in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise ValueError(f"missing setup variable {name!r}")
        return variables[name]

    return _VAR_RE.sub(replace, value)


def _region(operation: str) -> Region:
    if operation in {"send_email", "reply"}:
        return "compose"
    if operation == "type_prompt":
        return "sidebar"
    return "main"


def build_setup_recipe(guide: Guide, variables: dict[str, str] | None = None) -> Recipe:
    """Project executable Guide steps into the existing RecipeRunner shape."""
    values = {"exemplar_turns": "\n".join(guide.exemplar_turns), **(variables or {})}
    steps: list[Step] = []
    for source in guide.setup_steps:
        app = cast("App", _APP.get(source.app, source.app))
        role = _ROLE.get(source.account_role)
        if role is None:
            raise ValueError(f"unsupported setup account role {source.account_role!r}")
        surface = Surface(
            app=app,
            region=_region(source.operation),
            account_role=cast("AccountRole", role),
        )
        steps.append(
            Step(
                id=source.id,
                intent=source.intent,
                surface=surface,
                operation=source.operation,
                params=_render_variables(source.params, values),
                on_fail="abort",
            )
        )
    canonical = json.dumps([step.model_dump(mode="json") for step in steps], sort_keys=True)
    return Recipe(
        id=f"guide-setup-{guide.id}",
        seed_id=guide.id,
        name=f"Setup for {guide.title or guide.id}",
        content_hash=hashlib.sha256(canonical.encode()).hexdigest(),
        steps=steps,
        signals=[],
        mutation_surface=MutationSurface(),
    )


async def stage_guide(
    guide: Guide,
    *,
    variables: dict[str, str] | None = None,
    operations: OperationRegistry | None = None,
    account_pool: AccountPool | None = None,
    session_pool: UiModeSessionPool | None = None,
) -> RecipeRunResult | None:
    """Execute setup steps and return their observations; no-op when empty."""
    if not guide.setup_steps:
        return None
    recipe = build_setup_recipe(guide, variables)
    runner = RecipeRunner(
        recipe=recipe,
        operations=operations or default_operations(),
        account_pool=account_pool,
        session_pool=session_pool,
    )
    return await runner.run()


def bind_setup_target_options(
    guide: Guide,
    result: RecipeRunResult | None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind staged carrier identifiers into the live target constructor."""
    bound = dict(options or {})
    if result is None:
        return bound
    mappings = {
        "google_docs": ("document_id", "doc_id"),
        "google_sheets": ("spreadsheet_id", "sheet_id"),
        "gmail": ("thread_id", "thread_id"),
    }
    mapping = mappings.get(guide.target.app)
    if mapping is None:
        return bound
    source_key, option_key = mapping
    for observation in reversed(result.step_observations):
        value = observation.outputs.get(source_key)
        if isinstance(value, str) and value:
            bound.setdefault(option_key, value)
            break
    return bound


def verify_setup_result(guide: Guide, result: RecipeRunResult | None) -> None:
    """Validate staging evidence before any live search query is allowed."""
    if guide.setup_steps and result is None:
        raise RuntimeError("guide setup produced no result")
    if result is None:
        return
    if not result.completed:
        raise RuntimeError(result.abort_reason or "guide setup failed")
    failures = [obs.step_id for obs in result.step_observations if not obs.fired or obs.error]
    if failures:
        raise RuntimeError(f"setup steps did not complete: {', '.join(failures)}")
    required = list(guide.setup_verification.required_outputs) if guide.setup_verification else []
    available = {
        key
        for observation in result.step_observations
        for key, value in observation.outputs.items()
        if value not in (None, "")
    }
    missing = set(required) - available
    if missing:
        raise RuntimeError(f"setup verification missing outputs: {sorted(missing)}")


async def verify_target_setup(
    guide: Guide,
    target: Any,
    result: RecipeRunResult | None,
) -> None:
    """Confirm the staged carrier opens and, when configured, is indexed."""
    verify_setup_result(guide, result)
    if result is None and guide.setup_verification is None:
        return
    await target.setup()
    target.reset()
    context = await target.render_document(
        AbstractDocument(id=f"setup-check-{guide.id}", title="", body_blocks=[])
    )
    if context is None or not getattr(context, "context_id", None):
        raise RuntimeError("setup verification could not open the target carrier")
    check = guide.setup_verification
    if check is None or not check.probe_prompt:
        target.reset()
        return
    for attempt in range(check.attempts):
        response = await target.invoke(context, check.probe_prompt)
        if re.search(check.expected_pattern or "", response.text or "", re.IGNORECASE):
            target.reset()
            return
        if attempt + 1 < check.attempts:
            await asyncio.sleep(min(2**attempt, 4))
    target.reset()
    raise RuntimeError("carrier readiness probe did not confirm indexed/visible content")


__all__ = [
    "bind_setup_target_options",
    "build_setup_recipe",
    "stage_guide",
    "verify_setup_result",
    "verify_target_setup",
]
