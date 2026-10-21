"""Playwright invoker: drive the Gemini side panel as a real user.

Demo-grade automation. ``ui_mode_invoke`` is the one async function
that ``SlidesTarget`` delegates to when constructed with
``mode="ui_mode"``. The flow (mirrors ``google_docs.ui_mode.invoker`` —
same Gemini Sidekick panel):

  1. Launch a persistent Chromium context against the profile dir
     at ``ADVTEST_PLAYWRIGHT_PROFILE_DIR`` (default
     ``~/.advtest/playwright-profile``). The profile must already
     be signed in to the AI Premium account — see
     ``scripts/setup_playwright_profile.py``.
  2. Open ``https://docs.google.com/presentation/d/<deck_id>/edit`` in
     a new page.
  3. Click the side-panel entry button, wait for the prompt input,
     type the user prompt, submit with Enter.
  4. Wait for the loading indicator to disappear, then read the
     response text from the assistant message container.
  5. Screenshot the side panel (clip-bounded to the right rail, not
     the full page) into ``<output_dir>/screenshots/<trace_id>.png``.
  6. Return a ``Response`` with ``raw["screenshot_path"]`` pointing
     at the PNG so the orchestrator can copy it into the replay
     bundle.

On any timeout or selector miss, raise :class:`SidePanelUnavailable`
with the canonical message. The Slides selectors — and, especially,
the deck-navigation + speaker-notes readiness gates below — are seeded
from Docs and need live validation (see :mod:`selectors`).

The browser is headed (``headless=False``) with ``slow_mo=200`` so the
demo audience can follow each step on screen.
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
    wait_for_button_ready,
)
from advtest_ext.adapters._ui_mode_common.config import (
    AUTO_SUMMARY_SETTLE_MS,
    HUMAN_ACTION_PAUSE_S,
    HUMAN_HOVER_PAUSE_S,
    MAIN_PANE_READY_MS,
    OPEN_RETRY_DELAY_S,
    PANEL_OPEN_PROBE_MS,
    SELECTOR_DRIFT_MESSAGE,
    intercept_enabled,
    resolve_post_response_hold_s,
    resolve_profile_dir,
    resolve_timeout_ms,
)
from advtest_ext.adapters._ui_mode_common.response_assembly import (
    assemble_full_response,
)
from advtest_ext.adapters._ui_mode_common.response_read import read_response
from advtest_ext.adapters.slides.ui_mode.selectors import SELECTORS
from advtest_ext.document.target import Response

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Playwright is an optional extra; the import is deferred to
    # ``launch_context`` so unit tests can mock the surface without
    # the package installed.
    pass


# CSS selectors for the deck editor content. Used by
# :func:`_wait_for_deck_ready` to confirm the editor has actually
# painted. Slides-specific (the ``.punch-*`` classes are the Slides
# editor surface — "Punch" is Slides' internal codename). Multiple
# selectors so a class-name drift on one doesn't ground the whole flow.
#
# SEEDED — VALIDATE LIVE: the Docs adapter targets ``.kix-*`` (its
# canvas paragraph renderer); the Slides equivalents below are seeded
# guesses at the deck canvas + filmstrip and MUST be confirmed against
# live Slides before a real run.
_DECK_BODY_SELECTORS = (
    # Active-slide editing surface (the canvas the current slide paints
    # into) — the most specific "the deck has laid out" signal.
    ".punch-viewer-content",
    # Editor canvas container; a coarser fallback.
    ".sketchy-canvas",
    # Even coarser: the filmstrip that lists the deck's slides.
    ".punch-filmstrip-slides",
)

# The speaker-notes pane readiness gate. Slides renders notes in a
# pane below the canvas that mounts after the slide body. This has no
# Docs analogue (Docs has no notes pane). SEEDED — VALIDATE LIVE.
_DECK_NOTES_SELECTORS = (
    ".punch-notes-editor",
    ".punch-notes-textview",
)


def _deck_url(deck_id: str) -> str:
    return f"https://docs.google.com/presentation/d/{deck_id}/edit"


# Pull the deck id out of a Slides URL's ``/presentation/d/<id>`` path
# segment so we can compare it for EQUALITY (not substring). The id stops
# at the next ``/``, ``?`` or ``#``.
_DECK_ID_IN_URL = re.compile(r"/presentation/d/([^/?#]+)")


def _url_is_on_deck(current_url: str, deck_id: str) -> bool:
    """True iff ``current_url`` is already viewing ``deck_id``.

    Compares the URL's path deck-id segment for equality. A plain
    ``deck_id in current_url`` substring test would false-positive when the
    id rides in a query/fragment or is a prefix of a different deck's id —
    skipping navigation and leaving the session on the WRONG deck.
    """
    match = _DECK_ID_IN_URL.search(current_url)
    return match is not None and match.group(1) == deck_id


async def _panel_is_open(page: Any) -> bool:
    """Short probe: is the side panel container already visible?

    Returns ``True`` / ``False`` without raising. Used as the
    fast-path in :func:`_open_side_panel` so a previous run that
    left the panel open (or the user manually opening it during a
    live demo) skips the entry-button click entirely."""
    try:
        await page.locator(SELECTORS["side_panel_container"]).wait_for(
            state="visible", timeout=PANEL_OPEN_PROBE_MS
        )
        return True
    except Exception:
        return False


async def _open_side_panel(page: Any, timeout_ms: int) -> None:
    """Open the Gemini side panel, robust against the common
    "click-but-nothing-happens" failure mode.

    Layered defences:

    1. Fast-path: if the panel container is already visible (a
       previous run or the live operator left it open), return
       without touching the toolbar.
    2. Wait for the entry button to be visible AND enabled before
       clicking — Slides greys the button during hydration and a
       click then is a silent no-op.
    3. ``force=True`` on the click. Playwright's actionability
       check has false negatives on Google's custom buttons (it
       reports "element does not receive pointer events" while
       the underlying click handler would have fired fine).
    4. After clicking, *verify* the panel actually mounted by
       waiting on ``side_panel_container``. If the click was
       swallowed (intercepting overlay, hydration race), wait a
       beat and click once more.
    5. Final gate: wait for the prompt input so subsequent typing
       lands somewhere live.

    Any failure surfaces as :class:`SidePanelUnavailable` with the
    canonical drift-pointer message."""
    try:
        if not await _panel_is_open(page):
            button = page.locator(SELECTORS["side_panel_button"])
            await wait_for_button_ready(button, timeout_ms)
            # Hover + jittered pause + force-click — see human_click
            # for the why on each piece.
            await human_click(button, force=True)
            try:
                await page.locator(SELECTORS["side_panel_container"]).wait_for(
                    state="visible", timeout=timeout_ms
                )
            except Exception:
                # The first click looked like it landed but the panel
                # never mounted. One retry after a short settle —
                # avoids spam-clicking against a genuinely broken
                # selector while recovering from transient races.
                await asyncio.sleep(OPEN_RETRY_DELAY_S)
                await human_click(button, force=True)
                await page.locator(SELECTORS["side_panel_container"]).wait_for(
                    state="visible", timeout=timeout_ms
                )
            # Small look-around pause after the panel mounts — gives
            # the side-panel's own JS time to finish its mount /
            # focus dance before we type into it.
            await jitter_pause(HUMAN_ACTION_PAUSE_S)
        # Final gate runs in BOTH branches (fast-path skip and
        # open-from-closed). It's the "panel is usable, typing
        # will land somewhere live" contract that subsequent steps
        # rely on — skipping it on the fast-path would let an
        # already-mounted but mid-transition panel through.
        await page.locator(SELECTORS["side_panel_input"]).wait_for(
            state="visible", timeout=timeout_ms
        )
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc


async def _start_new_conversation(page: Any) -> bool:
    """Click the side panel's "New conversation" button to reset
    the panel to a clean state.

    Why: clicking the toolbar "Ask Gemini" button auto-fires a
    summarise request against the deck (one-click open-and-ask UX).
    When that request resolves first, our subsequent prompt
    becomes turn 3 of a "summarise → reply → user follow-up"
    thread, and the input combobox is in a post-submission state
    that Playwright sees as unstable. Clicking "New conversation"
    discards that history and brings the combobox back to a fresh,
    actionable state.

    Best-effort: if the button isn't visible within 3s (selector
    drifted, button collapsed into an overflow menu, Slides UI
    changed) we return ``False`` and the caller proceeds with the
    auto-summary path. The flow is robust either way — this is an
    optimisation, not a hard requirement.

    Returns ``True`` if the click landed, ``False`` otherwise.
    """
    try:
        button = page.locator(SELECTORS["side_panel_new_conversation"])
        await button.wait_for(state="visible", timeout=1_500)
    except Exception:
        return False
    try:
        # Force-click for the same reason as the toolbar entry
        # button — Playwright's actionability check has false
        # negatives on Google's custom buttons.
        await human_click(button, force=True)
    except Exception:
        return False
    # Brief settle so the panel re-renders to its blank-conversation
    # state before our subsequent typing.
    await jitter_pause(HUMAN_ACTION_PAUSE_S)
    return True


async def _wait_for_deck_ready(page: Any) -> None:
    """Wait for the deck to actually paint in the editor pane.

    The Slides API write confirmed the deck content server-side, but
    the Slides editor renders in two layers: the **shell** (toolbar,
    filmstrip chrome, side rail — including the "Ask Gemini" entry
    button) loads first, then the **deck content** (the active slide
    canvas + the speaker-notes pane, where the payload we injected
    lives) loads after. Clicking "Ask Gemini" while the deck is still
    blank fires Google's auto-summary against an empty editor — Gemini
    replies "the presentation is empty" even though Slides has the full
    content.

    Try the deck-body selectors in order (most specific first). First
    hit wins. Then best-effort probe the speaker-notes pane — the notes
    are the primary injection carrier, so a painted notes pane is a
    stronger "Gemini can see the payload" signal — but never block on
    it (the notes pane may be collapsed by the operator). If all the
    body selectors time out, we proceed anyway after a short sleep —
    better to attempt with a possibly-empty editor than to abort the
    whole run for a selector drift on a Slides internal class.

    Never raises: the caller uses this as a best-effort gate, not
    a hard precondition."""
    for selector in _DECK_BODY_SELECTORS:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=MAIN_PANE_READY_MS)
            # Body painted — give the speaker-notes pane a brief,
            # non-blocking chance to mount too (Slides-specific gate;
            # SEEDED, needs live validation).
            await _probe_notes_pane(page)
            return
        except Exception:
            continue
    # All selectors missed. Sleep briefly to give the editor a
    # chance to paint anyway — Gemini might still see the content
    # if Slides' indexing got there first.
    await asyncio.sleep(2.0)


async def _probe_notes_pane(page: Any) -> None:
    """Best-effort readiness probe for the speaker-notes pane — the
    deck's primary injection channel. Never raises; a collapsed or
    drifted notes pane simply isn't waited on. SEEDED — VALIDATE LIVE."""
    for selector in _DECK_NOTES_SELECTORS:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=800)
            return
        except Exception:
            continue


