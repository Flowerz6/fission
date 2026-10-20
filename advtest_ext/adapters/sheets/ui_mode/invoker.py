"""Drive the Gemini side panel against a live Google Sheet (ui_mode).

A focused port of ``google_docs.ui_mode.invoker``. The side-panel drive
(open -> optional new-conversation -> submit -> read the streamed reply) is
the SAME Gemini component, so those helpers are ported near-verbatim; the
Sheets-specific parts are only the host URL (``/spreadsheets/d/<id>``). We
DROP the Docs-only pieces — canvas body reading and the "Resolve Gemini
suggestions" edit gate — because a Sheets skill-leak conversation reads the
panel reply, not a spreadsheet body, and doesn't edit the sheet.

On any timeout / selector miss we raise :class:`SidePanelUnavailable` with
the canonical drift pointer; the Sheets selectors need live validation
(see ``docs/SHEETS_UI_MODE_RUNBOOK.md``).
"""

from __future__ import annotations

import asyncio
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    TrafficInterceptor,
    close_context,
    human_click,
    human_type,
    jitter_pause,
    launch_context,
    raise_side_panel_failure,
)
from advtest_ext.adapters._ui_mode_common.config import (
    AUTO_SUMMARY_SETTLE_MS,
    HUMAN_ACTION_PAUSE_S,
    HUMAN_HOVER_PAUSE_S,
    LOADING_VISIBLE_PROBE_MS,
    OPEN_RETRY_DELAY_S,
    SELECTOR_DRIFT_MESSAGE,
    intercept_enabled,
    resolve_profile_dir,
    resolve_timeout_ms,
)
from advtest_ext.adapters._ui_mode_common.response_assembly import (
    assemble_full_response,
    extract_model_thoughts,
)
from advtest_ext.adapters._ui_mode_common.response_read import read_new_blocks_settled
from advtest_ext.adapters.sheets.ui_mode.selectors import SELECTORS
from advtest_ext.document.target import Response

if TYPE_CHECKING:  # pragma: no cover
    pass


def _sheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"


