"""Shared Playwright pacing + interaction primitives.

These four helpers are product-agnostic — they take a Playwright
locator or page and do "looks like a human" pacing on top of the
Playwright API. Identical across gmail and google_docs invokers
before this module existed; centralised here so future products
(Sheets, Calendar, Slides) get them for free, and so a tweak (e.g.
a different anti-bot heuristic) lands in one place.

All four respect the pacing constants exported by
:mod:`advtest_ext.adapters._ui_mode_common.config`
(``HUMAN_HOVER_PAUSE_S``, ``HUMAN_TYPING_DELAY_MS``,
``BUTTON_READY_POLL_S``).
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from advtest_ext.adapters._ui_mode_common.config import (
    BUTTON_READY_POLL_S,
    HUMAN_HOVER_PAUSE_S,
    HUMAN_TYPING_DELAY_MS,
)


async def jitter_pause(bounds_s: tuple[float, float]) -> None:
    """Sleep for ``random.uniform(*bounds_s)`` seconds.

    Centralised helper so tests can monkey-patch a zero-delay
    variant. Two reasons to want jittered pauses on top of the
    Playwright-level ``slow_mo``:

    1. ``slow_mo`` is a constant inserted between every Playwright
       command; it spaces out automation but makes the *cadence*
       perfectly regular, which is itself a tell.
    2. Some high-level steps (post-goto settle, post-panel-open
       look-around) need longer pauses than slow_mo can give
       without sluggifying every micro-action.
    """
    await asyncio.sleep(random.uniform(*bounds_s))


async def human_click(locator: Any, *, force: bool = True) -> None:
    """Hover, brief pause, then click. Real humans don't teleport
    the cursor onto a target and click instantly — hovering fires
    the ``pointerenter`` / ``mouseenter`` events that the page's
    JS can observe before the click. Some bot detectors flag the
    absence of those events. ``force=True`` is retained because
    Playwright's actionability check still false-negatives on
    Google's custom buttons."""
    try:
        await locator.hover()
    except Exception:
        # Hover failure (off-screen, transient layout shift) is
        # cosmetic — fall through and click anyway. The click
        # itself enforces visibility / actionability.
        pass
    await jitter_pause(HUMAN_HOVER_PAUSE_S)
    await locator.click(force=force)


async def human_type(page: Any, input_locator: Any, text: str) -> None:
    """Focus the side-panel input then type ``text`` with a
    randomised per-character delay.

    Why this split:

      - ``input_locator.focus()`` is the **programmatic focus**
        path. It doesn't dispatch pointer events (no click
        handlers fire) and doesn't do Playwright's full
        actionability dance (visible/stable/receives-events).
        Critical for the Gemini side-panel input, which is a
        ``role="combobox"`` with dynamic ``aria-expanded`` /
        suggestion state — ``locator.type`` waits for stability
        that never arrives and times out at 30s.
      - ``page.keyboard.type`` then sends keystrokes to whatever
        has focus right now. With the explicit focus above,
        that's our combobox.

    Earlier iterations tried two other shapes:

      1. ``human_click(input_locator)`` + ``page.keyboard.type``.
         Failed because clicking the contenteditable can land on
         a quick-suggestion chip / starter prompt overlay → focus
         steals away → keystrokes go nowhere.
      2. ``input_locator.type(text, delay=…)``. Cleanest API but
         times out on the combobox actionability check, since the
         element keeps mutating as the panel UI re-renders.

    Variable delay across rounds (different ``random.uniform``
    draw each call); fixed delay within one typing burst."""
    await input_locator.focus()
    # Insert embedded newlines as Shift+Enter, never a raw "\n": keyboard.type
    # sends "\n" as an Enter keypress, and the Gemini side-panel composer treats
    # Enter as SUBMIT. Typing a multi-line prompt directly therefore fires one
    # message PER LINE, each canceling the previous in-flight request (the
    # "Canceled request" fragmentation). Type each line, soft-newline between
    # them, and let the caller submit the whole message once (send button).
    delay = random.uniform(*HUMAN_TYPING_DELAY_MS)
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if line:
            await page.keyboard.type(line, delay=delay)
        if i < len(lines) - 1:
            await page.keyboard.press("Shift+Enter")


async def wait_for_button_ready(locator: Any, timeout_ms: int) -> None:
    """Wait for the toolbar entry button to be visible AND not
    ``aria-disabled``.

    Playwright's ``wait_for(state="visible")`` only observes
    visibility, not the disabled state. Workspace surfaces
    occasionally grey the Gemini entry-point during page
    hydration; clicking while ``aria-disabled="true"`` is a no-op
    and is exactly the failure mode this helper guards against.
    We poll the attribute after visibility resolves rather than
    relying on Playwright's actionability check, because the
    caller follows up with a ``force=True`` click that bypasses
    actionability."""
    await locator.wait_for(state="visible", timeout=timeout_ms)
    deadline_s = timeout_ms / 1000
    elapsed_s = 0.0
    while elapsed_s < deadline_s:
        if await locator.get_attribute("aria-disabled") != "true":
            return
        await asyncio.sleep(BUTTON_READY_POLL_S)
        elapsed_s += BUTTON_READY_POLL_S
    raise TimeoutError("side-panel entry button stayed aria-disabled past the deadline")
