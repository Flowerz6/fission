"""Plant a crafted Calendar event via the calendar.google.com web UI.

The UI-mode counterpart to ``calendar.recipe_ops.create_event``'s old
Calendar-API path. We plant the carrier the same way a human would —
open the full-page event editor, type a title + description (the
description is where a poisoned payload rides), and Save — so the plant
needs NO Calendar API and NO extra OAuth scope. Only the signed-in
Chrome profile the rest of the ui_mode stack already uses.

Modelled on ``outlook.ui_mode.invoker.outlook_ui_send_email``: same
lifecycle contract (``page_factory`` / ``external_page`` /
``external_context`` / fresh-launch) so the multi-account SessionPool
can drive it, and the same "return an echo result" shape.

WARNING — LIVE VALIDATION REQUIRED.
==================================
The event-editor selectors below are seeded from Google Calendar's
known markup but the create/edit surface localises and A/B-tests more
than the shared Gemini panel does. Before a live run, open
``calendar.google.com/calendar/r/eventedit``, DevTools-inspect the
title / description / Save controls, and heal the values here. On any
timeout we raise :class:`SidePanelUnavailable` with the canonical drift
pointer so the failure reads the same as every other ui_mode miss.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    close_context,
    human_click,
    human_type,
    jitter_pause,
    launch_context,
)
from advtest_ext.adapters._ui_mode_common.config import (
    HUMAN_ACTION_PAUSE_S,
    SELECTOR_DRIFT_MESSAGE,
    resolve_profile_dir,
    resolve_timeout_ms,
)

# The full-page event editor. Opening this directly is more robust than
# clicking the "Create" dropdown (which A/B-tests between a menu and a
# quick-create popover). ``u/0`` pins the primary signed-in account.
_EVENT_EDIT_URL = "https://calendar.google.com/calendar/u/0/r/eventedit"

# Where the day-view journey reads from after the plant (echoed for
# downstream template refs; matches the old API handler's output).
_DAY_VIEW_URL = "https://calendar.google.com/calendar/u/0/r/day"

# Event-editor selectors — LIVE-VALIDATION-REQUIRED (see module warning).
# Each is a comma-joined fallback chain: ARIA-name first (survives markup
# churn), then a legacy id, then a broadened case-insensitive match.
_TITLE_SELECTOR = (
    'input[aria-label="Add title"], '
    "input#xTiInp, "
    'input[aria-label*="title" i]'
)
_DESCRIPTION_SELECTOR = (
    '[aria-label="Add description"], '
    "textarea#xDescInp, "
    '[aria-label*="description" i][contenteditable="true"], '
    'textarea[aria-label*="description" i]'
)
_SAVE_SELECTOR = (
    'button[aria-label*="Save" i]:not([disabled]):not([aria-disabled="true"]), '
    'button:has-text("Save")'
)


@dataclass(frozen=True)
class CalendarCreateResult:
    """Echoed plant outputs. ``event_id`` is a synthetic non-empty
    token (not the real Calendar id — the UI path never sees it): the
    downstream ``type_prompt`` step navigates to the day view, never a
    per-event deep link (see ``invoker._calendar_url``), and only needs
    ``event_id`` to be a non-empty string to satisfy its param check."""

    event_id: str
    title: str


def _drift(what: str) -> SidePanelUnavailable:
    return SidePanelUnavailable(f"{what}: {SELECTOR_DRIFT_MESSAGE}")


async def _fill_first_match(page: Any, selector: str, text: str, *, what: str, timeout_ms: int) -> None:
    """Focus + human-type into the first selector in the fallback chain
    that resolves. Raises :class:`SidePanelUnavailable` if none appear."""
    locator = page.locator(selector).first
    try:
        await locator.wait_for(state="visible", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001 — normalise to the drift signal
        raise _drift(f"calendar event-editor {what} field not found") from exc
    await human_type(page, locator, text)


async def calendar_ui_create_event(
    *,
    title: str,
    description: str = "",
    date: str | None = None,  # noqa: ARG001 — accepted for API parity; the
    #     editor defaults to today and the day-view journey reads today.
    profile_dir: Path | None = None,
    timeout_ms: int | None = None,
    page_factory: Any = None,
    external_context: Any = None,
    external_page: Any = None,
) -> CalendarCreateResult:
    """Create one Calendar event via the web UI. Lifecycle matches
    ``outlook_ui_send_email`` so the SessionPool plumbing drives it:

    * ``page_factory`` — test injection of ``(context, page)``; caller
      owns lifecycle.
    * ``external_page`` — reuse a persistent page; navigate in place.
    * ``external_context`` — open a fresh page on a shared context.
    * neither — fresh launch on the profile, close everything.
    """
    profile = profile_dir or resolve_profile_dir()
    timeout = timeout_ms if timeout_ms is not None else resolve_timeout_ms()

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

        # Land on the event editor. Skip the goto when we're already
        # there (idempotent multi-call sessions), same as the Outlook
        # invoker's inbox check.
        current_url = getattr(page, "url", "") or ""
        if "eventedit" not in current_url:
            await page.goto(_EVENT_EDIT_URL, timeout=timeout)
        await jitter_pause(HUMAN_ACTION_PAUSE_S)

        await _fill_first_match(
            page, _TITLE_SELECTOR, title, what="title", timeout_ms=timeout
        )
        if description:
            await _fill_first_match(
                page,
                _DESCRIPTION_SELECTOR,
                description,
                what="description",
                timeout_ms=timeout,
            )

        save = page.locator(_SAVE_SELECTOR).first
        try:
            await save.wait_for(state="visible", timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            raise _drift("calendar event-editor Save button not found") from exc
        await human_click(save)

        # Save navigates back to the grid. Wait for the editor URL to
        # fall away as the confirmation the plant committed.
        try:
            await page.wait_for_url(
                lambda url: "eventedit" not in url, timeout=timeout
            )
        except Exception:
            # Non-fatal: some layouts keep the URL and only toast. The
            # event is created either way; a stale editor URL must not
            # fail an otherwise-good plant.
            pass
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

    return CalendarCreateResult(event_id=f"ui-{abs(hash(title)) % (10**12):012d}", title=title)


__all__ = ["CalendarCreateResult", "calendar_ui_create_event"]
