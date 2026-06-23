"""Playwright invoker: drive the Gemini side panel inside Gmail.

Mirrors :mod:`advtest_ext.adapters.google_docs.ui_mode.invoker`. The
Workspace Sidekick side-panel component is shared across Docs and
Gmail, so the panel-open / submit-prompt / read-response /
screenshot dance is byte-identical in shape; what differs is the
URL, the thread navigation, and the readiness gate for the main
content pane.

Entry point: :func:`ui_mode_invoke`. Caller is
:class:`advtest_ext.adapters.gmail.target.GmailTarget` in
``ui_mode``. The flow:

  1. Launch (or reuse) a persistent Chromium context against
     ``ADVTEST_PLAYWRIGHT_PROFILE_DIR``.
  2. Navigate to ``https://mail.google.com/mail/u/0/#inbox/<thread_id>``.
  3. Wait for the thread view (or at least the main pane) to paint.
  4. Click the right-rail Gemini icon, wait for the panel.
  5. Best-effort: reset to a fresh conversation.
  6. Submit the user prompt; read the response.
  7. Screenshot the side panel.
  8. Return a ``Response`` with ``raw["screenshot_path"]``.

Failure mode is the same canonical :class:`SidePanelUnavailable`
plus forensic-artefact capture under ``<output_dir>/failures/``.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    TrafficInterceptor,
    UiModeSession,
    close_context,
    human_click,
    human_type,
    jitter_pause,
    launch_context,
    wait_for_button_ready,
)
from advtest_ext.adapters._ui_mode_common.config import (
    AUTO_SUMMARY_SETTLE_MS,
    HUMAN_ACTION_PAUSE_S,
    HUMAN_HOVER_PAUSE_S,
    LOADING_VISIBLE_PROBE_MS,
    MAIN_PANE_READY_MS,
    PANEL_OPEN_PROBE_MS,
    SELECTOR_DRIFT_MESSAGE,
    intercept_enabled,
    resolve_post_response_hold_s,
    resolve_profile_dir,
    resolve_timeout_ms,
)
from advtest_ext.adapters.gmail.ui_mode.selectors import (
    SELECTORS,
    THREAD_BODY_SELECTORS,
)
from advtest_ext.document.target import Response


def _thread_url(thread_id: str) -> str:
    """Build the mail.google.com URL that opens a single thread, OR the
    inbox when no specific thread id is known.

    Gmail's hash-routed inbox uses ``#inbox/<thread_id>``. The
    thread_id we get from the API send call is the same id Gmail
    uses in its URL fragment, so no translation is needed.

    When ``thread_id`` is empty (the browser-only end-to-end path, where
    the bait was delivered via the UI senders and no API thread id was
    captured), open the inbox itself — the side panel grounds on the
    most recent mail, which is the freshly-delivered bait."""
    # ``u/0`` pins the URL to the first signed-in account in the
    # profile — important because the profile may have additional
    # signed-in accounts whose ordering can drift.
    if not thread_id:
        return "https://mail.google.com/mail/u/0/#inbox"
    return f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"


async def _panel_is_open(page: Any) -> bool:
    """Fast probe: is the side-panel container already mounted?"""
    try:
        await page.locator(SELECTORS["side_panel_container"]).wait_for(
            state="visible", timeout=PANEL_OPEN_PROBE_MS
        )
        return True
    except Exception:
        return False


async def _open_side_panel(page: Any, timeout_ms: int) -> None:
    """Open the Gemini side panel. Layered defences:

    1. Fast-path: if already open, skip the click.
    2. Wait for the entry button to be visible and enabled.
    3. Force-click once.
    4. Wait for the container to be visible.
    5. Final gate on the input being visible — this is the
       "panel is usable, typing will land somewhere live" contract.
       Crucially, this also rejects the "panel opened then closed
       immediately" pathology: if the click hit a toggle that
       opened a transient overlay (e.g. search-bar Gemini), the
       container will be briefly visible but the input won't be
       reachable.

    No retry click — the previous retry pattern was firing on a
    toggle button that had already opened the panel, immediately
    closing it again. If the first click misses, the failure
    artefact captures it; we don't try to compensate by spamming.
    """
    try:
        if not await _panel_is_open(page):
            button = page.locator(SELECTORS["side_panel_button"]).first
            await wait_for_button_ready(button, timeout_ms)
            await human_click(button, force=True)
            await page.locator(SELECTORS["side_panel_container"]).wait_for(
                state="visible", timeout=timeout_ms
            )
            await jitter_pause(HUMAN_ACTION_PAUSE_S)
        await page.locator(SELECTORS["side_panel_input"]).wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc


async def _start_new_conversation(page: Any) -> bool:
    """Click "New conversation" to discard any auto-summary. Best-
    effort — returns ``False`` if the button isn't visible within
    1.5s and the caller proceeds with whatever state the panel is
    in."""
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


async def _wait_for_thread_body(page: Any) -> None:
    """Best-effort wait for the thread / main mail pane to paint.

    Gmail's chrome (left nav, right rail) loads before the main
    pane has any thread content. Clicking the right-rail Gemini
    icon too early would land on an empty pane and either fail
    to open the panel or open it against nothing.

    Try the thread-body selectors in order; first hit wins. All
    miss → short sleep and proceed anyway. Never raises."""
    for selector in THREAD_BODY_SELECTORS:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=MAIN_PANE_READY_MS)
            return
        except Exception:
            continue
    await asyncio.sleep(2.0)


async def _wait_for_panel_idle(page: Any) -> None:
    """Wait for any in-flight Gemini generation (e.g. auto-summary)
    to finish before submitting our own prompt. See Docs invoker
    for the full rationale."""
    loading_locator = page.locator(SELECTORS["side_panel_loading_indicator"])
    try:
        await loading_locator.wait_for(state="visible", timeout=500)
    except Exception:
        return
    try:
        await loading_locator.wait_for(state="hidden", timeout=AUTO_SUMMARY_SETTLE_MS)
    except Exception:
        return


async def _submit_prompt(page: Any, user_prompt: str, timeout_ms: int) -> None:
    """Type the prompt and submit. Click send-button first; fall back
    to Enter if the selector misses."""
    del timeout_ms  # kept for signature parity with the Docs sibling
    try:
        await _wait_for_panel_idle(page)
        input_locator = page.locator(SELECTORS["side_panel_input"])
        await human_type(page, input_locator, user_prompt)
        await jitter_pause(HUMAN_HOVER_PAUSE_S)
        if not await _click_send_button(page):
            await page.keyboard.press("Enter")
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc


async def _click_send_button(page: Any) -> bool:
    """Click the composer send button. Returns ``False`` on miss so
    the caller can fall back to Enter."""
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


async def _read_response(page: Any, timeout_ms: int) -> str:
    """Wait for loading indicator (appear → disappear), then read
    ``inner_text`` from the last agent bubble. Fast-path skips the
    appear-wait if the indicator never mounts (synchronous reply /
    defence message)."""
    try:
        loading_locator = page.locator(SELECTORS["side_panel_loading_indicator"])
        try:
            await loading_locator.wait_for(state="visible", timeout=LOADING_VISIBLE_PROBE_MS)
        except Exception:
            pass
        else:
            await loading_locator.wait_for(state="hidden", timeout=timeout_ms)
        response_locator = page.locator(SELECTORS["side_panel_response"]).last
        await response_locator.wait_for(state="visible", timeout=timeout_ms)
        text = await response_locator.inner_text()
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc
    return text or ""


async def _screenshot_side_panel(
    page: Any,
    out_path: Path,
    timeout_ms: int,
) -> None:
    """Screenshot the response bubble's bounding box. Falls back to
    full-page on bounding-box query failure."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        response_locator = page.locator(SELECTORS["side_panel_response"]).last
        await response_locator.wait_for(state="visible", timeout=timeout_ms)
        box = await response_locator.bounding_box()
        if box is None:
            await page.screenshot(path=str(out_path), full_page=False)
            return
        await page.screenshot(
            path=str(out_path),
            clip={
                "x": box["x"],
                "y": box["y"],
                "width": box["width"],
                "height": box["height"],
            },
        )
    except Exception:
        out_path.write_bytes(b"")