async def _read_deck_body_text(page: Any) -> str:
    """Return the deck editor content as plain text via Playwright.

    Tries each deck-body selector in order, then the notes-pane
    selectors — the payload rides in the speaker notes, so we want that
    text too. For multi-element selectors we concatenate ALL matching
    elements rather than reading only ``.first`` — Slides renders one
    element per slide / notes block and ``.first`` would only catch the
    leading one. Each call has a short timeout so a missing locator
    can't hang the run. Best-effort — any failure returns ``""`` so
    signal evaluation simply finds an empty body. The caller stashes
    this on the persisted Trace's ``raw_response_data['document_body']``.
    """
    for selector in (*_DECK_BODY_SELECTORS, *_DECK_NOTES_SELECTORS):
        try:
            locator = page.locator(selector)
            # all_inner_texts returns one string per matched element.
            texts = await locator.all_inner_texts()
        except Exception:
            continue
        joined = "\n".join(t for t in texts if t).strip()
        if joined:
            return joined
    # No body-element fallback: Slides renders text to a canvas, so
    # ``page.locator('body').inner_text()`` returns toolbar/filmstrip
    # chrome text without the actual deck content — that garbage would
    # clobber the planted ``document_body`` from the ``set_speaker_notes``
    # handler in the Runner's "last non-empty wins" merge. Empty result
    # is honest: the DOM didn't yield editor text; trust the
    # handler-provided body.
    return ""


