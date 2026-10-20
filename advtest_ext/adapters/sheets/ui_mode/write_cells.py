"""Mint a Google Sheet and seed its cells via the browser UI — no Sheets API.

The counterpart to ``docs.set_document_body`` for Sheets: a guide's setup can
populate the host sheet with real content so the conversation ("review this
sheet", "summarise the Q3 data") aligns with what's actually on screen, instead
of driving Gemini against a blank grid.

UI-mode, so it needs no ``spreadsheets`` OAuth scope — only the signed-in Chrome
profile the rest of the ui_mode stack uses. Cells are written the way a human
would: select the top-left cell via the Name Box, then type row by row using Tab
(next column) and Enter (next row). Verified against live Sheets.

Lifecycle mirrors ``calendar.ui_mode.create_event`` / ``outlook_ui_send_email``
so the multi-account SessionPool can drive it.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from advtest_ext.adapters._ui_mode_common import (
    SidePanelUnavailable,
    close_context,
    jitter_pause,
    launch_context,
)
from advtest_ext.adapters._ui_mode_common.config import (
    HUMAN_ACTION_PAUSE_S,
    SELECTOR_DRIFT_MESSAGE,
    resolve_profile_dir,
    resolve_timeout_ms,
)

_CREATE_URL = "https://docs.google.com/spreadsheets/create"
_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")
# Name Box (the cell-reference input, top-left of the toolbar). Comma-joined
# fallback chain — LIVE-VALIDATION-REQUIRED if Sheets renames it.
_NAME_BOX = "#t-name-box, input.waffle-name-box, [aria-label='Name box']"


@dataclass(frozen=True)
class SheetSeedResult:
    spreadsheet_id: str
    n_rows: int


def _drift(what: str) -> SidePanelUnavailable:
    return SidePanelUnavailable(f"{what}: {SELECTOR_DRIFT_MESSAGE}")


async def _select_a1(page: Any, cell: str, timeout_ms: int) -> None:
    """Jump to ``cell`` via the Name Box so typing lands at a known origin."""
    nb = page.locator(_NAME_BOX).first
    try:
        await nb.wait_for(state="visible", timeout=timeout_ms)
        await nb.click()
        await nb.fill(cell)
        await page.keyboard.press("Enter")
    except Exception as exc:
        raise _drift("sheets Name Box not found (cannot position cursor)") from exc
    await asyncio.sleep(0.3)


async def _type_rows(page: Any, rows: list[list[str]]) -> None:
    """Type a 2-D grid from the current cell: Tab across, Enter to the next row
    (which returns to the starting column)."""
    for row in rows:
        for i, cell in enumerate(row):
            if cell:
                await page.keyboard.type(str(cell))
            await page.keyboard.press("Tab" if i < len(row) - 1 else "Enter")
        await asyncio.sleep(0.05)


async def sheets_ui_create_and_seed(
    *,
    rows: list[list[str]],
    start_cell: str = "A1",
    profile_dir: Path | None = None,
    timeout_ms: int | None = None,
    page_factory: Any = None,
    external_context: Any = None,
    external_page: Any = None,
) -> SheetSeedResult:
    """Mint a blank Sheet and type ``rows`` into it, starting at ``start_cell``.
    Returns the new spreadsheet id (empty rows still mint an empty sheet). Same
    lifecycle contract as the other ui_mode planters."""
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

        current = getattr(page, "url", "") or ""
        if "/spreadsheets/d/" not in current:
            await page.goto(_CREATE_URL, timeout=timeout)
        # Wait for the create redirect to land on a real spreadsheet id.
        spreadsheet_id = ""
        for _ in range(40):
            m = _SHEET_ID_RE.search(getattr(page, "url", "") or "")
            if m:
                spreadsheet_id = m.group(1)
                break
            await asyncio.sleep(0.3)
        if not spreadsheet_id:
            raise _drift(
                "sheets create did not yield a /spreadsheets/d/<id> URL "
                "(profile not logged into Google?)"
            )
        await jitter_pause(HUMAN_ACTION_PAUSE_S)

        if rows:
            await _select_a1(page, start_cell, timeout)
            await _type_rows(page, rows)
            await asyncio.sleep(1.0)  # let the last edit commit / autosave
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

    return SheetSeedResult(spreadsheet_id=spreadsheet_id, n_rows=len(rows))


__all__ = ["SheetSeedResult", "sheets_ui_create_and_seed"]