async def _capture_failure_artefact(
    page: Any,
    output_dir: Path,
    trace_id: str,
    *,
    cause: BaseException | None = None,
) -> None:
    """Best-effort: forensic screenshot + page-state dump under
    ``<output_dir>/failures/<trace_id>.{png,txt}``. Mirrors the Docs
    sibling — every step wrapped in try/except so artefact capture
    can't mask the original :class:`SidePanelUnavailable`."""
    failures_dir = output_dir / "failures"
    try:
        failures_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return
    png_path = failures_dir / f"{trace_id}.png"
    txt_path = failures_dir / f"{trace_id}.txt"
    try:
        await page.screenshot(path=str(png_path), full_page=True)
    except Exception:
        try:
            png_path.write_bytes(b"")
        except Exception:
            pass
    url = ""
    title = ""
    body_text = ""
    try:
        url = str(getattr(page, "url", "") or "")
    except Exception:
        pass
    try:
        title = await page.title()
    except Exception:
        pass
    try:
        body_text = await page.locator("body").inner_text()
    except Exception:
        pass
    cause_chain: list[str] = []
    cur = cause
    while cur is not None:
        cause_chain.append(f"{type(cur).__name__}: {str(cur)[:500]}")
        cur = cur.__cause__ or cur.__context__
        if len(cause_chain) > 5:
            break
    cause_block = "\n".join(cause_chain) if cause_chain else "(no cause attached)"
    try:
        txt_path.write_text(
            (
                f"URL: {url}\nTitle: {title}\n\n"
                f"--- underlying cause chain ---\n{cause_block}\n\n"
                f"--- body inner_text (first 2000 chars) ---\n"
                f"{body_text[:2000]}"
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


async def ui_mode_invoke(
    *,
    thread_id: str,
    user_prompt: str,
    trace_id: str,
    output_dir: Path,
    profile_dir: Path | None = None,
    timeout_ms: int | None = None,
    page_factory: Any = None,
    external_context: Any = None,
    external_page: Any = None,
) -> Response:
    """Drive the Gemini side panel against an existing Gmail thread.

    Parameters
    ----------
    thread_id : str
        Gmail thread id. ``GmailTarget.render_document`` already
        created the thread via the API and recorded the id on its
        ``NativeContext``; the caller passes it through.
    user_prompt : str
        Prompt to type into the side panel.
    trace_id : str
        Filename stem for the screenshot.
    output_dir : Path
        Per-run output directory. Screenshots land under
        ``<output_dir>/screenshots/<trace_id>.png``.
    profile_dir : Path | None
        Override for the persistent profile.
    timeout_ms : int | None
        Override for the per-step wait timeout.
    page_factory : optional
        Async callable returning ``(context, page)``. Test injection.
    external_context : optional
        Live Playwright context to reuse. Opens a fresh page on it;
        closes the page on exit, leaves the context alone.
    external_page : optional
        Live Playwright page to reuse — for ``UiModeSession``'s
        single-page reuse path. Navigates in place, closes nothing.

    Returns
    -------
    Response
        ``text`` is the panel's reply; ``raw["screenshot_path"]``
        points at the PNG.

    Raises
    ------
    SidePanelUnavailable
        Any Playwright timeout / selector miss. Forensic artefact
        written under ``<output_dir>/failures/`` first.
    """
    profile = profile_dir or resolve_profile_dir()
    timeout = timeout_ms if timeout_ms is not None else resolve_timeout_ms()
    screenshot_path = (output_dir / "screenshots" / f"{trace_id}.png").resolve()
    # Per-trace JSONL capture of Workspace Gemini API traffic. Path
    # is always computed; the file is only populated when
    # interception is enabled (default on — see intercept_enabled).
    # When disabled, the path on the returned Response is set to
    # None so the dashboard doesn't render a broken-link panel.
    intercept_path = (output_dir / "intercepted" / f"{trace_id}.jsonl").resolve()
    intercept_on = intercept_enabled()

    page: Any = None
    context: Any = None
    owns_context: bool = False
    owns_page: bool = False
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
            # AsyncExitStack lets us conditionally enter the
            # interceptor without nesting two near-identical blocks.
            # When interception is disabled the stack is empty and
            # the wrapped code runs unchanged.
            async with AsyncExitStack() as stack:
                if intercept_on:
                    await stack.enter_async_context(TrafficInterceptor(page, intercept_path))
                # Force a real navigation between attacks. Gmail's
                # URLs are hash-routed (#inbox/<thread_id>), so a
                # plain ``goto`` between threads is a same-page hash
                # change — the side panel stays mounted with the
                # previous conversation's history intact, and after
                # a few IPI attempts the accumulated context primes
                # Gemini into a generic-refusal stance ("I'm still
                # learning, can't help with that"). Real reload
                # resets the client-side panel state per attempt.
                #
                # Sequence: about:blank wipes the page, then goto
                # the new thread does a full document load. Single
                # goto with reload would also work but the about:
                # blank bounce is more visually obvious in the live
                # demo and avoids any goto-equals-current-URL no-op
                # edge.
                try:
                    await page.goto("about:blank", timeout=timeout)
                except Exception:
                    # about:blank failures are non-fatal; we'd
                    # rather try the real navigation than abort
                    # the round.
                    pass
                await page.goto(_thread_url(thread_id), timeout=timeout)
                await _wait_for_thread_body(page)
                await jitter_pause(HUMAN_ACTION_PAUSE_S)
                response_text = await _run_side_panel_flow(
                    page=page,
                    user_prompt=user_prompt,
                    screenshot_path=screenshot_path,
                    timeout_ms=timeout,
                )
        except SidePanelUnavailable as panel_exc:
            try:
                await _capture_failure_artefact(page, output_dir, trace_id, cause=panel_exc)
            except Exception:
                pass
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

    return Response(
        text=response_text,
        complied=None,
        raw={
            "thread_id": thread_id,
            "screenshot_path": str(screenshot_path),
            "intercepted_traffic_path": str(intercept_path) if intercept_on else None,
            "ui_mode": True,
        },
    )


async def _run_side_panel_flow(
    *,
    page: Any,
    user_prompt: str,
    screenshot_path: Path,
    timeout_ms: int,
) -> str:
    """Inner sequence shared between production and test-injection
    paths. Open panel, settle auto-summary, reset conversation,
    submit, read, screenshot, hold."""
    await _open_side_panel(page, timeout_ms)
    await _wait_for_panel_idle(page)
    await _start_new_conversation(page)
    await _submit_prompt(page, user_prompt, timeout_ms)
    response_text = await _read_response(page, timeout_ms)
    await _screenshot_side_panel(page, screenshot_path, timeout_ms)
    hold_s = resolve_post_response_hold_s()
    if hold_s > 0:
        await asyncio.sleep(hold_s)
    return response_text


__all__ = [
    "SidePanelUnavailable",
    "UiModeSession",
    "ui_mode_invoke",
]