async def _wait_for_panel_idle(page: Any) -> None:
    """Wait for any in-flight Gemini generation to finish before we
    try to submit our own prompt.

    Why this exists: clicking the "Ask Gemini" toolbar button is a
    one-click "open panel AND auto-summarise" action in the live
    Slides UX — the panel mounts WHILE a default summarisation
    request is already running. Typing into the contenteditable
    during that window either drops the keystrokes or queues them
    in a way that surfaces as "our prompt was submitted before
    our typing happened" (because the auto-summary's submission
    is what the user sees first).

    Implementation: probe the loading-indicator selector once with
    a short timeout. If it's NOT visible already (no auto-summary,
    or it already finished), return immediately. If it IS visible,
    wait for it to disappear with the auto-summary timeout —
    Gemini's summaries against a small deck typically settle in
    ≤15s, so 30s gives generous headroom.

    Never raises: a probe-side failure means "we can't tell, just
    proceed". The downstream :func:`_read_response` will pick up
    any remaining busy state on its own."""
    loading_locator = page.locator(SELECTORS["side_panel_loading_indicator"])
    try:
        await loading_locator.wait_for(state="visible", timeout=500)
    except Exception:
        # No auto-summary in flight (or selector miss; we don't
        # care here). Nothing to wait for.
        return
    try:
        await loading_locator.wait_for(state="hidden", timeout=AUTO_SUMMARY_SETTLE_MS)
    except Exception:
        # Auto-summary genuinely stuck; let _submit_prompt try
        # anyway. If the input is still busy, it'll either succeed
        # (Gemini queues our turn) or fail with the canonical
        # drift-pointer that the failure-capture artefact will
        # explain. Better than blocking the run indefinitely.
        return


