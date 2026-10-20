"""Shared side-panel response reading for the Workspace ui_mode invokers.

The google_docs / sheets / calendar / slides invokers each drive the SAME
Gemini side-panel component, so they all read the assistant's reply the same
way: wait past the loading indicator, confirm THIS turn's new bubble, then
settle-poll the (streamed, multi-block) reply to its final text. That logic
was copy-pasted into each adapter; it lives here once now.

What's intentionally NOT shared:
  * Gmail reads via a bespoke ``page.evaluate`` over Gmail-specific DOM
    (``jsname="t1Rade"`` spans), so it keeps its own reader.
  * Sheets/Calendar append clickable *choice chips* to the reply and raise a
    surface-specific empty message — they call :func:`read_response` for the
    text and layer their extras on top.
"""

from __future__ import annotations

import asyncio
from typing import Any

from advtest_ext.adapters._ui_mode_common.config import LOADING_VISIBLE_PROBE_MS
from advtest_ext.adapters._ui_mode_common.errors import SidePanelUnavailable

# Settle-poll bounds for the streamed reply: poll ``read_new_blocks_settled``
# until the joined text stops growing for ``_SETTLE_STABLE_ROUNDS`` consecutive
# reads (or ``_SETTLE_MAX_ROUNDS`` is hit). Tests patch these on THIS module.
_SETTLE_POLL_S = 0.6
_SETTLE_STABLE_ROUNDS = 2
_SETTLE_MAX_ROUNDS = 40  # ~24s ceiling for very long dumps

# One retry of the response read, with a longer window, before giving up: a
# read timeout is far more often a slow / throttled panel than a DOM rename.
_READ_RETRY_TIMEOUT_MULT = 1.5
_READ_TIMEOUT_MESSAGE = (
    # Keeps the "Selector drift suspected" phrase (the SidePanelUnavailable
    # contract other callers/tests key on) but states the likelier cause first.
    "Selector drift suspected: timed out reading the side-panel response even "
    "after a retry. This is most often a transient slow / throttled panel; if it "
    "persists, the response selector has drifted — the panel DOM is captured in "
    "<output_dir>/failures/, and scripts/dump_side_panel_dom.py re-checks it live."
)


async def join_new_blocks(page: Any, sel: str, prev_count: int) -> str:
    """Concatenate every NEW agent bubble block (index >= prev_count).

    A structured reply (headings + lists) mounts as several bubble-content
    elements; reading just one truncates it. We read each new block's
    inner_text and join. When the element count can't be read (a test
    double) or hasn't grown, fall back to the single new bubble so the
    original behaviour is preserved."""
    loc = page.locator(sel)
    try:
        count = int(await loc.count())
    except Exception:
        count = 0
    if count <= prev_count:
        # Construct the locator INSIDE the try so a test double whose
        # ``.nth``/``.last`` raises falls through to the next candidate
        # rather than escaping.
        for use_last in (False, True):
            try:
                locator = loc.last if use_last else loc.nth(prev_count)
                return (await locator.inner_text()) or ""
            except Exception:
                continue
        return ""
    parts: list[str] = []
    for i in range(prev_count, count):
        try:
            block = await loc.nth(i).inner_text()
        except Exception:
            continue
        if block:
            parts.append(block)
    return "\n\n".join(parts).strip()


async def read_new_blocks_settled(page: Any, sel: str, prev_count: int) -> str:
    """Poll :func:`join_new_blocks` until the joined text stops growing
    for ``_SETTLE_STABLE_ROUNDS`` consecutive reads (or the ceiling),
    so we persist the FULL streamed reply rather than a partial render."""
    last = ""
    stable = 0
    for _ in range(_SETTLE_MAX_ROUNDS):
        current = await join_new_blocks(page, sel, prev_count)
        if current == last and current:
            stable += 1
            if stable >= _SETTLE_STABLE_ROUNDS:
                return current
        else:
            stable = 0
            last = current
        await asyncio.sleep(_SETTLE_POLL_S)
    return last


async def _read_response_once(
    page: Any,
    selectors: Any,
    timeout_ms: int,
    prev_count: int,
    *,
    fallback_last: bool,
) -> str:
    """One attempt at reading THIS turn's reply. Raises on any wait/read
    failure so :func:`read_response` can decide whether to retry.

    ``fallback_last`` controls whether a missing new-bubble index falls back
    to ``.last``. Docs/Slides want that (it tolerates count quirks); Sheets/
    Calendar pass ``False`` so a stale previous bubble is never read.
    """
    loading = page.locator(selectors["side_panel_loading_indicator"])
    try:
        await loading.wait_for(state="visible", timeout=LOADING_VISIBLE_PROBE_MS)
    except Exception:
        # Indicator never showed — fast / synchronous reply.
        pass
    else:
        await loading.wait_for(state="hidden", timeout=timeout_ms)
    sel = selectors["side_panel_response"]
    # Confirm THIS turn's response exists (a new bubble at prev_count).
    try:
        await page.locator(sel).nth(prev_count).wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        if not fallback_last:
            raise
        await page.locator(sel).last.wait_for(state="visible", timeout=timeout_ms)
    # A long structured reply renders as MULTIPLE bubble blocks, and the
    # loading indicator can hide before they finish — so read ALL new blocks
    # (index >= prev_count) and poll until the joined text stops growing.
    return await read_new_blocks_settled(page, sel, prev_count)


async def read_response(
    page: Any,
    selectors: Any,
    timeout_ms: int,
    prev_count: int = 0,
    *,
    fallback_last: bool = True,
) -> str:
    """Read THIS turn's reply from the Gemini side panel, retrying once.

    A read timeout here is far more often a slow / throttled panel than an
    actual DOM rename — the response is still rendering, or an "unusual
    activity" throttle slowed it past the first window. One longer retry
    recovers that transient case; only if THIS also fails do we treat it as
    drift (the panel DOM is captured in the failure artefact for re-pinning).
    """
    try:
        text = await _read_response_once(
            page, selectors, timeout_ms, prev_count, fallback_last=fallback_last
        )
    except Exception:
        try:
            text = await _read_response_once(
                page,
                selectors,
                int(timeout_ms * _READ_RETRY_TIMEOUT_MULT),
                prev_count,
                fallback_last=fallback_last,
            )
        except Exception as exc:
            raise SidePanelUnavailable(_READ_TIMEOUT_MESSAGE) from exc
    return text or ""
