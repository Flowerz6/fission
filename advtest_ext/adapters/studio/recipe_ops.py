"""Workspace Studio operation handlers for the Recipe Runner.

Two handlers, registered into the default registry by
:func:`advtest_ext.core.builtin_operations.default_operations`:

- ``("studio", "create_app")`` — "point a Studio app at a data source" setup
  step. Studio apps ingest a DATA SOURCE (typically a shared Sheet planted via
  ``sheets.create_and_seed``); this records/mints the app bound to it and
  returns an ``app_id`` a later ``run_agent`` step (or the attack turn) drives.
- ``("studio", "run_agent")`` — "run the app / send the trigger" step.
  Delegates to the Studio ui_mode invoker, so it inherits the adapter's Chrome
  stealth, traffic interception, and drift handling.

STUDIO ADAPTER NOTE: there is no public Studio API this code can create an app
with, so ``create_app`` mints via the logged-in BROWSER (or records a supplied
``app_id`` / app URL). The Studio selectors and the app-URL shape are SEEDED and
need live validation (see :mod:`advtest_ext.adapters.studio.ui_mode.selectors`).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from advtest_ext.adapters.studio.ui_mode import (
    UiModeSession,
    ui_mode_invoke,
)
from advtest_ext.core.recipe_operations import (
    OperationContext,
    OperationResult,
    require_str_param,
)

_DEFAULT_RECIPE_OUTPUT_DIR = Path("out") / "recipe_runs"


async def create_app(ctx: OperationContext) -> OperationResult:
    """Record/mint a Studio app bound to a data source. Step params::

        params:
          data_source_id: "{{step.plant_data_source.sheet_id}}"  # the carrier
          app_id: "<existing app id or full app URL>"            # optional
          title: "Weekly Report"                                 # optional

    Outputs::

        outputs:
          app_id: <app id or URL the attack turn / run_agent drives>
          data_source_id: <unchanged from input>

    When ``app_id`` is supplied it is used verbatim (the common path: an app the
    operator built in Studio once, reused across runs). Otherwise, with a live
    per-account session, the app is minted through the browser (SEEDED create
    URL); without one, a placeholder handle is returned so the recipe stays
    executable in dry/unit contexts.
    """
    data_source_id = ctx.params.get("data_source_id")
    supplied = ctx.params.get("app_id")
    if isinstance(supplied, str) and supplied:
        app_id = supplied
    elif ctx.session is not None and isinstance(ctx.session, UiModeSession):
        page = await ctx.session.ensure_page()
        create_url = os.environ.get(
            "ADVTEST_STUDIO_CREATE_URL", "https://workspace.google.com/studio/create"
        )
        await page.goto(create_url)
        app_id = str(getattr(page, "url", "") or create_url)
    else:
        app_id = f"studio-app-{uuid.uuid4().hex[:10]}"

    return OperationResult(
        outputs={
            "app_id": app_id,
            "data_source_id": data_source_id if isinstance(data_source_id, str) else "",
        },
        raw={"operation": "studio.create_app"},
    )


async def run_agent(ctx: OperationContext) -> OperationResult:
    """Run a Studio app / send the trigger turn. Step params::

        params:
          app_id: "{{step.create_app.app_id}}"
          prompt: "Run the Weekly Report app on this data source and show me the report."

    Outputs::

        outputs:
          app_id: <unchanged>
          response_excerpt: <first 300 chars of the agent output>

    The full agent output lands in ``OperationResult.response_text`` so the
    Recipe Runner can evaluate Recipe-level signals (e.g. the exfil sink) on it.
    """
    app_id = require_str_param(ctx.params, "app_id", "studio.run_agent")
    prompt = require_str_param(ctx.params, "prompt", "studio.run_agent")
    trace_id = f"recipe-{uuid.uuid4().hex[:12]}"
    output_dir = Path(ctx.params.get("output_dir") or _DEFAULT_RECIPE_OUTPUT_DIR)

    explicit = ctx.params.get("reset_conversation")
    reset_conversation = explicit if isinstance(explicit, bool) else True

    if ctx.session is not None and isinstance(ctx.session, UiModeSession):
        page = await ctx.session.ensure_page()
        response = await ui_mode_invoke(
            app_id=app_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            external_page=page,
            reset_conversation=reset_conversation,
        )
    else:
        response = await ui_mode_invoke(
            app_id=app_id,
            user_prompt=prompt,
            trace_id=trace_id,
            output_dir=output_dir,
            reset_conversation=reset_conversation,
        )

    excerpt = (response.text or "")[:300]
    return OperationResult(
        response_text=response.text or "",
        outputs={"app_id": app_id, "response_excerpt": excerpt},
        raw={"operation": "studio.run_agent", "trace_id": trace_id, **dict(response.raw)},
    )


__all__ = ["create_app", "run_agent"]