# Slide-suggestion gate. When a "help me write" turn makes Gemini EDIT the
# deck, Slides can enter a suggestion mode; the NEXT send pops a modal:
#
#   "Resolve Gemini suggestions — To send a new prompt, you need to
#    either accept or reject rest of the suggestions."   [Cancel] [Accept All]
#
# (the Accept/Reject gate mirrored from the Docs surface). The only action
# that unblocks is "Accept All" (Cancel just dismisses the send), so we
# click it when the dialog is up. Critically, the dialog only appears
# AFTER a send attempt — so resolution runs on the send path, not before
# typing. SEEDED from Docs; re-check on drift against live Slides.
_ACCEPT_ALL_SELECTOR = (
    'div[role="alertdialog"] button[data-mdc-dialog-action="ok"], '
    'button[aria-label="Accept All" i], '
    'button[aria-label="Accept all" i]'
)


async def _accept_resolve_dialog(page: Any) -> bool:
    """If the "Resolve Gemini suggestions" modal is up, click Accept All
    to unblock the composer. Returns True if it clicked, False if no
    dialog was present (or the selector missed). Best-effort: never
    raises, so a turn that didn't trip the gate is unaffected."""
    try:
        button = page.locator(_ACCEPT_ALL_SELECTOR).first
        await button.wait_for(state="visible", timeout=800)
    except Exception:
        return False
    try:
        await human_click(button, force=True)
        await jitter_pause(HUMAN_ACTION_PAUSE_S)
        return True
    except Exception:
        return False


async def _submit_prompt(page: Any, user_prompt: str, timeout_ms: int) -> None:
    """Type the prompt and submit (Enter).

    Two safety nets before typing:

    - Wait for any in-flight auto-summary (see
      :func:`_wait_for_panel_idle`). The "Ask Gemini" button click
      kicks one off on every panel open; if we type into the input
      while it's running, our keystrokes get dropped or our Enter
      submits a queued/stale value.
    - Type directly into the locator (see :func:`human_type`). NOT
      via ``page.keyboard.type`` — that sends to whatever has focus
      right now, and a side-panel quick-suggestion chip can steal
      focus during the mount.

    Loading-state observation for OUR submitted prompt lives
    further down in :func:`_read_response` — we want both the
    "Gemini started" (visible) and "Gemini finished" (hidden) edges
    on that response, and the response read immediately follows."""
    try:
        # Settle the auto-summary first. No-op when the panel is
        # already idle (most rounds after the first against a given
        # deck, since each deck only auto-summarises once on panel
        # open).
        await _wait_for_panel_idle(page)
        # A standing resolve dialog left over from a prior turn would
        # block typing/sending — clear it first if present.
        await _accept_resolve_dialog(page)
        input_locator = page.locator(SELECTORS["side_panel_input"])
        await human_type(page, input_locator, user_prompt)
        # Brief settle before submit — without it, occasionally the
        # last typed char hasn't propagated to the contenteditable's
        # IME and submission carries an incomplete prompt.
        await jitter_pause(HUMAN_HOVER_PAUSE_S)
        # Click the send button first (more reliable than Enter,
        # which the composer's IME / autocomplete sometimes swallows
        # as a newline — text sits in the input and never submits).
        # Fall back to Enter only if the send-button selector misses,
        # which is the canonical drift-safety path.
        if not await _click_send_button(page):
            await page.keyboard.press("Enter")
        # If this send tripped the "Resolve Gemini suggestions" gate
        # (a prior turn left deck edits pending), the modal is now up and
        # the prompt did NOT go through. Accept All, then re-send.
        if await _accept_resolve_dialog(page):
            if not await _click_send_button(page):
                await page.keyboard.press("Enter")
    except Exception as exc:
        raise SidePanelUnavailable(SELECTOR_DRIFT_MESSAGE) from exc


