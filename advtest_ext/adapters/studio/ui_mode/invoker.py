"""Playwright invoker: drive a Workspace Studio app as a real user.

Demo-grade automation. ``ui_mode_invoke`` is the one async function that
``GeminiStudioMultiTurnTarget`` delegates to. Studio is NOT the Gemini side
panel — it is an agentic app-runner — so this flow does not reuse the shared
side-panel driver; it drives the Studio app surface directly:

  1. Reuse the caller's logged-in page (multi-turn) or launch a persistent
     Chromium context against ``ADVTEST_PLAYWRIGHT_PROFILE_DIR``.
  2. Navigate to the Studio app (``_studio_app_url(app_id)``) unless already
     there.
  3. On a reset turn, click "New run" to clear any prior conversation.
  4. Type the trigger prompt into the agent input and click Run.
  5. Wait for the running indicator to appear (run started) then disappear
     (run finished); read the agent's streamed output.
  6. Screenshot the surface and return a :class:`Response`, with the
     intercepted Gemini/Studio traffic captured alongside when enabled.

On any timeout or selector miss, raise :class:`SidePanelUnavailable` (the
canonical UI-drift signal the orchestrator keys on) with the drift-pointer
message. EVERYTHING here is SEEDED and needs live validation — the app URL
template and every selector (see :mod:`selectors`).
"""

from __future__ import annotations

import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    TrafficInterceptor,
    close_context,
    human_click,
    human_type,
    jitter_pause,
    launch_context,
    wait_for_button_ready,
)
from advtest_ext.adapters._ui_mode_common.config import (
    HUMAN_ACTION_PAUSE_S,
    LOADING_VISIBLE_PROBE_MS,
    MAIN_PANE_READY_MS,
    SELECTOR_DRIFT_MESSAGE,
    intercept_enabled,
    resolve_profile_dir,
    resolve_timeout_ms,
)
from advtest_ext.adapters.studio.ui_mode.selectors import SELECTORS
from advtest_ext.document.target import Response

# SEEDED — VALIDATE LIVE. The real Workspace Studio app URL shape is not known
# to this code; override with ADVTEST_STUDIO_APP_URL_TEMPLATE (a format string
# with a single ``{app_id}`` placeholder) once confirmed, or pass a full URL as
# the ``app_id`` (if it starts with ``http`` it is used verbatim).
_DEFAULT_APP_URL_TEMPLATE = "https://workspace.google.com/studio/app/{app_id}"


def _studio_app_url(app_id: str) -> str:
    if app_id.startswith("http://") or app_id.startswith("https://"):
        return app_id
    template = os.environ.get("ADVTEST_STUDIO_APP_URL_TEMPLATE") or _DEFAULT_APP_URL_TEMPLATE
    return template.format(app_id=app_id)


async def _wait_for_app_ready(page: Any, timeout_ms: int) -> None:
    """Confirm the Studio app surface painted before we drive it. Best-effort
    on the container, hard-gated on the input (typing must land somewhere live)."""
    try:
        await page.locator(SELECTORS["app_container"]).first.wait_for(
            state="visible", timeout=MAIN_PANE_READY_MS
        )
    except Exception:
        # Container selector may have drifted; the input gate below is the real
        # precondition, so don't abort here.
        pass
    try:
        await page.locator(SELECTORS["agent_input"]).first.wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc


async def _start_new_run(page: Any) -> bool:
    """Best-effort: click "New run" to clear any prior conversation. Returns
    ``True`` if it landed; never raises (a missing button just means the app
    starts from whatever state it was in)."""
    try:
        button = page.locator(SELECTORS["new_run"]).first
        await button.wait_for(state="visible", timeout=1_500)
    except Exception:
        return False
    try:
        await human_click(button, force=True)
    except Exception:
        return False
    await jitter_pause(HUMAN_ACTION_PAUSE_S)
    return True


async def _run_and_read(page: Any, user_prompt: str, timeout_ms: int) -> str:
    """Type the trigger, run the app/agent, and return its output text."""
    try:
        agent_input = page.locator(SELECTORS["agent_input"]).first
        await agent_input.wait_for(state="visible", timeout=timeout_ms)
        await human_type(agent_input, user_prompt)
        await jitter_pause(HUMAN_ACTION_PAUSE_S)

        # Prefer clicking Run; fall back to Enter if the button selector drifts.
        try:
            run_button = page.locator(SELECTORS["run_button"]).first
            await wait_for_button_ready(run_button, timeout_ms)
            await human_click(run_button, force=True)
        except Exception:
            await agent_input.press("Enter")

        # Confirm the run started (indicator appears), then wait for it to
        # clear. A missing "started" signal is not fatal — some runs settle
        # fast — so that probe is best-effort; the "finished" wait is the gate.
        indicator = page.locator(SELECTORS["running_indicator"]).first
        try:
            await indicator.wait_for(state="visible", timeout=LOADING_VISIBLE_PROBE_MS)
        except Exception:
            pass
        try:
            await indicator.wait_for(state="hidden", timeout=timeout_ms)
        except Exception:
            # Indicator selector may have drifted; fall through to reading
            # whatever the response container holds.
            pass

        response = page.locator(SELECTORS["agent_response"]).last
        await response.wait_for(state="visible", timeout=timeout_ms)
        text = await response.inner_text()
        return (text or "").strip()
    except SidePanelUnavailable:
        raise
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc


async def ui_mode_invoke(
    *,
    app_id: str,
    user_prompt: str,
    trace_id: str,
    output_dir: Path | str,
    external_page: Any = None,
    reset_conversation: bool = False,
    profile_dir: Path | None = None,
) -> Response:
    """Drive one turn against a Workspace Studio app and return the agent's
    output as a :class:`Response`.

    ``external_page`` (the warm page from a :class:`UiModeSession`) keeps one
    logged-in context across turns for multi-turn attacks; when it is ``None``
    a fresh persistent context is launched and torn down for this single call.
    """
    output_path = Path(output_dir)
    (output_path / "screenshots").mkdir(parents=True, exist_ok=True)
    timeout_ms = resolve_timeout_ms()
    target_url = _studio_app_url(app_id)

    async with AsyncExitStack() as stack:
        if external_page is not None:
            page = external_page
        else:
            context = await launch_context(profile_dir or resolve_profile_dir())
            stack.push_async_callback(close_context, context)
            page = await context.new_page()

        intercept_path = output_path / f"{trace_id}.intercepted.jsonl"
        if intercept_enabled():
            await stack.enter_async_context(TrafficInterceptor(page, intercept_path))

        current_url = getattr(page, "url", "") or ""
        if app_id not in current_url:
            await page.goto(target_url)
        await _wait_for_app_ready(page, timeout_ms)

        if reset_conversation:
            await _start_new_run(page)

        text = await _run_and_read(page, user_prompt, timeout_ms)

        screenshot_path = output_path / "screenshots" / f"{trace_id}.png"
        try:
            await page.screenshot(path=str(screenshot_path))
        except Exception:
            screenshot_path = None  # type: ignore[assignment]

        raw: dict[str, Any] = {
            "surface": "workspace_studio",
            "app_id": app_id,
            "url": target_url,
            "trace_id": trace_id,
        }
        if screenshot_path is not None:
            raw["screenshot_path"] = str(screenshot_path)
        if intercept_enabled():
            raw["intercepted_path"] = str(intercept_path)
        return Response(text=text, raw=raw)


__all__ = ["ui_mode_invoke"]