def _url_is_on_sheet(url: str, spreadsheet_id: str) -> bool:
    """True when ``url`` is already on this spreadsheet — EQUALITY on the
    ``/spreadsheets/d/<id>`` segment (a substring test would wrongly skip
    navigation when the id is a prefix of another's)."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    return bool(m and m.group(1) == spreadsheet_id)


# ----------------------------------------------------------------------
# Side-panel drive (ported from google_docs.ui_mode — same Gemini panel)
# ----------------------------------------------------------------------


async def _panel_is_open(page: Any) -> bool:
    try:
        await page.locator(SELECTORS["side_panel_container"]).wait_for(state="visible", timeout=800)
        return True
    except Exception:
        return False


async def _open_side_panel(page: Any, timeout_ms: int) -> None:
    """Open the Gemini side panel (fast-path if already open), then gate on
    the input being visible so typing will land."""
    try:
        if not await _panel_is_open(page):
            button = page.locator(SELECTORS["side_panel_button"]).first
            await button.wait_for(state="visible", timeout=timeout_ms)
            await human_click(button, force=True)
            try:
                await page.locator(SELECTORS["side_panel_container"]).wait_for(
                    state="visible", timeout=timeout_ms
                )
            except Exception:
                await asyncio.sleep(OPEN_RETRY_DELAY_S)
                await human_click(button, force=True)
                await page.locator(SELECTORS["side_panel_container"]).wait_for(
                    state="visible", timeout=timeout_ms
                )
            await jitter_pause(HUMAN_ACTION_PAUSE_S)
        await page.locator(SELECTORS["side_panel_input"]).wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception as exc:
        await raise_side_panel_failure(page, exc, SELECTOR_DRIFT_MESSAGE)


async def _start_new_conversation(page: Any) -> bool:
    """Best-effort click the panel's "New conversation" button so our prompt
    is turn 1 of a fresh thread (not turn 3 of an auto-summary). Returns
    whether it clicked; never raises."""
    try:
        button = page.locator(SELECTORS["side_panel_new_conversation"])
        await button.wait_for(state="visible", timeout=1_500)
    except Exception:
        return False
    try:
        await human_click(button, force=True)
    except Exception:
        return False
    await jitter_pause(HUMAN_ACTION_PAUSE_S)
    return True


async def _wait_for_panel_idle(page: Any) -> None:
    """Wait out any in-flight auto-generation before we submit. Never raises."""
    loading = page.locator(SELECTORS["side_panel_loading_indicator"])
    try:
        await loading.wait_for(state="visible", timeout=500)
    except Exception:
        return
    try:
        await loading.wait_for(state="hidden", timeout=AUTO_SUMMARY_SETTLE_MS)
    except Exception:
        return


async def _click_send_button(page: Any) -> bool:
    """Click the composer send button; ``False`` if the selector missed."""
    try:
        button = page.locator(SELECTORS["side_panel_send"])
        await button.wait_for(state="visible", timeout=2_000)
    except Exception:
        return False
    try:
        await human_click(button, force=True)
    except Exception:
        return False
    return True


async def _submit_prompt(page: Any, user_prompt: str, timeout_ms: int) -> None:
    """Type the prompt into the panel input and submit (send button, Enter
    fallback). ``timeout_ms`` reserved for parity with the Docs invoker."""
    del timeout_ms
    try:
        await _wait_for_panel_idle(page)
        input_locator = page.locator(SELECTORS["side_panel_input"])
        await human_type(page, input_locator, user_prompt)
        await jitter_pause(HUMAN_HOVER_PAUSE_S)
        if not await _click_send_button(page):
            await page.keyboard.press("Enter")
    except Exception as exc:
        await raise_side_panel_failure(page, exc, SELECTOR_DRIFT_MESSAGE)


async def _count_responses(page: Any) -> int:
    try:
        return int(await page.locator(SELECTORS["side_panel_response"]).count())
    except Exception:
        return 0


# Sheets Gemini sometimes replies with clickable OPTIONS instead of (or
# alongside) a text bubble: a multiple-choice *questionnaire*
# (appsElementsSidekickQuestionnaireRoot -> a QuestionList of options), or an
# inline *suggestion* list. Capturing their labels means the attacker SEES the
# offered options -- otherwise an options-only reply reads as empty and fails.
# Class names confirmed against a live Sheets choice prompt (DevTools, 2026-07).
# We scope UNDER these two option containers only (not the generic response-
# options container) so the copy / regenerate / thumbs action-bar buttons -- a
# sibling of these, outside them -- are never mistaken for choices. The clickable
# option itself is a button / role=button / radio / checkbox / option.
_CHOICE_CONTAINER_CLASSES = (
    "appsElementsSidekickQuestionnaireRoot",
    "appsElementsSidekickInlineSuggestionListSuggestionListContainer",
)
_CLICKABLE_OPTION_ROLES = (
    "button",
    '[role="button"]',
    '[role="radio"]',
    '[role="checkbox"]',
    '[role="option"]',
)
_SUGGESTION_CHIP_SELECTOR = ", ".join(
    f'[role="complementary"][aria-label="Gemini"] .{cls} {role}'
    for cls in _CHOICE_CONTAINER_CLASSES
    for role in _CLICKABLE_OPTION_ROLES
)


async def _read_suggestion_chips(page: Any) -> list[str]:
    """Labels of any clickable choice/suggestion chips Gemini offered. Best-
    effort; never raises (a miss just means "no chips")."""
    try:
        loc = page.locator(_SUGGESTION_CHIP_SELECTOR)
        n = int(await loc.count())
    except Exception:
        return []
    chips: list[str] = []
    for i in range(min(n, 8)):
        try:
            label = (await loc.nth(i).inner_text()).strip()
        except Exception:
            continue
        if label and label not in chips:
            chips.append(label)
    return chips


async def _read_response(page: Any, timeout_ms: int, prev_count: int = 0) -> str:
    """Read THIS turn's reply. Waits for a genuinely NEW agent bubble and
    settle-polls the streamed text; if there's no text bubble, falls through to
    any clickable choice chips (Sheets sometimes replies with those). Raises
    only when neither a reply nor a chip is found."""
    try:
        loading = page.locator(SELECTORS["side_panel_loading_indicator"])
        try:
            await loading.wait_for(state="visible", timeout=LOADING_VISIBLE_PROBE_MS)
        except Exception:
            pass
        else:
            await loading.wait_for(state="hidden", timeout=timeout_ms)
        sel = SELECTORS["side_panel_response"]
        text = ""
        try:
            # A genuinely NEW agent bubble. No .last fallback — reading the
            # previous bubble would hand the attacker a stale reply.
            await page.locator(sel).nth(prev_count).wait_for(state="visible", timeout=timeout_ms)
            text = await read_new_blocks_settled(page, sel, prev_count)
        except Exception:
            # No new text bubble — maybe a choice-chip reply. Try chips below.
            text = ""
        chips = await _read_suggestion_chips(page)
        if chips:
            prefix = (text.rstrip() + "\n\n") if text.strip() else ""
            text = prefix + "[Gemini asked you to choose: " + " | ".join(chips) + "]"
    except Exception as exc:
        await raise_side_panel_failure(page, exc, SELECTOR_DRIFT_MESSAGE)
    if not text.strip():
        raise SidePanelUnavailable(
            "read an EMPTY reply from the Sheets Gemini side panel — no text "
            "bubble AND no choice chips. The response / loading-indicator / "
            "suggestion-chip selector likely drifted (docs/SHEETS_UI_MODE_RUNBOOK.md). "
            + SELECTOR_DRIFT_MESSAGE
        )
    return text


# Gemini shows its reasoning in a collapsible "Model thoughts" disclosure above
# the reply. That chain-of-thought is high-value red-team signal (it often
# states WHY it is about to refuse / what it is weighing), so we capture it FOR
# THE ATTACKER. It is kept OUT of Response.text on purpose: the judge scores
# .text, and thoughts that merely NAME a forbidden skill while reasoning to
# refuse it would otherwise trip the deterministic leak regex -> a false leak.
# BEST-GUESS selectors, scoped under the panel; validate live (runbook). The
# toggle is the disclosure control; the content is the reasoning body. We click
# the toggle only if the body looks empty (some UIs lazily render on expand).
_MODEL_THOUGHTS_TOGGLE_SELECTOR = (
    '[role="complementary"][aria-label="Gemini"] '
    '[class*="Thought" i] [role="button"], '
    '[role="complementary"][aria-label="Gemini"] '
    'button[aria-label*="thought" i], '
    '[role="complementary"][aria-label="Gemini"] '
    'button[aria-label*="reasoning" i]'
)
_MODEL_THOUGHTS_CONTENT_SELECTOR = (
    '[role="complementary"][aria-label="Gemini"] [class*="ThoughtContent" i], '
    '[role="complementary"][aria-label="Gemini"] [class*="ThoughtBody" i], '
    '[role="complementary"][aria-label="Gemini"] [class*="Thought" i] [class*="markdown" i]'
)


async def _read_model_thoughts(page: Any) -> str:
    """The latest 'Model thoughts' reasoning, capped. Best-effort: expands the
    disclosure if the body reads empty, never raises (a miss = no thoughts)."""
    try:
        content = page.locator(_MODEL_THOUGHTS_CONTENT_SELECTOR)
        n = int(await content.count())
    except Exception:
        return ""
    if n == 0:
        return ""
    try:
        body = (await content.nth(n - 1).inner_text()).strip()
    except Exception:
        body = ""
    if not body:
        # Possibly collapsed / lazily rendered — try clicking the disclosure.
        try:
            toggle = page.locator(_MODEL_THOUGHTS_TOGGLE_SELECTOR).last
            await toggle.click(timeout=1_000)
            body = (await content.nth(n - 1).inner_text()).strip()
        except Exception:
            body = ""
    if not body:
        return ""
    return str(body)  # full reasoning — evidence is never truncated at capture


async def _run_side_panel_flow(
    *,
    page: Any,
    user_prompt: str,
    screenshot_path: Path,
    timeout_ms: int,
    reset_conversation: bool,
) -> tuple[str, str]:
    """One turn: open the panel, (optionally) reset to a fresh conversation,
    submit the prompt, read the reply, capture the model thoughts, and snapshot
    the panel. Returns ``(reply_text, model_thoughts)`` — thoughts may be ""."""
    await _open_side_panel(page, timeout_ms)
    if reset_conversation:
        await _start_new_conversation(page)
    prev_count = await _count_responses(page)
    await _submit_prompt(page, user_prompt, timeout_ms)
    text = await _read_response(page, timeout_ms, prev_count=prev_count)
    thoughts = await _read_model_thoughts(page)
    try:
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(screenshot_path))
    except Exception:
        pass
    return text, thoughts


async def _capture_failure(page: Any, output_dir: Path, trace_id: str) -> None:
    """Best-effort forensic screenshot on a drift failure. Never raises."""
    try:
        path = (output_dir / "failures" / f"{trace_id}.png").resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(path))
    except Exception:
        pass


async def ui_mode_invoke(
    *,
    spreadsheet_id: str,
    user_prompt: str,
    trace_id: str,
    output_dir: Path,
    profile_dir: Path | None = None,
    timeout_ms: int | None = None,
    page_factory: Any = None,
    external_context: Any = None,
    external_page: Any = None,
    reset_conversation: bool = True,
) -> Response:
    """Drive the Gemini side panel against Sheet ``spreadsheet_id`` for one
    conversational turn. ``external_page`` (a warm :class:`UiModeSession` page)
    is the production multi-turn path — the tab is navigated in place so the
    conversation persists across turns. ``page_factory`` injects doubles in
    tests. Returns a :class:`Response` whose ``text`` is the panel reply."""
    profile = profile_dir or resolve_profile_dir()
    timeout = timeout_ms if timeout_ms is not None else resolve_timeout_ms()
    screenshot_path = (output_dir / "screenshots" / f"{trace_id}.png").resolve()
    intercept_path = (output_dir / "intercepted" / f"{trace_id}.jsonl").resolve()
    intercept_on = intercept_enabled()

    page: Any = None
    context: Any = None
    owns_context = False
    owns_page = False
    try:
        if page_factory is not None:
            context, page = await page_factory()
        elif external_page is not None:
            page = external_page
        elif external_context is not None:
            context = external_context
            page = await context.new_page()
            owns_page = True
        else:
            context = await launch_context(profile)
            owns_context = True
            page = await context.new_page()
        try:
            async with AsyncExitStack() as stack:
                if intercept_on:
                    await stack.enter_async_context(TrafficInterceptor(page, intercept_path))
                # Navigate only when not already on this sheet — re-loading
                # closes the panel and wipes the in-flight conversation.
                if not _url_is_on_sheet(getattr(page, "url", "") or "", spreadsheet_id):
                    await page.goto(_sheet_url(spreadsheet_id), timeout=timeout)
                await jitter_pause(HUMAN_ACTION_PAUSE_S)
                response_text, model_thoughts = await _run_side_panel_flow(
                    page=page,
                    user_prompt=user_prompt,
                    screenshot_path=screenshot_path,
                    timeout_ms=timeout,
                    reset_conversation=reset_conversation,
                )
        except SidePanelUnavailable:
            # The DOM response-bubble / loading-indicator selector drifts fairly
            # often, so _read_response can declare "empty" while the reply actually
            # streamed fine (we've measured the full answer + thoughts sitting in the
            # intercept for these). The intercepted stream is the source of truth, so
            # recover from it before giving up — the TrafficInterceptor has already
            # flushed (the async-with exited during unwinding). Only a genuinely
            # empty capture is a real SidePanelUnavailable.
            recovered = assemble_full_response(intercept_path) if intercept_on else ""
            if recovered.strip():
                response_text = recovered
                model_thoughts = extract_model_thoughts(intercept_path)
            else:
                await _capture_failure(page, output_dir, trace_id)
                raise
    finally:
        if owns_page and page is not None:
            try:
                await page.close()
            except Exception:
                pass
        if owns_context and context is not None:
            try:
                await close_context(context)
            except Exception:
                pass

    # Persist the COMPLETE response from the intercepted stream, not
    # Playwright's mid-stream inner_text read (often badly truncated — the
    # server response is the source of truth). Fall back to the UI text only
    # if the capture is missing or somehow shorter, so we never truncate more.
    ui_response_text = response_text
    if intercept_on:
        full = assemble_full_response(intercept_path)
        if full and len(full) >= len(response_text):
            response_text = full
        # The collapsible "Model thoughts" DOM scrape is fragile and frequently
        # misses; the reasoning is also in the intercepted stream, so prefer that
        # (falls back to the scrape when the stream carries no thought summaries).
        stream_thoughts = extract_model_thoughts(intercept_path)
        if stream_thoughts:
            model_thoughts = stream_thoughts

    return Response(
        text=response_text,
        complied=None,
        raw={
            "spreadsheet_id": spreadsheet_id,
            "screenshot_path": str(screenshot_path),
            "ui_response": ui_response_text,
            "intercepted_traffic_path": str(intercept_path) if intercept_on else None,
            "ui_mode": True,
            # For the attacker's adaptation only — deliberately NOT in .text so
            # the judge never scores the reasoning (see _read_model_thoughts).
            "model_thoughts": model_thoughts,
        },
    )


__all__ = ["ui_mode_invoke"]