async def _click_send_button(page: Any) -> bool:
    """Click the side-panel send button. Returns ``True`` if the
    click landed, ``False`` if the selector missed (button not
    visible within a short probe).

    Why not just rely on Enter: the composer's contenteditable can
    interpret Enter as "insert newline" rather than "submit" depending
    on IME state and which suggestion chip the focus drifted to.
    Result: prompt sits typed in the input and never goes to Gemini.
    Clicking the explicit send button sidesteps that whole class.
    """
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


async def _count_responses(page: Any) -> int:
    """How many assistant bubbles are in the panel right now. Captured
    BEFORE submitting so :func:`_read_response` can wait for a NEW one.
    Best-effort: returns 0 if the locator can't count (e.g. a test
    double without ``count()``)."""
    try:
        return int(await page.locator(SELECTORS["side_panel_response"]).count())
    except Exception:
        return 0


async def _read_response(page: Any, timeout_ms: int, prev_count: int = 0) -> str:
    """Read THIS turn's assistant reply, retrying once on a transient timeout.

    Shared read + settle + retry logic lives in
    :mod:`advtest_ext.adapters._ui_mode_common.response_read`.
    """
    return await read_response(page, SELECTORS, timeout_ms, prev_count)


async def _screenshot_side_panel(
    page: Any,
    out_path: Path,
    timeout_ms: int,
) -> None:
    """Screenshot the response container's bounding box, not the full
    page. Keeps the artefact tight for embedding in the replay
    bundle and the PDF report.

    If the bounding box query fails (mid-animation race), fall back
    to a full-page screenshot — we'd rather have a noisier artefact
    than none."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # ``.last`` to match the read path — both helpers focus on
        # the most-recent agent bubble.
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
        # Screenshot failure is non-fatal: the response text was
        # already captured upstream. Surface a placeholder file so
        # the replay bundle still has a slot for the artefact.
        out_path.write_bytes(b"")


async def _capture_failure_artefact(
    page: Any,
    output_dir: Path,
    trace_id: str,
    *,
    cause: BaseException | None = None,
) -> None:
    """Best-effort: dump a full-page screenshot + a text excerpt of
    whatever the page actually showed when ``SidePanelUnavailable``
    was raised.

    Helps the operator distinguish the three failure shapes the
    canonical "selector drift suspected" message conflates:

      - Genuine selector drift (DOM changed; the deck and side panel
        are present but our matchers miss).
      - Auth challenge / "verify it's you" interstitial (most
        likely after rapid Chrome relaunches against the same
        profile — see ``UiModeSession``).
      - Onboarding modal blocking the panel (one-time after a
        Workspace settings change).

    Writes happen under ``<output_dir>/failures/<trace_id>.{png,txt}``.
    Every step is wrapped in its own try/except — failure to capture
    the artefact must NOT mask the original ``SidePanelUnavailable``
    that the caller is about to re-raise.
    """
    failures_dir = output_dir / "failures"
    try:
        failures_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return  # disk problem — give up entirely
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
    # Underlying-cause excerpt: SidePanelUnavailable wraps the
    # original Playwright exception, but its canonical message
    # ("Selector drift suspected; …") hides the actual reason. Pull
    # the cause chain into the artefact so the operator sees whether
    # it was "Page is closed", "Locator wait_for timeout", "Target
    # closed", a navigation error, etc.
    cause_chain: list[str] = []
    cur = cause
    while cur is not None:
        cause_chain.append(f"{type(cur).__name__}: {str(cur)[:500]}")
        cur = cur.__cause__ or cur.__context__
        if len(cause_chain) > 5:  # avoid runaway chains
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
    deck_id: str,
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
    """Drive the Gemini side panel against an existing deck.

    Parameters
    ----------
    deck_id : str
        The id of the deck to open. ``SlidesTarget.render_document``
        already created the deck and recorded the id on its
        ``NativeContext``; the caller passes it through.
    user_prompt : str
        The prompt to type into the side panel.
    trace_id : str
        Filename stem for the screenshot.
    output_dir : Path
        Per-run output directory. Screenshots land under
        ``<output_dir>/screenshots/<trace_id>.png``.
    profile_dir : Path | None
        Override for the persistent profile. Defaults to
        ``$ADVTEST_PLAYWRIGHT_PROFILE_DIR`` or
        ``~/.advtest/playwright-profile``.
    timeout_ms : int | None
        Override for the per-step wait timeout. Defaults to
        ``$ADVTEST_PLAYWRIGHT_TIMEOUT`` or 20_000 ms.
    page_factory : optional
        Async callable returning ``(context, page)`` for the run.
        Production callers leave this as ``None`` and the function
        opens a real Playwright context; unit tests pass a mock
        factory to inject scripted Page / Context doubles without
        Playwright installed. Takes precedence over
        ``external_context`` when both are supplied.
    external_context : optional
        A live Playwright context to reuse across multiple
        invocations within the same run (see :class:`UiModeSession`).
        When provided, the function opens a fresh page on that
        context, drives the flow, and closes JUST the page on
        exit — the context stays open for the next caller.
        Mutually exclusive with ``page_factory`` in practice;
        ``page_factory`` wins if both are non-None.

    Returns
    -------
    Response
        ``text`` is the side panel's reply, ``raw["screenshot_path"]``
        is the absolute path of the PNG.

    Raises
    ------
    SidePanelUnavailable
        Any Playwright timeout, selector miss, or DOM mismatch. The
        message is the canonical drift pointer. When the page object
        exists at the failure point, a forensic screenshot + text
        excerpt is written under ``<output_dir>/failures/`` first.
    """
    profile = profile_dir or resolve_profile_dir()
    timeout = timeout_ms if timeout_ms is not None else resolve_timeout_ms()
    screenshot_path = (output_dir / "screenshots" / f"{trace_id}.png").resolve()
    # Per-trace JSONL capture of Workspace Gemini API traffic. Path
    # is always computed; the file is only populated when
    # interception is enabled (which is the default — see
    # intercept_enabled). When disabled, the path on the returned
    # Response is set to None so the dashboard doesn't render a
    # broken-link panel.
    intercept_path = (output_dir / "intercepted" / f"{trace_id}.jsonl").resolve()
    intercept_on = intercept_enabled()

    # Four dispatch paths, in order of test/production priority:
    # 1. page_factory (test injection) — caller owns lifecycle.
    # 2. external_page (UiModeSession single-page reuse) — caller
    #    owns the page across many invokes; we navigate in place and
    #    close NOTHING on exit. This is the production path for the
    #    live demo: every branch navigates the same Chrome tab so
    #    the audience never sees a blank tab between attempts.
    # 3. external_context (legacy multi-page reuse) — we open a fresh
    #    page on the shared context, close just the page on exit.
    # 4. neither — fresh launch, close everything on exit.
    page: Any = None  # set inside each branch; used by failure capture
    context: Any = None
    owns_context: bool = False
    owns_page: bool = False
    document_body: str = ""
    try:
        if page_factory is not None:
            context, page = await page_factory()
            # Factory owns both — we close nothing.
        elif external_page is not None:
            page = external_page
            # We don't need a context handle for the in-place
            # navigation path; the page already has its own.
        elif external_context is not None:
            context = external_context
            page = await context.new_page()
            owns_page = True  # close the page, not the context
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
                # Skip the navigation + deck-wait when the page is
                # already on the target deck — re-loading the URL
                # closes the Gemini side panel and wipes any
                # in-flight conversation, which kills multi-turn
                # chains (progressive-disclosure crescendo).
                # Compare the URL's /presentation/d/<id> segment for
                # EQUALITY (see _url_is_on_deck): a substring test would
                # wrongly skip navigation when the id appears in a
                # query/fragment or as a prefix of another deck's id,
                # stranding us on the wrong deck.
                current_url = getattr(page, "url", "") or ""
                if not _url_is_on_deck(current_url, deck_id):
                    await page.goto(_deck_url(deck_id), timeout=timeout)
                    # Wait for the deck to actually paint before
                    # doing anything else. Skipping this races the
                    # auto-summary against an empty editor and Gemini
                    # reports "the presentation is empty" — see
                    # _wait_for_deck_ready for the full story.
                    await _wait_for_deck_ready(page)
                # Small post-paint settle: real users glance at the page
                # before doing anything. Helps if Slides renders some
                # late-mounting UI that affects layout.
                await jitter_pause(HUMAN_ACTION_PAUSE_S)
                response_text = await _run_side_panel_flow(
                    page=page,
                    user_prompt=user_prompt,
                    screenshot_path=screenshot_path,
                    timeout_ms=timeout,
                    reset_conversation=reset_conversation,
                )
                # Capture editor body BEFORE teardown. Read from the
                # live DOM via Playwright so signal evaluation can
                # score against what Gemini actually saw — no Slides
                # API side-channel, no scope expansion.
                document_body = await _read_deck_body_text(page)
        except SidePanelUnavailable as panel_exc:
            # Capture forensics before propagating. Best-effort —
            # any failure inside the capture is swallowed. The
            # ``cause`` kwarg lets the artefact include the chained
            # Playwright exception (e.g. "Page is closed", a real
            # wait_for timeout) so the operator can tell which
            # failure mode actually fired.
            try:
                await _capture_failure_artefact(page, output_dir, trace_id, cause=panel_exc)
            except Exception:
                pass
            raise
    finally:
        # Lifecycle teardown. Note: SidePanelUnavailable propagates
        # OUT of this finally because the inner except only catches
        # the capture path.
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

    return Response(
        text=response_text,
        complied=None,
        raw={
            "deck_id": deck_id,
            "screenshot_path": str(screenshot_path),
            "ui_response": ui_response_text,
            "intercepted_traffic_path": str(intercept_path) if intercept_on else None,
            "ui_mode": True,
            "document_body": document_body,
        },
    )


async def _run_side_panel_flow(
    *,
    page: Any,
    user_prompt: str,
    screenshot_path: Path,
    timeout_ms: int,
    reset_conversation: bool = True,
) -> str:
    """Inner sequence shared between the production and test-
    injection paths. Open the panel, optionally reset to a fresh
    conversation, submit the prompt, read the response, screenshot.
    All raise-points convert to ``SidePanelUnavailable`` upstream —
    this function is deliberately a thin orchestrator over the
    helpers.

    ``reset_conversation`` controls whether we click "New
    conversation" before submitting. True (default) lands the
    prompt as turn 1 of a fresh thread — right for one-shot
    attacks (PAIR/TAP) and the first turn of a multi-step
    chain. False keeps the existing conversation so subsequent
    steps land as turn 2, 3, ... — required for progressive-
    disclosure chains where persona / context must accumulate
    across turns."""
    await _open_side_panel(page, timeout_ms)
    # Wait for Google's auto-summary to finish BEFORE attempting
    # the reset. Clicking "New conversation" while the panel is
    # mid-stream leaves it in a half-reset state where the old
    # conversation lingers and the input combobox stays
    # non-actionable.
    await _wait_for_panel_idle(page)
    if reset_conversation:
        # Best-effort: discard the auto-summary by clicking
        # "New conversation". When this lands, our prompt becomes
        # turn 1 of a fresh conversation. When it doesn't (selector
        # drift, button missing), the conversation continues as a
        # follow-up — our reply is still ``.last`` either way.
        await _start_new_conversation(page)
    # Count bubbles BEFORE submit so the read waits for the NEW one
    # (the panel already holds prior turns + the auto-summary).
    prev_count = await _count_responses(page)
    await _submit_prompt(page, user_prompt, timeout_ms)
    response_text = await _read_response(page, timeout_ms, prev_count=prev_count)
    await _screenshot_side_panel(page, screenshot_path, timeout_ms)
    # Demo-hold: keep Gemini's response on screen for a few seconds
    # before returning so the operator / audience can read it before
    # the next attack creates a new deck page and the side panel
    # flashes blank. Configurable via ADVTEST_UI_MODE_HOLD_S; 0
    # disables.
    hold_s = resolve_post_response_hold_s()
    if hold_s > 0:
        await asyncio.sleep(hold_s)
    return response_text


__all__ = [
    "SidePanelUnavailable",
    "ui_mode_invoke",
]
